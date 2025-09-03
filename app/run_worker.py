# FILE: run_worker.py
import time
import json
import logging
import traceback
from database import get_next_pending_job, update_job_with_result, update_job_with_error
from scraper import scrape_aliexpress_product
from ai_generator import generate_product
from typing import Dict, List, Any


# A helper function to sanitize the AI's variant output
def _sanitize_variants(variants: Dict[str, Any]) -> Dict[str, List[str]]:
    sanitized_variants = {}
    for key, values in variants.items():
        cleaned_list = []
        if isinstance(values, dict):
            # Case 1: The value is a dictionary (e.g., {'Bust Size': '...'})
            cleaned_list.extend([f"{k}: {v}" for k, v in values.items()])
        elif isinstance(values, list):
            # Case 2: The value is a list, but might contain nested dictionaries
            for item in values:
                if isinstance(item, dict):
                    # Flatten the nested dictionary
                    cleaned_list.extend([f"{k}: {v}" for k, v in item.items()])
                else:
                    # If it's a string or other simple type, add it directly
                    cleaned_list.append(str(item))
        else:
            # Case 3: The value is a simple string, wrap it in a list
            cleaned_list.append(str(values))

        sanitized_variants[key] = cleaned_list

    return sanitized_variants


def main():
    print("🚀 Worker started. Looking for jobs...")
    while True:
        job = get_next_pending_job()
        if job:
            job_id = job['_id']
            print(f"--- STARTING JOB: {job_id} ---")
            print(f"URL: {job['url']}")
            try:
                print(f"[{job_id}] -> Step 1: Starting scraper...")
                scraped_data = scrape_aliexpress_product(job['url'])
                if not scraped_data:
                    raise Exception("Scraper returned no data.")
                print(f"[{job_id}] -> Step 1: Scraper FINISHED successfully.")

                print(f"[{job_id}] -> Step 2: Sending scraped data to AI generator...")
                enriched_content = generate_product(scraped_data)
                if not enriched_content:
                    raise Exception("AI generation failed.")
                print(f"[{job_id}] -> Step 2: AI generation FINISHED successfully.")

                # --- NEW STEP: Robust Data Cleaning and Validation ---
                if 'variants' in enriched_content and isinstance(enriched_content['variants'], dict):
                    print(f"[{job_id}] -> BEFORE cleaning: {enriched_content['variants']}")
                    enriched_content['variants'] = _sanitize_variants(enriched_content['variants'])
                    print(f"[{job_id}] -> AFTER cleaning: {enriched_content['variants']}")
                # --- END OF NEW STEP ---

                # Pasul 3: Combinarea rezultatelor
                final_product = {**scraped_data, **enriched_content}

                # Pasul 4: Salvarea rezultatului final în MongoDB
                print(f"[{job_id}] -> Step 3: Saving final result to database...")
                update_job_with_result(job_id, final_product)
                print(f"✅ Job {job_id} completed successfully.")

            except Exception as e:
                error_message = f"Job {job_id} failed: {e}"
                print(f"❌ {error_message}")
                update_job_with_error(job_id, str(e))
        else:
            print("No pending jobs found. Waiting...")
            time.sleep(5)


if __name__ == "__main__":
    main()