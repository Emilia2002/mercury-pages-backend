import logging
import traceback
from datetime import datetime, timezone
from itertools import product
from app.auth import get_password_hash
import stripe
import os
import httpx
import json
from urllib.parse import urljoin, urlparse
from typing import List, Optional, Dict, Any, Tuple
from fastapi import FastAPI, Depends, HTTPException, status, Request, Body
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, EmailStr
from bson import ObjectId
from google.oauth2 import id_token
from google.auth.transport import requests
import app.database as database
import app.schemas as schemas
import app.auth as auth
import app.ai_generator as ai_generator

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info("--- Starting main.py initialization ---")

try:
    env_path = Path(__file__).resolve().parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
    logger.info("✅ .env file loaded.")
    app = FastAPI(title="Mercury Pages API")


    def serialize_doc(doc):
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc


    def serialize_docs(docs):
        return [serialize_doc(doc) for doc in docs]


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


    def normalize_variants(variants_raw: Any) -> Dict[str, List[str]]:
        normalized = {}
        if isinstance(variants_raw, dict):
            for key, values in variants_raw.items():
                str_values = []
                if isinstance(values, list):
                    for v in values:
                        if isinstance(v, str):
                            str_values.append(v)
                        elif isinstance(v, dict) and v:
                            str_values.append(next(iter(v.values())))
                elif isinstance(values, str):
                    str_values.append(values)
                normalized[str(key).strip()] = [v.strip() for v in str_values if v]

        elif isinstance(variants_raw, list):
            for item in variants_raw:
                if isinstance(item, str) and ":" in item:
                    name, value = item.split(":", 1)
                    normalized.setdefault(name.strip(), []).append(value.strip())
                elif isinstance(item, dict):
                    # Handle list of dicts like [{"name": "Size", "options": ["S", "M"]}]
                    name = item.get("variant_name") or item.get("name") or "Option"
                    options = item.get("options") or [next(iter(item.values()))] if item else []
                    normalized.setdefault(str(name), []).extend([str(opt).strip() for opt in options])
                elif isinstance(item, str):
                    normalized.setdefault("Option", []).append(item)

        if not normalized:
            return {"default": ["Default"]}

        return normalized


    def normalize_variants_for_shopify(variants_dict):
        options = []
        option_names = list(variants_dict.keys())

        # Build Shopify options
        for name, values in variants_dict.items():
            cleaned_values = [v.split(":")[-1].strip() if ":" in v else v for v in values]
            options.append({"name": name, "values": cleaned_values})

        return options


    from urllib.parse import urlparse


    def clean_image_url(url: str) -> Optional[str]:

        if not url:
            return None

        if "," in url:
            url = url.split(",")[0].split(" ")[0]

        url = url.strip()

        if url.startswith("//"):
            url = "https:" + url

        parsed = urlparse(url)
        if parsed.scheme not in ["http", "https"]:
            return None
        if " " in url or "Liquid error" in url:
            return None

        valid_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp"]
        if not any(url.lower().__contains__(ext) for ext in valid_extensions):
            return None

        return url


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

        title_tag = soup.find('meta', property='og:title')
        if not title_tag:
            title_tag = soup.find('h1')
        if title_tag and title_tag.get('content'):
            data["original_title"] = title_tag.get('content').strip()
        elif title_tag and title_tag.string:
            data["original_title"] = title_tag.string.strip()

        price_tag = soup.find('meta', property='og:price:amount')
        if not price_tag:
            price_tag = soup.find(class_=lambda x: x and ('price' in x.lower() or 'current-price' in x.lower()))
        if price_tag and price_tag.get('content'):
            data["price"] = price_tag.get('content').strip()
        elif price_tag and price_tag.string:
            data["price"] = price_tag.string.strip()

        variants = {}
        for select_tag in soup.find_all('select'):
            option_name = select_tag.get('aria-label') or select_tag.get('name') or 'Option'
            options = [opt.string.strip() for opt in select_tag.find_all('option') if opt.string]
            if options:
                variants[option_name] = options
        if variants:
            data['variants'] = variants

        availability_text = soup.find(text=lambda x: x and ("out of stock" in x.lower() or "unavailable" in x.lower()))
        if availability_text:
            data['availability'] = False

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
        variants: Dict[str, list[str]]
        price: str
        category: str
        benefits_list: list[str]
        availability: bool


    def safe_hash_password(password: str):
        truncated = password[:72]
        return get_password_hash(truncated)

    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    YOUR_DOMAIN = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
    BROWSER_API_WSS = os.environ.get("BROWSER_API_WSS")
    SCRAPE_DO_API = os.environ.get("SCRAPE_DO_API")
    logger.info("✅ Stripe config loaded.")
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")

    PRICE_IDS = {
        "craftsman": os.environ.get("STRIPE_CRAFTSMAN_PRICE_ID"),
        "artisan": os.environ.get("STRIPE_ARTISAN_PRICE_ID"),
        "inventor": os.environ.get("STRIPE_INVENTOR_PRICE_ID"),
    }

    BRAND_VOICE_LIMIT = 1
    FREE_PLAN_LIMIT = 5

    # --- CORS Middleware ---
    origins = [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://localhost:3003",
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
                "subscription_plan": "free",
                "subscription_active": False,
                "pages_generated_month": 0,
                "current_month": datetime.now(timezone.utc).strftime("%Y-%m"),
                "brand_voice_used": 0,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "supabase_id": user.supabase_id,
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
            raise
        except Exception as e:
            logger.error(f"CRITICAL ERROR in /token endpoint: {e}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="An internal server error occurred during login.")


    @app.post("/google-login", response_model=schemas.Token)
    async def google_login(token_data: schemas.GoogleToken):
        google_user_info = None
        try:
            request = requests.Request()
            google_user_info = id_token.verify_oauth2_token(
                token_data.id_token,
                request,
                GOOGLE_CLIENT_ID
            )
            logger.info(f"GOOGLE LOGIN SUCCESS: {id_token}")
            logger.info(f"GOOGLE LOGIN: {google_user_info}")

        except ValueError as e:
            logger.warning(f"Google token validation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or Expired Google ID Token",
            )

        except Exception as e:
            logger.error(f"Error during token validation: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during token validation.")

        email = google_user_info.get("email")
        name = google_user_info.get("name")
        is_verified = google_user_info.get("email_verified")

        if not email or not is_verified:
            raise HTTPException(status_code=400, detail="Invalid email address.")

        try:
            user = database.get_user_by_email(email)

            if not user:
                # Create user
                new_user_data = {
                    "email": email,
                    "name": name,
                    "is_google_user": True
                }
                database.create_user(new_user_data)  # Save the user in the db
                user = database.get_user_by_email(email)
            access_token = auth.create_access_token(data={"sub": user['email']})
            return {"access_token": access_token, "token_type": "bearer"}

        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="An internal server error occurred during Google sign-in.")


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


    @app.post("/ai-extract-data", response_model=AIExtractResponse)
    async def extract_data_with_ai(request: AIExtractRequest, current_user: dict = Depends(auth.get_current_user)):
        email = current_user.get("email")
        user = database.get_user_by_email(email)
        logger.info(f"User limits: {user.get('pages_generated_month', 0)}/{FREE_PLAN_LIMIT} pages used this month.")
        if user.get("subscription_plan", "free").lower() == "free" and user.get("pages_generated_month",0) >= FREE_PLAN_LIMIT:
            logger.warning(
                f"User {email} exceeded plan limit ({user.get("pages_generated_month", 0)}/{FREE_PLAN_LIMIT}). Raising 403.")
            raise HTTPException(
                status_code=403,
                detail=f"You have exceeded your limit of {FREE_PLAN_LIMIT} pages for the month. Please upgrade your plan."
            )

        job_id = database.create_scraping_job(request.source_url, email)
        try:
            url = request.source_url
            logger.info(f"Job {job_id}: Extracting data from URL: {url}")

            parsed_url = urlparse(url)
            clean_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

            async with httpx.AsyncClient() as client:
                scrape_do_url = f"https://api.scrape.do/?url={clean_url}&token={SCRAPE_DO_API}&render=true&super=true"
                logger.info(f"Job {job_id}: Making Scrape.do API call to {scrape_do_url}")
                scrape_resp = await client.get(scrape_do_url, timeout=60.0)
                scrape_resp.raise_for_status()
                extracted_data = extract_product_data(scrape_resp.text, clean_url)

            logger.info(f"Job {job_id}: Product data extraction successful.")
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
                "variants": normalize_variants(openai_data.get("variants") or extracted_data.get("variants", {})),
                "price": extracted_data.get("price", "N/A"),
                "category": openai_data.get("category", "Uncategorized"),
                "benefits_list": openai_data.get("benefits_list", []),
                "availability": extracted_data.get("availability", False),
            }

            database.update_job_with_result(ObjectId(job_id), final_response_data)
            database.increment_user_monthly_usage(email)
            logger.info(f"Job {job_id}: Completed successfully")

            return final_response_data

        except Exception as e:
            logger.error(f"Job {job_id}: Failed with error: {e}")
            database.update_job_with_error(ObjectId(job_id), str(e))
            raise HTTPException(status_code=500, detail=f"AI extraction failed: {str(e)}")


    @app.get("/jobs/{job_id}", response_model=schemas.Job)
    def get_job_status(job_id: str, current_user: dict = Depends(auth.get_current_user)):
        job = database.get_job_by_id(job_id)
        if not job or job.get("user_email") != current_user.get("email"):
            raise HTTPException(status_code=404, detail="Job not found")
        return job


    @app.get("/jobs", response_model=list[schemas.Job])
    def list_jobs(current_user: dict = Depends(auth.get_current_user)):
        jobs = database.get_jobs_by_user(current_user["email"])
        print(f"Trying to get jobs for...{current_user.get('email')}")
        return serialize_docs(jobs)


    @app.get("/me", response_model=schemas.UserInfo)
    def get_current_user_info(current_user: dict = Depends(auth.get_current_user)):
        user = database.get_user_by_email(current_user["email"])
        plan = user.get("subscription_plan", "free").lower()
        limits = FREE_PLAN_LIMIT

        return {
            "email": user["email"],
            "first_name": user.get("first_name"),
            "subscription_plan": plan,
            "subscription_active": user.get("subscription_active", False),
            "pages_limit_month": limits,
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
        user = database.get_user_by_email(current_user["email"])
        if not user or not user.get("stripe_subscription_id"):
            raise HTTPException(status_code=400, detail="Stripe subscription ID not found.")
        try:
            stripe.Subscription.cancel(user["stripe_subscription_id"])
            database.update_user_subscription_status(user["email"], "free", False)
            return {"status": "success", "message": "Subscription cancelled successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


    def generate_shopify_variants(variants_dict: Dict[str, List[str]]) -> Tuple[List[dict], List[dict]]:
        max_options = 3
        max_variants = 250

        if not variants_dict:
            return (
                [{"name": "Title", "values": ["Default"]}],
                [{"option1": "Default"}],
            )

        option_names = list(variants_dict.keys())[:max_options]

        cleaned_variants_dict = {}

        for name in option_names:
            values = variants_dict.get(name, [])

            cleaned_values = []
            for v in values:
                if not v:
                    continue
                val = v.split(":", 1)[-1].strip()
                val = val.replace("mercury-", "").strip()
                cleaned_values.append(val)

            unique_values = list(dict.fromkeys([v.strip().title() for v in cleaned_values if v.strip()]))
            cleaned_variants_dict[name] = unique_values

        options = [{"name": name, "values": values} for name, values in cleaned_variants_dict.items()]

        all_keys = list(cleaned_variants_dict.keys())
        all_values = [cleaned_variants_dict[k] for k in all_keys]

        variants = []
        seen_combos = set()

        for combo in product(*all_values):
            combo_key = tuple(combo)
            if combo_key in seen_combos:
                continue  # skip duplicate combos
            seen_combos.add(combo_key)

            variant = {f"option{i + 1}": val for i, val in enumerate(combo)}
            variants.append(variant)

            if len(variants) >= max_variants:
                break

        if not variants:
            variants = [{"option1": "Default"}]
            options = [{"name": "Title", "values": ["Default"]}]

        return options, variants


    async def _perform_shopify_export(job_id: str, user: dict, product_data: schemas.ProductData):
        store_url = user.get("shopify_store_url")
        access_token = user.get("shopify_access_token")

        if not store_url or not access_token:
            database.update_shopify_export_job_with_error(ObjectId(job_id), "Shopify credentials not set.")
            return

        try:
            clean_price_str = ''.join(c for c in product_data.price if c.isdigit() or c == '.')
            sanitized_price = f"{float(clean_price_str):.2f}"
        except (ValueError, TypeError):
            logger.warning(f"Job {job_id}: Invalid price '{product_data.price}'. Defaulting to 0.00.")
            sanitized_price = "0.00"

        options, variants = generate_shopify_variants(product_data.variants, sanitized_price)

        if len(variants) > 250:
            logger.error(f"Job {job_id}: Exceeded Shopify's 250 variant limit. Generated {len(variants)} variants.")
            database.update_shopify_export_job_with_error(job_id,
                                                          "Generated too many variants. Shopify limits products to 250 variants.")
            return

        images = [{"src": clean_image_url(img)} for img in (product_data.images or []) if clean_image_url(img)]

        limited_images = images[:15]
        payload = {
            "product": {
                "new_title": product_data.new_title or "Untitled",
                "body_html": product_data.description or "",
                "price": product_data.price or "0.00",
                "options": options,
                "variants": variants,
                "images": limited_images
            }
        }

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

            database.update_shopify_export_job_with_result(ObjectId(job_id), {"product": resp.json()})
            logger.info(f"Job {job_id}: Successfully exported product to Shopify.")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error during Shopify export for job {job_id}: {e.response.text}")
            database.update_shopify_export_job_with_error(ObjectId(job_id), f"Shopify API error: {e.response.text}")
        except httpx.TimeoutException:
            logger.error(f"Job {job_id}: Timeout during Shopify export.")
            database.update_shopify_export_job_with_error(ObjectId(job_id), "Shopify export timed out.")
        except Exception as e:
            logger.error(f"Job {job_id}: Unexpected error during Shopify export: {e}")
            database.update_shopify_export_job_with_error(ObjectId(job_id),
                                                          f"An internal server error occurred during export: {str(e)}")


    @app.post("/users/me/shopify", status_code=status.HTTP_200_OK)
    async def save_shopify_credentials(
            creds: ShopifyCredentials,
            current_user: dict = Depends(auth.get_current_user)
    ):
        logger.info(f"Trying to save: {current_user['email']}")

        try:
            database.users_collection.update_one(
                {"email": current_user["email"]},
                {"$set": {
                    "shopify_store_url": creds.store_url,
                    "shopify_access_token": creds.access_token
                }}
            )
            logger.info(f"Shopify credentials saved for user: {current_user['email']}")
            return {"status": "success", "message": "Shopify credentials saved."}
        except Exception as e:
            logger.error(f"Error saving Shopify credentials for {current_user['email']}: {e}")
            raise HTTPException(status_code=500, detail="Internal server error saving credentials.")


    @app.patch("/jobs/{job_id}", status_code=status.HTTP_200_OK)
    def update_job_result(
            job_id: str,
            payload: schemas.ProductPartialUpdate,
            current_user: dict = Depends(auth.get_current_user)
    ):

        logger.info(f"PATCH /jobs/{job_id} called by {current_user.get('email')}")
        try:
            job = database.get_job_by_id(job_id)
            if not job or job.get("user_email") != current_user.get("email"):
                logger.warning(f"Job {job_id} not found or unauthorized access by {current_user.get('email')}")
                raise HTTPException(status_code=404, detail="Job not found")

            existing_result = job.get("result") or {}
            updated_result = {**existing_result}

            body = payload.model_dump(exclude_unset=True)
            for k, v in body.items():
                updated_result[k] = v

            database.update_job_with_result(ObjectId(job_id), updated_result)
            updated_job = database.get_job_by_id(job_id)
            return serialize_doc(updated_job)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating job {job_id}: {e}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="Failed to update job result")


    @app.post("/shopify/export", status_code=status.HTTP_200_OK)
    async def export_to_shopify(
            product: schemas.ProductData,
            current_user: dict = Depends(auth.get_current_user)
    ):
        logger.info(f"Received product data: {product.dict()}")
        user = database.get_user_by_email(current_user["email"])
        store_url = user.get("shopify_store_url")
        access_token = user.get("shopify_access_token")

        if not store_url or not access_token:
            raise HTTPException(
                status_code=400,
                detail="Shopify credentials not set. Please complete onboarding."
            )

        # --- Price sanitization ---
        try:
            clean_price_str = ''.join(c for c in product.price if c.isdigit() or c == '.')
            sanitized_price = f"{float(clean_price_str):.2f}"
        except (ValueError, TypeError):
            sanitized_price = "0.00"

        # --- Variants ---
        options, variants = generate_shopify_variants(product.variants)
        if len(variants) > 250:
            raise HTTPException(status_code=400, detail="Too many variants (Shopify max = 250)")

        images = [{"src": clean_image_url(img)} for img in (product.images or []) if clean_image_url(img)]
        limited_images = images[:15]

        payload = {
            "product": {
                "title": product.new_title or getattr(product, "original_title", None) or "Untitled Product",
                "body_html": product.description or "",
                "price": product.price or "0.00",
                "options": options,
                "variants": variants,
                "images": limited_images,
            }
        }

        logger.info("Payload for Shopify:")
        logger.info(json.dumps(payload, indent=2))

        # --- Call Shopify API ---
        try:
            async with httpx.AsyncClient() as client:
                url = f"https://{store_url}/admin/api/{os.environ.get('SHOPIFY_API_VERSION', '2023-07')}/products.json"
                headers = {
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": access_token,
                }
                resp = await client.post(url, headers=headers, json=payload, timeout=120.0)
                resp.raise_for_status()
                shopify_data = resp.json()

            logger.info("✅ Shopify export successful.")
            return {"status": "success", "product": shopify_data}

        except httpx.HTTPStatusError as e:
            logger.error(f"Shopify API error: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Shopify API error: {e.response.text}")

        except httpx.TimeoutException:
            logger.error("Shopify export timed out.")
            raise HTTPException(status_code=504, detail="Shopify export timed out.")

        except Exception as e:
            logger.error(f"Unexpected error during Shopify export: {e}")
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


    logger.info("🟢 FastAPI app initialization complete. Server is ready.")


except Exception as e:
    logger.error("❌ CRITICAL STARTUP ERROR:")
    logger.error(traceback.format_exc())
    raise e
