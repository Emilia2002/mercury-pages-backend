import json
import os
from dotenv import load_dotenv
from openai import OpenAI
from pathlib import Path
from typing import Any, Dict, List

def generate_product(product_data: dict) -> dict | None:
    env_path = Path(__file__).resolve().parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
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
    - Original Title: {product_data.get('title')}
    - Price: {product_data.get('price')}
    - Variants: {product_data.get('variants')}

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


def analyze_brand_description(description: str) -> dict | None:
    """
    Analyzes a brand description to extract keywords, recommend social platforms,
    and generate a sample post.
    """
    env_path = Path(__file__).resolve().parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
    api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        print("❌ AI Analyzer Error: OPENAI_API_KEY not found.")
        return None

    client = OpenAI(api_key=api_key)

    print("AI Analyzer: Building prompt for brand voice analysis...")
    prompt = f"""
    Act as an expert brand and social media strategist. Your task is to analyze the following brand description.

    **Brand Description:**
    "{description}"

    **Your Tasks:**
    1.  **Extract Keywords:** Identify 5-7 keywords that best define the brand's personality and tone.
    2.  **Recommend Social Platforms:** Based on the brand voice, recommend the top 2-3 social media platforms.
    3.  **Generate a Sample Post:** Write a short, engaging sample post for the top recommended platform.

    Return the response ONLY as a valid JSON object with these exact keys: "keywords", "social_platforms", "sample_post".
    """

    print("AI Analyzer: Sending advanced prompt to OpenAI...")
    try:
        # This is the missing API call logic
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a brand strategist that only responds in JSON format."},
                {"role": "user", "content": prompt}
            ]
        )

        ai_response_str = response.choices[0].message.content
        ai_response_json = json.loads(ai_response_str)

        print("✅ AI Analyzer: Analysis complete.")
        return ai_response_json

    except Exception as e:
        print(f"❌ AI Analyzer: An error occurred: {e}")
        return None
