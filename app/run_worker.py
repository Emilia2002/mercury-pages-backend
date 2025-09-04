import os
import time
import json
import httpx
from pathlib import Path
from http.client import HTTPException
from typing import Dict, List, Any
from pydantic import BaseModel
from datetime import datetime, timezone
from dotenv import load_dotenv
from openai import OpenAI
from app.main import logger
from .database import get_next_pending_job, update_job_with_result, update_job_with_error

# Load environment variables
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


class Job(BaseModel):
    _id: str
    url: str
    status: str
    user_email: str
    created_at: datetime

    @classmethod
    def from_dict(cls, d):
        d['_id'] = str(d['_id'])
        return cls(**d)


def ai_extract_data(http_client: httpx.Client, url: str) -> Dict[str, Any]:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set.")

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={GEMINI_API_KEY}"

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "description": {"type": "STRING"},
            "images": {"type": "ARRAY", "items": {"type": "STRING"}},
            "variants": {"type": "ARRAY", "items": {"type": "STRING"}},
            "price": {"type": "STRING"},
            "category": {"type": "STRING"},
            "availability": {"type": "BOOLEAN"}
        },
        "propertyOrdering": ["title", "description", "images", "variants", "price", "category", "availability"]
    }

    payload = {
        "contents": [
            {"parts": [{"text": f"Extract structured data from the following product page URL: {url}"}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema
        }
    }

    response = http_client.post(api_url, json=payload, timeout=60.0)
    response.raise_for_status()

    gemini_response = response.json()
    parts = gemini_response.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])

    if not parts or "text" not in parts[0]:
        logger.error("Gemini API response is missing text content.")
        raise HTTPException(status_code=500, detail="Failed to get valid response from AI model.")

    extracted_data = json.loads(parts[0]["text"])
    return extracted_data


def generate_product(final_product_data: dict) -> dict | None:
    """Generates product content using the OpenAI API."""
    api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        print("❌ AI Generator Error: Could not find OPENAI_API_KEY.")
        return None

    client = OpenAI(api_key=api_key)

    print("AI Generator: Building prompt for product page...")

    prompt = f"""
    Act as an expert Shopify e-commerce copywriter. Your task is to transform the following raw product data into a compelling product page.
    The tone should be persuasive and focused on customer benefits.

    **Raw Product Data:**
    {json.dumps(final_product_data, indent=2)}

    **Your Tasks:**
    1.  **New Product Title:** Create a catchy, SEO-friendly title (max 60 characters).
    2.  **Product Description:** Write a 2-3 paragraph product description. Start with a hook, explain the benefits, and end with a call to action. Use emojis where appropriate.
    3.  **Bulleted Benefits:** List 3-5 key benefits as a simple list of strings.
    4.  **Variants:** Format the provided variants data into a user-friendly JSON object. For ALL keys (e.g., "Product Measurements", "Size Info", "Color"), the value MUST be a flat array of strings. If the original data is a dictionary (like {{"Bust Size": "..."}}), convert it to a string array like ["Bust Size: ...", "Skirt Length: ..."]. Ensure there are no nested dictionaries or lists within the values.

    Return the response ONLY as a valid JSON object with these keys: "new_title", "description", "benefits_list", "variants".
    """

    print("AI Generator: Sending prompt to OpenAI...")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a helpful assistant designed to output JSON."},
                {"role": "user", "content": prompt}
            ]
        )
        ai_content_str = response.choices[0].message.content
        ai_content_json = json.loads(ai_content_str)
        print("✅ AI Generator: Content received successfully!")
        return ai_content_json
    except Exception as e:
        print(f"AI Generator: An error occurred: {e}")
        return None


def main():
    print("🚀 Worker started. Looking for jobs...")

    with httpx.Client(timeout=120.0) as http_client:
        while True:
            job = get_next_pending_job()
            if job:
                job_id = job['_id']
                print(f"--- STARTING JOB: {job_id} ---")
                print(f"URL: {job['url']}")
                try:
                    logger.info(f"[{job_id}] -> Step 1: Using AI to extract product data...")
                    extracted_data = ai_extract_data(http_client, job['url'])
                    logger.info(f"[{job_id}] -> Step 1: AI extraction FINISHED successfully.")

                    logger.info(f"[{job_id}] -> Step 2: Generating long-form content from extracted data...")
                    enriched_data = generate_product(extracted_data)
                    if not enriched_data:
                        raise Exception("AI generation failed.")
                    logger.info(f"[{job_id}] -> Step 2: AI generation FINISHED successfully.")

                    logger.info(f"[{job_id}] -> Step 3: Merging data and saving final result to database...")
                    final_product_data = {**extracted_data, **enriched_data}
                    update_job_with_result(job_id, final_product_data)
                    logger.info(f"✅ Job {job_id} completed successfully.")

                except Exception as e:
                    error_message = f"Job {job_id} failed: {e}"
                    print(f"❌ {error_message}")
                    update_job_with_error(job_id, str(e))

            else:
                print("No pending jobs found. Waiting...")
                time.sleep(10)


if __name__ == "__main__":
    main()
