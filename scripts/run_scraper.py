"""Re-scrape GSMArena (full Samsung catalog) and overwrite data/seed_data.json.

Usage:
    python -m scripts.run_scraper                  # scrape everything
    python -m scripts.run_scraper --max-phones 50   # cap for a quick run
"""
import argparse
from scraper.gsmarena_scraper import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-phones", type=int, default=None)
    args = parser.parse_args()
    run(max_phones=args.max_phones)
