"""
GSMArena scraper for Samsung phones.

Scrapes the FULL Samsung catalog listing on GSMArena (all series: Galaxy S,
Z Fold/Flip, A, M, Note, Tab excluded, etc.), not just the S-series, by
paginating through the brand listing page. GSMArena has no public API and
blocks/rate-limits bots (Cloudflare), so this is not guaranteed to keep
working if the site changes its markup or blocking rules.

`data/seed_data.json` ships pre-populated (Galaxy S21-S26 lineup) so the
rest of the system works without running this script. Re-run this to
refresh/expand it from the live site.

Usage:
    python -m scraper.gsmarena_scraper                 # scrape everything
    python -m scraper.gsmarena_scraper --max-phones 50  # cap for a quick run
"""
import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://www.gsmarena.com"
CATALOG_FIRST_PAGE = f"{BASE_URL}/samsung-phones-9.php"
CATALOG_PAGE_PATTERN = f"{BASE_URL}/samsung-phones-f-9-0-p{{page}}.php"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}
REQUEST_DELAY_SEC = 2.5
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_data.json"

# GSMArena spec-table section -> {row label -> our field name}
SPEC_FIELD_MAP = {
    "Display": {"Size": "display_size", "Type": "display_type", "Resolution": "resolution"},
    "Platform": {"Chipset": "chipset"},
    "Memory": {"Internal": "storage"},
    "Main Camera": {"Triple": "camera_main", "Quad": "camera_main", "Single": "camera_main", "Dual": "camera_main"},
    "Selfie camera": {"Single": "camera_front", "Dual": "camera_front"},
    "Battery": {"Type": "battery_capacity", "Charging": "charging"},
    "Body": {"Dimensions": "dimensions", "Weight": "weight"},
}


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def find_all_samsung_phone_links(max_phones: int | None = None) -> list[str]:
    """Paginate through the full Samsung catalog listing until either
    max_phones is reached or a page returns no new phone links."""
    links: list[str] = []
    page = 1
    while True:
        url = CATALOG_FIRST_PAGE if page == 1 else CATALOG_PAGE_PATTERN.format(page=page)
        try:
            soup = fetch(url)
        except requests.RequestException as exc:
            logger.warning(f"Stopping pagination at page {page}: {exc}")
            break

        page_links = []
        for a in soup.select("div.makers ul li a"):
            href = a.get("href")
            if href and href.endswith(".php") and "-9" not in href.split("/")[0]:
                page_links.append(f"{BASE_URL}/{href}")

        if not page_links:
            logger.info(f"No more phones found at page {page}; stopping")
            break

        links.extend(page_links)
        logger.info(f"Page {page}: found {len(page_links)} phones (total {len(links)})")

        if max_phones and len(links) >= max_phones:
            links = links[:max_phones]
            break

        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    return links


def parse_phone_page(url: str) -> dict:
    soup = fetch(url)
    name_el = soup.select_one("h1.specs-phone-name-title")
    name = name_el.get_text(strip=True) if name_el else "Unknown"

    spec = {}
    for table in soup.select("#specs-list table"):
        section_th = table.select_one("th")
        section = section_th.get_text(strip=True) if section_th else ""
        field_map = SPEC_FIELD_MAP.get(section, {})
        for row in table.select("tr"):
            ttl = row.select_one("td.ttl")
            nfo = row.select_one("td.nfo")
            if not ttl or not nfo:
                continue
            label = ttl.get_text(strip=True)
            value = nfo.get_text(" ", strip=True)
            key = field_map.get(label)
            if key:
                spec[key] = value

    price_el = soup.select_one('td.nfo a[href*="price"]')
    price_text = price_el.get_text(strip=True) if price_el else ""
    price_match = re.search(r"[\d,.]+", price_text)
    price_usd = float(price_match.group().replace(",", "")) if price_match else None

    year_el = soup.find(string=re.compile(r"Released \d{4}"))
    year_match = re.search(r"\d{4}", year_el) if year_el else None

    return {
        "name": name,
        "model_code": re.sub(r"\s+", "_", name).lower(),
        "release_year": int(year_match.group()) if year_match else None,
        "price_usd": price_usd,
        "source_url": url,
        "specification": spec,
    }


def run(max_phones: int | None = None) -> list[dict]:
    logger.info("Starting full-catalog GSMArena scrape for Samsung phones")
    links = find_all_samsung_phone_links(max_phones=max_phones)
    logger.info(f"Total phone links to scrape: {len(links)}")

    phones = []
    for i, link in enumerate(links, 1):
        try:
            phones.append(parse_phone_page(link))
            logger.info(f"[{i}/{len(links)}] Scraped: {link}")
        except Exception as exc:
            logger.warning(f"Failed to scrape {link}: {exc}")
        time.sleep(REQUEST_DELAY_SEC)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(phones, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(phones)} phones to {OUTPUT_PATH}")
    return phones


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-phones", type=int, default=None, help="Cap the number of phones scraped")
    args = parser.parse_args()
    run(max_phones=args.max_phones)
