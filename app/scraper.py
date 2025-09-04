import httpx
from bs4 import BeautifulSoup
from typing import List
from urllib.parse import urljoin, urlparse


def scrape_images_only(url: str) -> List[str]:

    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        image_urls = []
        image_tags = soup.find_all('img')

        for img in image_tags:
            src = img.get('src')
            if src:
                absolute_url = urljoin(url, src)
                if urlparse(absolute_url).scheme in ['http', 'https']:
                    image_urls.append(absolute_url)

        return image_urls

    except httpx.RequestError as e:
        print(f"Scraper Error: Failed to access URL {url}. Reason: {e}")
        return []
    except Exception as e:
        print(f"Scraper Error: An unexpected error occurred during scraping: {e}")
        return []
