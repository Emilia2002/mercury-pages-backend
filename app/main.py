# FILE: app/main.py
import logging
import traceback
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

# --- CONFIGURAREA LOGĂRII (PRIMUL LUCRU) ---
# Aceasta asigură că log-urile vor fi afișate în consola Render
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info("--- Starting main.py initialization ---")

try:
    # Import your modules
    import app.database as database
    import app.schemas as schemas
    import app.auth as auth
    import app.ai_generator as ai_generator

    logger.info("✅ Modules imported successfully.")

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


    # --- Stripe & Plan Config ---
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    YOUR_DOMAIN = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
    logger.info("✅ Stripe config loaded.")

    PRICE_IDS = {
        "craftsman": os.environ.get("STRIPE_CRAFTSMAN_PRICE_ID"),
        "artisan": os.environ.get("STRIPE_ARTISAN_PRICE_ID"),
        "inventor": os.environ.get("STRIPE_INVENTOR_PRICE_ID"),
    }

    PLAN_LIMITS = {
        "free": {"pages_per_month": 5},
        "craftsman": {"pages_per_month": 30},
        "artisan": {"pages_per_month": 100},
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


    # ... (restul endpoint-urilor dumneavoastră rămân la fel, dar ar trebui să înlocuiți 'print' cu 'logger.info' sau 'logger.error')

    logger.info("🟢 FastAPI app initialization complete. Server is ready.")

except Exception as e:
    # --- PLASA DE SIGURANȚĂ ---
    # Dacă apare orice eroare în timpul pornirii, o vom afișa aici.
    logger.error("❌ CRITICAL STARTUP ERROR:")
    logger.error(traceback.format_exc())
    # --- --------------------- ---
    # Ridicăm din nou eroarea pentru ca procesul să se oprească
    raise e