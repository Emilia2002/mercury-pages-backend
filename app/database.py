import os

from dotenv import load_dotenv
from pathlib import Path

from pydantic import EmailStr
from pymongo import MongoClient, ReturnDocument
from bson.objectid import ObjectId
from datetime import datetime, timezone

# --- Load .env ---
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# --- MongoDB Setup ---
uri = os.environ.get("MONGODB_KEY")  # <-- Use MONGODB_KEY now
if not uri:
    raise ValueError("MONGODB_KEY not set in .env")

try:
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.server_info()
    print(f"✅ Connected to MongoDB")
except Exception as e:
    print(f"❌ MongoDB connection failed:", e)

db = client.fastpage_db

jobs_collection = db.get_collection("jobs")
users_collection = db.get_collection("users")
users_collection.create_index("email", unique=True)  # Ensure unique emails

def create_scraping_job(url: str, user_email: str) -> str:
    job = {
        "url": url,
        "user_email": user_email,
        "type": "scrape",
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc)
    }
    result = jobs_collection.insert_one(job)
    return str(result.inserted_id)


def get_job_by_id(job_id: str):
    job = jobs_collection.find_one({"_id": ObjectId(job_id)})
    if job:
        # Convert ObjectId and datetime to strings for serialization
        job['_id'] = str(job['_id'])
        if isinstance(job.get('created_at'), datetime):
            job['created_at'] = job['created_at'].isoformat()
    return job


def get_next_pending_job():
    job = jobs_collection.find_one_and_update(
        {"status": "pending"},
        {"$set": {"status": "in_progress"}},
        return_document=ReturnDocument.AFTER
    )
    if job:
        # Convert ObjectId and datetime to strings
        job['_id'] = str(job['_id'])
        if isinstance(job.get('created_at'), datetime):
            job['created_at'] = job['created_at'].isoformat()
    return job


def update_job_with_result(job_id: ObjectId, product_data: dict):
    jobs_collection.update_one(
        {"_id": job_id},
        {"$set": {"status": "completed", "result": product_data}}
    )

def update_job_with_error(job_id: ObjectId, error_message: str):
    jobs_collection.update_one(
        {"_id": job_id},
        {"$set": {"status": "failed", "error": error_message}}
    )

def create_user(user_data: dict):
    print("Trying to create user:", user_data)
    try:
        result = users_collection.insert_one(user_data)
        print(f"🆕 User created: {user_data.get('email')} (id: {result.inserted_id})")
        return result
    except Exception as e:
        print("❌ Failed to create user:", e)
        return None


def get_user_by_email(email: EmailStr):
    return users_collection.find_one({"email": email})

def update_user_subscription(email: str, stripe_customer_id: str, stripe_subscription_id: str, plan: str):
    users_collection.update_one(
        {"email": email},
        {"$set": {
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
            "subscription_plan": plan,
            "subscription_active": True
        }}
    )

def reset_monthly_usage(email: str, current_month: str):
    users_collection.update_one(
        {"email": email},
        {"$set": {"pages_generated_month": 0, "current_month": current_month}}
    )

def increment_user_monthly_usage(email: str):
    users_collection.update_one(
        {"email": email},
        {"$inc": {"pages_generated_month": 1}}
    )

def increment_brand_voice_usage(email: str):
    users_collection.update_one(
        {"email": email},
        {"$inc": {"brand_voice_used": 1}}
    )

def get_jobs_by_user(user_email: str):
    jobs = list(jobs_collection.find({"user_email": user_email}).sort("created_at", -1))

    # Convert ObjectId and datetime objects to strings for serialization
    for job in jobs:
        job['_id'] = str(job['_id'])
        if isinstance(job.get('created_at'), datetime):
            job['created_at'] = job['created_at'].isoformat()

    return jobs


def update_user_brand_profile(email: str, brand_profile: dict):
    print(f"DATABASE: Updating brand profile for {email}")
    users_collection.update_one(
        {"email": email},
        {"$set": {"brand_profile": brand_profile}}
    )


def update_user_subscription_status(email: str, plan: str, is_active: bool):
    users_collection.update_one(
        {"email": email},
        {"$set": {
            "subscription_plan": plan,
            "subscription_active": is_active,
            "stripe_subscription_id": None  # <-- Clear the subscription ID
        }}
    )


def update_user_shopify_credentials(email: str, store_url: str, access_token: str):
    users_collection.update_one(
        {"email": email},
        {"$set": {
            "shopify_store_url": store_url,
            "shopify_access_token": access_token
        }}
    )
