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
pages_collection = db.get_collection("pages")
users_collection = db.get_collection("users")
image_assets_collection = db.get_collection("image_assets")
users_collection.create_index("email", unique=True)  # Ensure unique emails
pages_collection.create_index([("owner_email", 1), ("updated_at", -1)])
pages_collection.create_index([("owner_email", 1), ("job_id", 1)], unique=True)
image_assets_collection.create_index([("user_email", 1), ("created_at", -1)])

def create_scraping_job(url: str, user_email: str, template_id: str | None = None, template_config: dict | None = None) -> str:
    job = {
        "url": url,
        "user_email": user_email,
        "type": "scrape",
        "template_id": template_id,
        "template_config": template_config or {},
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc)
    }
    result = jobs_collection.insert_one(job)
    return str(result.inserted_id)


from datetime import datetime
from bson import ObjectId

def get_job_by_id(job_id: str, user_email: str | None = None):
    try:
        object_id = ObjectId(job_id)
    except Exception:
        return None

    query = {"_id": object_id}
    if user_email:
        query["user_email"] = user_email
    job = jobs_collection.find_one(query)
    if not job:
        return None

    job["id"] = str(job["_id"])
    job.pop("_id", None)

    if isinstance(job.get("created_at"), datetime):
        job["created_at"] = job["created_at"].isoformat()

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


def _serialize_page(page):
    if not page:
        return None
    page["_id"] = str(page["_id"])
    for key in ("created_at", "updated_at"):
        if isinstance(page.get(key), datetime):
            page[key] = page[key].isoformat()
    return page


def create_page_document(*, owner_email: str, job_id: str, page_title: str, template_id: str | None, theme: dict, sections: list):
    now = datetime.now(timezone.utc)
    page = {
        "owner_email": owner_email,
        "job_id": job_id,
        "page_title": page_title,
        "template_id": template_id,
        "theme": theme,
        "sections": sections,
        "schema_version": 1,
        "revision": 0,
        "created_at": now,
        "updated_at": now,
    }
    result = pages_collection.insert_one(page)
    page["_id"] = result.inserted_id
    return _serialize_page(page)


def get_page_document(page_id: str, owner_email: str):
    try:
        page = pages_collection.find_one({"_id": ObjectId(page_id), "owner_email": owner_email})
    except Exception:
        return None
    return _serialize_page(page)


def get_pages_by_user(owner_email: str):
    return [_serialize_page(page) for page in pages_collection.find({"owner_email": owner_email}).sort("updated_at", -1)]


def update_page_document(page_id: str, owner_email: str, revision: int, updates: dict):
    try:
        object_id = ObjectId(page_id)
    except Exception:
        return None, "not_found"
    updates = {key: value for key, value in updates.items() if value is not None}
    updates["updated_at"] = datetime.now(timezone.utc)
    result = pages_collection.find_one_and_update(
        {"_id": object_id, "owner_email": owner_email, "revision": revision},
        {"$set": updates, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if result:
        return _serialize_page(result), None
    exists = pages_collection.find_one({"_id": object_id, "owner_email": owner_email}, {"_id": 1})
    return (None, "conflict" if exists else "not_found")


def delete_page_document(page_id: str, owner_email: str):
    try:
        result = pages_collection.delete_one({"_id": ObjectId(page_id), "owner_email": owner_email})
    except Exception:
        return False
    return result.deleted_count == 1


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


def create_image_asset(asset: dict) -> str:
    result = image_assets_collection.insert_one(asset)
    return str(result.inserted_id)


def create_image_asset_record(
    *,
    user_email: str,
    page_id: str | None,
    job_id: str | None,
    purpose: str,
    template_id: str,
    prompt: str,
    model: str,
    provider_url: str | None,
    provider_b64: str | None,
):
    return create_image_asset({
        "user_email": user_email,
        "page_id": page_id,
        "job_id": job_id,
        "source": "generated",
        "purpose": purpose,
        "template_id": template_id,
        "prompt": prompt,
        "model": model,
        "provider_url": provider_url,
        "provider_b64": None,
        "status": "completed",
        "created_at": datetime.now(timezone.utc),
    })


def update_job_shopify_export(job_id: ObjectId, export_data: dict):
    jobs_collection.update_one(
        {"_id": job_id},
        {"$set": {
            "shopify_export": export_data,
            "result.shopify_export": export_data,
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
