import json
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BROWSER_API_WSS = "wss://brd-customer-hl_31a47ad1-zone-scraping_browser1:r1rnojcfexvj@brd.superproxy.io:9222"

def scrape_aliexpress_product(url: str) -> dict | None:
    product_data = {

        "title": None,

        "price": None,

        "images": [],

        "color": [],

        "size_info": []

    }

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(BROWSER_API_WSS)
        page = browser.new_page()

        try:
            print(f"[{url}] - Navighez prin Browser API...")
            page.goto(url, timeout=120000, wait_until='domcontentloaded')

            print(f"[{url}] - Pagină încărcată. Extrag datele...")
            try:
                title_selector = "h1[data-pl='product-title']"
                page.wait_for_selector(title_selector, timeout=10000)
                product_data['title'] = page.locator(title_selector).inner_text()
            except PlaywrightTimeoutError:
                print("Title not extracted")

            try:
                price_selector = "span[class*='price-default--current']"
                page.wait_for_selector(price_selector, timeout=10000)
                pret_brut = page.locator(price_selector).first.inner_text()
                if pret_brut:
                    product_data['price'] = ''.join(filter(lambda char: char.isdigit() or char in ',.', pret_brut))
            except PlaywrightTimeoutError:
                print("Price not extracted")

            try:
                image_container_selector = "div[class*='main-image--wrap']"
                page.wait_for_selector(image_container_selector, timeout=5000)
                image_elements = page.locator(f"{image_container_selector} img")
                product_data['images'] = [img.get_attribute("src") for img in image_elements.all()]
            except PlaywrightTimeoutError:
                print("Images not extracted")

            try:
                color_selector = "div[class*='sku-item--title']"
                page.wait_for_selector(color_selector, timeout=10000)
                product_data_raw =  page.locator(color_selector).first.inner_text()
                product_data['color'] = [el.inner_text().replace("Color:", "").strip()
                                         for el in page.locator(color_selector).all()]
            except PlaywrightTimeoutError:
                print("Nu a fost extrasa culoarea")

            try:
                size_info = "div[class*='sku-item--sizeInfo']"
                page.wait_for_selector(size_info, timeout=10000)
                product_data['size_info'] = page.locator(size_info).first.inner_text()
            except PlaywrightTimeoutError:
                print("Nu au fost extrase informatiile marimilor")

            print(f"✅ - Data extracted successfully!")
            print(product_data)
            return product_data

        except Exception as e:
            print(f"❌ [{url}] - Scraper error: {e}")
            return None
        finally:
            browser.close()
            print(f"[{url}] - Browser closed.")