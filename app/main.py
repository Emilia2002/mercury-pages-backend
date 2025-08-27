# FILE: app/main.py
from datetime import datetime, timezone
import stripe
import os
import json
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
from bson import ObjectId
from fastapi.responses import JSONResponse

# Import your modules
import app.database as database
import app.schemas as schemas
import app.auth as auth
import app.ai_generator as ai_generator

# Load environment variables
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

app = FastAPI(title="Mercury Pages API")


# --- Helper Functions ---
def serialize_doc(doc):
    """Converts a MongoDB document (with ObjectId) to a JSON-friendly dict."""
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def serialize_docs(docs):
    """Converts a list of MongoDB documents."""
    return [serialize_doc(doc) for doc in docs]


# --- Stripe & Plan Config ---
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
YOUR_DOMAIN = os.environ.get("FRONTEND_URL", "http://localhost:3000")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

PRICE_IDS = {
    "craftsman": os.environ.get("STRIPE_CRAFTSMAN_PRICE_ID"),
    "artisan": os.environ.get("STRIPE_ARTISAN_PRICE_ID"),
    "inventor": os.environ.get("STRIPE_INVENTOR_PRICE_ID"),
}

PLAN_LIMITS = {
    "free": {"pages_per_month": 5},
    "craftsman": {"pages_per_month": 30},
    "artisan": {"pages_per_month": 100},
    "inventor": {"pages_per_month": None},  # None means unlimited
}

BRAND_VOICE_LIMIT = 1

# --- CORS Middleware ---
origins = [
    "http://localhost:3000",
    "https://mercury-pages-frontend.vercel.app",
    "https://mercurypages.ai"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.options("/{rest_of_path:path}")
async def preflight_handler(rest_of_path: str):
    return JSONResponse(content={"status": "ok"})
# --- Endpoints ---

@app.get("/")
def read_root():
    return {"message": "Serverul funcționează!"}


@app.post("/register", response_model=schemas.UserInDB)
def register_user(user: schemas.UserCreate):
    """Handles new user registration."""
    existing_user = database.get_user_by_email(user.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_data = {
        "first_name": user.first_name,
        "email": user.email,
        "hashed_password": auth.get_password_hash(user.password),
        "subscription_plan": "free",
        "subscription_active": False,
        "pages_generated_month": 0,
        "current_month": datetime.now(timezone.utc).strftime("%Y-%m"),
        "brand_voice_used": 0,
    }

    result = database.create_user(user_data)
    if not result:
        raise HTTPException(status_code=500, detail="Could not create user.")

    created_user = database.get_user_by_email(user.email)
    return serialize_doc(created_user)


@app.post("/token", response_model=schemas.Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """Handles user login and returns a JWT."""
    user = database.get_user_by_email(form_data.username)
    if not user or not auth.verify_password(form_data.password, user.get('hashed_password')):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    access_token = auth.create_access_token(data={"sub": user['email']})
    return {"access_token": access_token, "token_type": "bearer"}


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
    if not user:
        raise HTTPException(status_code=403, detail="User not found")

    plan = user.get("subscription_plan", "free").lower()
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    current_month_str = datetime.now(timezone.utc).strftime("%Y-%m")
    if user.get("current_month") != current_month_str:
        database.reset_monthly_usage(email, current_month_str)
        user = database.get_user_by_email(email)

    if limits["pages_per_month"] is not None and user.get("pages_generated_month", 0) >= limits["pages_per_month"]:
        raise HTTPException(status_code=403, detail="Monthly page generation limit reached. Please upgrade your plan.")

    job_id = database.create_scraping_job(str(request.source_url), email)
    database.increment_user_monthly_usage(email)
    return {"message": "Scraping job created", "job_id": job_id}


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
        "pages_generated_month": user.get("pages_generated_month", 0)
    }


@app.post("/brand-voice/analyze", response_model=schemas.BrandVoiceAnalyzeResponse)
def analyze_brand_voice(request: schemas.BrandVoiceAnalyzeRequest, current_user: dict = Depends(auth.get_current_user)):
    """Analyzes brand description for keywords, platforms, and a sample post."""
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