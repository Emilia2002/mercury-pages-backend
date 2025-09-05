import asyncio
import logging
import traceback
from datetime import datetime, timezone
import stripe
import os
import httpx
import json
from urllib.parse import urljoin, urlparse
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, status, Request, Body
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel
from bson import ObjectId

import app.database as database
import app.schemas as schemas
import app.auth as auth
import app.ai_generator as ai_generator

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info("--- Starting main.py initialization ---")

try:
    # Load environment variables
    env_path = Path(__file__).resolve().parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
    logger.info("✅ .env file loaded.")

    app = FastAPI(title="Mercury Pages API")


    # --- Helper Functions ---
    def serialize_doc(doc):
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc


    def serialize_docs(docs):
        return [serialize_doc(doc) for doc in docs]


    def flatten_variants(variants_raw) -> list[str]:
        """Normalize any variant format into a flat list of strings."""
        if isinstance(variants_raw, dict):
            # dict format: {"Size": ["S", "M"]}
            return [f"{name}: {opt}" for name, opts in variants_raw.items() for opt in opts]

        elif isinstance(variants_raw, list):
            result = []
            for item in variants_raw:
                if isinstance(item, dict):
                    # list of dicts: [{"variant_name": "Size", "options": ["S","M"]}]
                    name = item.get("variant_name") or item.get("name") or "Option"
                    for opt in item.get("options", []):
                        result.append(f"{name}: {opt}")
                elif isinstance(item, str):
                    result.append(item)
            return result

        elif isinstance(variants_raw, str):
            return [variants_raw]

        return []


    def extract_images(soup: BeautifulSoup, base_url: str) -> list[str]:
        images = set()

        for img in soup.find_all('img'):
            for attr in ["src", "data-src", "data-image", "data-lazy-src", "data-original", "srcset"]:
                val = img.get(attr)
                if val:
                    # Handle srcset (take the first URL)
                    if " " in val and "http" in val:
                        val = val.split(" ")[0]
                    absolute_url = urljoin(base_url, val)
                    if urlparse(absolute_url).scheme in ["http", "https"]:
                        images.add(absolute_url)

        # 2. AliExpress JSON blob (product gallery often stored here)
        for script in soup.find_all("script"):
            if script.string and "window.runParams" in script.string:
                try:
                    import re, json
                    match = re.search(r'window\.runParams\s*=\s*(\{.*\});', script.string, re.DOTALL)
                    if match:
                        data = json.loads(match.group(1))
                        # AliExpress stores product images under multiple possible keys
                        gallery = data.get("data", {}).get("imageModule", {}).get("imagePathList", [])
                        if gallery:
                            for img_url in gallery:
                                images.add(img_url)
                except Exception:
                    pass

        return list(images)


    def normalize_variants(variants_raw):
        """
        Converts variants into Dict[str, List[str]] format.
        Handles cases where database stores list of dicts.
        """
        if isinstance(variants_raw, dict):
            normalized = {}
            for key, values in variants_raw.items():
                # flatten dicts in the list into strings
                str_values = []
                for v in values:
                    if isinstance(v, dict):
                        # pick first value in dict
                        str_values.append(next(iter(v.values())))
                    elif isinstance(v, str):
                        str_values.append(v)
                normalized[key] = str_values
            return normalized
        elif isinstance(variants_raw, list):
            normalized = {}
            for item in variants_raw:
                if isinstance(item, dict):
                    name = item.get("variant_name") or item.get("name") or "Option"
                    options = item.get("options") or []
                    normalized[name] = [str(opt) for opt in options]
            return normalized
        return {"default": ["Default"]}


    def extract_product_data(html_content: str, base_url: str) -> dict:

        soup = BeautifulSoup(html_content, 'html.parser')
        data = {
            "original_title": "Untitled Product",
            "images": [],
            "variants": {"default": ["Default"]},
            "price": "N/A",
            "category": "Uncategorized",
            "availability": True,
        }

        data["images"] = extract_images(soup, base_url)

        # 2. Extract Title (look for meta tags or h1)
        title_tag = soup.find('meta', property='og:title')
        if not title_tag:
            title_tag = soup.find('h1')
        if title_tag and title_tag.get('content'):
            data["original_title"] = title_tag.get('content').strip()
        elif title_tag and title_tag.string:
            data["original_title"] = title_tag.string.strip()

        # 3. Extract Price (look for meta tags or common classes)
        price_tag = soup.find('meta', property='og:price:amount')
        if not price_tag:
            price_tag = soup.find(class_=lambda x: x and ('price' in x.lower() or 'current-price' in x.lower()))
        if price_tag and price_tag.get('content'):
            data["price"] = price_tag.get('content').strip()
        elif price_tag and price_tag.string:
            data["price"] = price_tag.string.strip()

        # 4. Extract Variants (heuristic, very site-specific)
        variants = {}
        for select_tag in soup.find_all('select'):
            option_name = select_tag.get('aria-label') or select_tag.get('name') or 'Option'
            options = [opt.string.strip() for opt in select_tag.find_all('option') if opt.string]
            if options:
                variants[option_name] = options
        if variants:
            data['variants'] = variants

        # 5. Extract Availability (heuristic, by text content)
        availability_text = soup.find(text=lambda x: x and ("out of stock" in x.lower() or "indisponibil" in x.lower()))
        if availability_text:
            data['availability'] = False

        # 6. Extract Category (heuristic, from breadcrumbs)
        breadcrumbs = soup.find(class_=lambda x: x and ('breadcrumbs' in x.lower() or 'breadcrumb' in x.lower()))
        if breadcrumbs:
            category_links = breadcrumbs.find_all('a')
            if category_links:
                data['category'] = category_links[-1].text.strip()

        return data


    # --- Pydantic Models ---
    class ShopifyCredentials(BaseModel):
        store_url: str
        access_token: str


    class AIExtractRequest(BaseModel):
        source_url: str


    class AIExtractResponse(BaseModel):
        original_title: str
        new_title: str
        description: str
        images: list[str]
        variants: list[str]
        price: str
        category: str
        benefits_list: list[str]  # Added to match the returned data
        availability: bool


    # --- Stripe & Plan Config ---
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    YOUR_DOMAIN = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
    BROWSER_API_WSS = os.environ.get("BROWSER_API_WSS")
    SCRAPE_DO_API = os.environ.get("SCRAPE_DO_API")
    logger.info("✅ Stripe config loaded.")

    PRICE_IDS = {
        "craftsman": os.environ.get("STRIPE_CRAFTSMAN_PRICE_ID"),
        "artisan": os.environ.get("STRIPE_ARTISAN_PRICE_ID"),
        "inventor": os.environ.get("STRIPE_INVENTOR_PRICE_ID"),
    }

    PLAN_LIMITS = {
        "free": {"pages_per_month": 5},
        "craftsman": {"pages_per_month": 30},
        "artisan": {"pages_per_month": None},
        "inventor": {"pages_per_month": None},
    }

    BRAND_VOICE_LIMIT = 1
    logger.info("✅ Plan limits loaded.")

    # --- CORS Middleware ---
    origins = [
        "http://localhost:3000",
        "https://mercury-pages-frontend.vercel.app",
        "https://mercurypages.ai",
        "https://www.mercurypages.ai"
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info(f"✅ CORS configured for origins: {origins}")


    # --- Endpoints ---
    @app.get("/")
    def read_root():
        logger.info("GET / route called")
        return {"message": "Server is running!"}


    @app.post("/register", response_model=schemas.UserInDB)
    def register_user(user: schemas.UserCreate):
        logger.info(f"POST /register called for email: {user.email}")
        try:
            existing_user = database.get_user_by_email(user.email)
            if existing_user:
                raise HTTPException(status_code=400, detail="Email already registered")

            user_data = {
                "first_name": user.first_name, "email": user.email,
                "hashed_password": auth.get_password_hash(user.password),
                "subscription_plan": "free", "subscription_active": False,
                "pages_generated_month": 0, "current_month": datetime.now(timezone.utc).strftime("%Y-%m"),
                "brand_voice_used": 0, "stripe_customer_id": None, "stripe_subscription_id": None,
            }

            result = database.create_user(user_data)
            if not result:
                raise HTTPException(status_code=500, detail="Could not create user.")

            created_user = database.get_user_by_email(user.email)
            return serialize_doc(created_user)
        except Exception as e:
            logger.error(f"Error in /register: {e}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="Internal server error during registration.")


    @app.post("/token", response_model=schemas.Token)
    def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
        logger.info(f"POST /token called for username: {form_data.username}")
        try:
            user = database.get_user_by_email(form_data.username)
            if not user or not auth.verify_password(form_data.password, user.get('hashed_password')):
                logger.warning(f"Failed login attempt for username: {form_data.username}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect email or password",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            logger.info(f"User {form_data.username} authenticated successfully.")
            access_token = auth.create_access_token(data={"sub": user['email']})
            return {"access_token": access_token, "token_type": "bearer"}
        except Exception as e:
            logger.error(f"CRITICAL ERROR in /token endpoint: {e}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="An internal server error occurred during login.")


    @app.post("/create-checkout-session")
    def create_checkout_session(checkout_request: schemas.CheckoutRequest,
                                current_user: dict = Depends(auth.get_current_user)):
        """Creates a Stripe Checkout session for a subscription."""
        price_id = PRICE_IDS.get(checkout_request.plan_name)
        if not price_id:
            raise HTTPException(status_code=400, detail="Invalid plan name")
        try:
            session = stripe.checkout.Session.create(
                line_items=[{'price': price_id, 'quantity': 1}],
                mode='subscription',
                success_url=f"{YOUR_DOMAIN}/dashboard?success=true",
                cancel_url=f"{YOUR_DOMAIN}/pricing?canceled=true",
                customer_email=current_user.get("email"),
                metadata={"user_email": current_user.get("email"), "plan_name": checkout_request.plan_name}
            )
            return {"url": session.url}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


    @app.post("/stripe-webhook")
    async def stripe_webhook(request: Request):
        """Listens for successful payment events from Stripe."""
        payload = await request.body()
        sig_header = request.headers.get('Stripe-Signature')
        if not sig_header:
            raise HTTPException(status_code=400, detail="Stripe-Signature header not found.")
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            email = session.get('metadata', {}).get('user_email')
            plan = session.get('metadata', {}).get('plan_name')
            stripe_customer_id = session.get('customer')
            stripe_subscription_id = session.get('subscription')
            if email and plan and stripe_customer_id and stripe_subscription_id:
                database.update_user_subscription(email, stripe_customer_id, stripe_subscription_id, plan)
        return {"status": "success"}


    @app.post("/generate-page/", status_code=202)
    def generate_page(request: schemas.ScrapeRequest, current_user: dict = Depends(auth.get_current_user)):
        """Creates a scraping job for an authenticated user after checking their plan limits."""
        email = current_user.get("email")
        user = database.get_user_by_email(email)
        logger.info(f"User authenticated successfully.")
        if not user:
            raise HTTPException(status_code=403, detail="User not found")

        plan = user.get("subscription_plan", "free").lower()
        logger.info(f"User plan: {plan}")

        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        logger.info(f"User limits: {limits}")

        current_month_str = datetime.now(timezone.utc).strftime("%Y-%m")
        if user.get("current_month") != current_month_str:
            database.reset_monthly_usage(email, current_month_str)
            user = database.get_user_by_email(email)

        if limits["pages_per_month"] is not None and user.get("pages_generated_month", 0) >= limits["pages_per_month"]:
            raise HTTPException(status_code=403,
                                detail="Monthly page generation limit reached. Please upgrade your plan.")

        job_id = database.create_scraping_job(str(request.source_url), email)
        database.increment_user_monthly_usage(email)
        return {"message": "Scraping job created", "job_id": job_id}


    @app.post("/ai-extract-data", response_model=AIExtractResponse)
    async def extract_data_with_ai(request: AIExtractRequest, current_user: dict = Depends(auth.get_current_user)):
        email = current_user.get("email")
        # Step 0: create a job in 'pending' state
        job_id = database.create_scraping_job(request.source_url, email)

        try:
            url = request.source_url
            logger.info(f"Job {job_id}: Extracting data from URL: {url}")

            # Step 1: Clean the URL and scrape content
            parsed_url = urlparse(url)
            clean_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

            async with httpx.AsyncClient() as client:
                scrape_do_url = f"https://api.scrape.do/?url={clean_url}&token={SCRAPE_DO_API}&render=true"
                logger.info(f"Job {job_id}: Making Scrape.do API call to {scrape_do_url}")
                scrape_resp = await client.get(scrape_do_url, timeout=60.0)
                scrape_resp.raise_for_status()

                # Extract all data from the HTML
                extracted_data = extract_product_data(scrape_resp.text, clean_url)

            logger.info(f"Job {job_id}: Product data extraction successful.")

            # Step 2: OpenAI copywriting
            openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            openai_prompt = f"""
            You are an expert Shopify product page generator.
            Take the raw product data and generate a **Shopify-ready product object**.

            Tasks:
            1. Rewrite the product title into something unique, attractive, and keyword-friendly.
            2. Write a persuasive long-form product description (with emojis if relevant).
            3. Create a short **benefits list** in bullet points.
            4. If the category is missing or 'Uncategorized', infer the best category (e.g. 'Electronics', 'Fashion', 'Home & Kitchen', 'Sports', etc.).
            5. If variants are missing or only 'default', analyze the data and guess meaningful variants (size, color, material, style, etc.).
            6. Always return **valid JSON**.

            Raw extracted data:
            {json.dumps(extracted_data, indent=2)}

            Return JSON with keys:
            - new_title
            - description
            - benefits_list
            - category
            - variants
            """

            openai_resp = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                    {"role": "user", "content": openai_prompt}
                ]
            )
            openai_data = json.loads(openai_resp.choices[0].message.content)
            logger.info(f"Job {job_id}: OpenAI generation successful")

            final_response_data = {
                "original_title": extracted_data.get("original_title", "Untitled Product"),
                "new_title": openai_data.get("new_title", "Untitled Product"),
                "description": openai_data.get("description", ""),
                "images": extracted_data.get("images", []),
                "variants": flatten_variants(openai_data.get("variants") or extracted_data.get("variants", {})),
                "price": extracted_data.get("price", "N/A"),
                "category": openai_data.get("category", "Uncategorized"),
                "benefits_list": openai_data.get("benefits_list", []),
                "availability": extracted_data.get("availability", False),
            }

            database.update_job_with_result(ObjectId(job_id), final_response_data)
            logger.info(f"Job {job_id}: Completed successfully")

            return final_response_data

        except Exception as e:
            logger.error(f"Job {job_id}: Failed with error: {e}")
            database.update_job_with_error(ObjectId(job_id), str(e))
            raise HTTPException(status_code=500, detail=f"AI extraction failed: {str(e)}")


    @app.get("/jobs/{job_id}", response_model=schemas.Job)
    def get_job_status(job_id: str, current_user: dict = Depends(auth.get_current_user)):
        """Retrieves the status and result of a single job."""
        job = database.get_job_by_id(job_id)
        if not job or job.get("user_email") != current_user.get("email"):
            raise HTTPException(status_code=404, detail="Job not found")
        return job


    @app.get("/jobs", response_model=list[schemas.Job])
    def list_jobs(current_user: dict = Depends(auth.get_current_user)):
        """Lists all jobs for the current user."""
        jobs = database.get_jobs_by_user(current_user["email"])
        return serialize_docs(jobs)


    @app.get("/me", response_model=schemas.UserInfo)
    def get_current_user_info(current_user: dict = Depends(auth.get_current_user)):
        """Retrieves detailed information about the currently logged-in user."""
        user = database.get_user_by_email(current_user["email"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        plan = user.get("subscription_plan", "free").lower()
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

        return {
            "email": user["email"],
            "first_name": user.get("first_name"),
            "subscription_plan": plan,
            "subscription_active": user.get("subscription_active", False),
            "pages_limit_month": limits["pages_per_month"],
            "pages_generated_month": user.get("pages_generated_month", 0),
            "shopify_store_url": user.get("shopify_store_url"),
            "shopify_access_token": user.get("shopify_access_token")
        }


    @app.post("/brand-voice/analyze", response_model=schemas.BrandVoiceAnalyzeResponse)
    def analyze_brand_voice(request: schemas.BrandVoiceAnalyzeRequest,
                            current_user: dict = Depends(auth.get_current_user)):
        email = current_user.get("email")
        user = database.get_user_by_email(email)
        if user.get("subscription_plan", "free") == "free" and user.get("brand_voice_used", 0) >= BRAND_VOICE_LIMIT:
            raise HTTPException(status_code=403, detail="You have used your free brand voice analysis. Please upgrade.")

        database.increment_brand_voice_usage(email)
        analysis_result = ai_generator.analyze_brand_description(request.description)
        if not analysis_result:
            raise HTTPException(status_code=500, detail="AI analysis failed.")
        return analysis_result


    @app.post("/users/me/brand-profile")
    def save_brand_profile(profile: schemas.BrandProfileUpdate, current_user: dict = Depends(auth.get_current_user)):
        """Saves the complete brand profile for the authenticated user."""
        user_email = current_user.get("email")
        profile_data = profile.model_dump()
        database.update_user_brand_profile(user_email, profile_data)
        return {"status": "success", "message": "Brand profile saved successfully."}


    @app.post("/create-customer-portal-session")
    def create_customer_portal_session(current_user: dict = Depends(auth.get_current_user)):
        """Creates a Stripe Customer Portal session URL for the user."""
        user = database.get_user_by_email(current_user["email"])
        if not user or not user.get("stripe_customer_id"):
            raise HTTPException(status_code=400, detail="Stripe customer ID not found.")
        try:
            session = stripe.billing_portal.Session.create(
                customer=user["stripe_customer_id"],
                return_url=f"{YOUR_DOMAIN}/account",
            )
            return {"url": session.url}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


    @app.post("/cancel-subscription")
    def cancel_subscription(current_user: dict = Depends(auth.get_current_user)):
        """Cancels a user's Stripe subscription."""
        user = database.get_user_by_email(current_user["email"])
        if not user or not user.get("stripe_subscription_id"):
            raise HTTPException(status_code=400, detail="Stripe subscription ID not found.")
        try:
            stripe.Subscription.cancel(user["stripe_subscription_id"])
            database.update_user_subscription_status(user["email"], "free", False)
            return {"status": "success", "message": "Subscription cancelled successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


    def generate_shopify_variants(variants_dict: Dict[str, List[str]], base_price: str):
        """Generates Shopify-compatible options and variants from a dictionary.
        Limits the total number of variants to 250 and options to 3."""
        options = []
        variants = []
        option_names = list(variants_dict.keys())

        # Shopify has a hard limit of 250 variants per product
        max_variants = 250
        # And a hard limit of 3 options
        max_options = 3

        if len(option_names) > max_options:
            logger.warning(f"Warning: Truncating variants to {max_options} to comply with Shopify's limit.")
            option_names = option_names[:max_options]
            # Rebuild a truncated variants_dict for the next step
            variants_dict = {name: variants_dict[name] for name in option_names}

        # If there are no variants or a single variant with no options, create a single default one
        if not option_names:
            variants.append({"price": base_price, "sku": "default-sku", "option1": "Default Title"})
            return options, variants

        # Create the 'options' array
        for name, values in variants_dict.items():
            options.append({"name": name, "values": values})

        # Generate the Cartesian product of all variant values
        def generate_combinations(keys, index, current_combination):
            if len(variants) >= max_variants:
                return

            if index == len(keys):
                variant_object = {
                    "price": base_price,
                    "sku": f"mercury-{'-'.join(current_combination.values())}"
                }
                for i, value in enumerate(current_combination.values()):
                    variant_object[f"option{i+1}"] = value
                variants.append(variant_object)
                return

            current_key = keys[index]
            for value in variants_dict[current_key]:
                if len(variants) >= max_variants:
                    break
                new_combination = current_combination.copy()
                new_combination[f"option{index + 1}"] = value
                generate_combinations(keys, index + 1, new_combination)

        generate_combinations(option_names, 0, {})

        return options, variants


    async def _perform_shopify_export(job_id: str, user: dict, product_data: schemas.ProductData):
        """
        The actual background task that handles the Shopify export.
        """
        store_url = user.get("shopify_store_url")
        access_token = user.get("shopify_access_token")

        if not store_url or not access_token:
            database.update_job_with_error(job_id, "Shopify credentials not set.")
            return

        # Sanitize the price field
        try:
            # Remove any non-digit, non-decimal-point characters
            clean_price_str = ''.join(c for c in product_data.price if c.isdigit() or c == '.')
            # Convert to float and back to a 2-decimal string
            sanitized_price = f"{float(clean_price_str):.2f}"
        except (ValueError, TypeError):
            logger.warning(f"Job {job_id}: Invalid price '{product_data.price}'. Defaulting to 0.00.")
            sanitized_price = "0.00"

        options, variants = generate_shopify_variants(product_data.variants, sanitized_price)

        if len(variants) > 250:
            logger.error(f"Job {job_id}: Exceeded Shopify's 250 variant limit. Generated {len(variants)} variants.")
            database.update_job_with_error(job_id,
                                           "Generated too many variants. Shopify limits products to 250 variants.")
            return

        payload = {
            "product": {
                "title": product_data.new_title or product_data.title or "Untitled",
                "body_html": product_data.description or "",
                "options": options,
                "variants": variants,
                "images": [{"src": img} for img in (product_data.images or [])]
            }
        }

        # Log the final payload for debugging
        logger.info(f"Job {job_id}: Final payload for Shopify API:")
        logger.info(json.dumps(payload, indent=2))
        logger.info("--- End Payload Log ---")

        try:
            async with httpx.AsyncClient() as client:
                url = f"https://{store_url}/admin/api/{os.environ.get('SHOPIFY_API_VERSION', '2023-07')}/products.json"
                headers = {
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": access_token
                }
                resp = await client.post(url, headers=headers, json=payload, timeout=120.0)
                resp.raise_for_status()

            database.update_job_with_result(ObjectId(job_id), {"status": "completed", "product": resp.json()})
            logger.info(f"Job {job_id}: Successfully exported product to Shopify.")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error during Shopify export for job {job_id}: {e.response.text}")
            database.update_job_with_error(ObjectId(job_id), f"Shopify API error: {e.response.text}")
        except httpx.TimeoutException:
            logger.error(f"Job {job_id}: Timeout during Shopify export.")
            database.update_job_with_error(ObjectId(job_id), "Shopify export timed out.")
        except Exception as e:
            logger.error(f"Job {job_id}: Unexpected error during Shopify export: {e}")
            database.update_job_with_error(ObjectId(job_id), f"An internal server error occurred during export: {str(e)}")


    @app.post("/shopify/export", status_code=status.HTTP_202_ACCEPTED)
    async def export_to_shopify(
            product: schemas.ProductData,
            current_user: dict = Depends(auth.get_current_user)
    ):
        logger.info("Starting new Shopify export job.")
        user = database.get_user_by_email(current_user["email"])
        store_url = user.get("shopify_store_url")
        access_token = user.get("shopify_access_token")

        if not store_url or not access_token:
            raise HTTPException(
                status_code=400,
                detail="Shopify credentials not set. Please complete onboarding."
            )

        # Create a new job in the database and return the job ID immediately
        job_id = database.create_shopify_export_job(current_user["email"], product.model_dump())

        # Start the background task
        asyncio.create_task(_perform_shopify_export(job_id, user, product))

        return {"message": "Shopify export job started", "job_id": job_id}


    logger.info("🟢 FastAPI app initialization complete. Server is ready.")


except Exception as e:
    logger.error("❌ CRITICAL STARTUP ERROR:")
    logger.error(traceback.format_exc())
    raise e
