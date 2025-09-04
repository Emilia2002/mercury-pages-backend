import json
import os
from dotenv import load_dotenv
from openai import OpenAI
from pathlib import Path
from typing import Any, Dict, List

def analyze_brand_description(description: str) -> dict | None:
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
