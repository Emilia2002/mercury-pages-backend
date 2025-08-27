# FILE: run_worker.py
import time
import json
from database import get_next_pending_job, update_job_with_result, update_job_with_error
from scraper import scrape_aliexpress_product
from ai_generator import generate_product


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

                # Pasul 2: Rularea generatorului AI
                print(f"[{job_id}] -> Step 2: Sending scraped data to AI generator...")
                enriched_content = generate_product(scraped_data)
                if not enriched_content:
                    raise Exception("AI generation failed.")
                print(f"[{job_id}] -> Step 2: AI generation FINISHED successfully.")

                # --- NEW STEP: Data Cleaning and Validation ---
                if 'variants' in enriched_content and isinstance(enriched_content['variants'], dict):
                    if 'sizes' in enriched_content['variants'] and isinstance(enriched_content['variants']['sizes'],
                                                                              dict):
                        # Convert the malformed dictionary to a list of strings
                        sizes_data = enriched_content['variants']['sizes']
                        cleaned_sizes_list = [f"{key}: {value}" for key, value in sizes_data.items()]
                        enriched_content['variants']['sizes'] = cleaned_sizes_list
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