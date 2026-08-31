"""Create tables and load data/seed_data.json into PostgreSQL.

Usage: python -m scripts.setup_db
"""
import json
from database.db import init_db
from database.phone_service import PhoneService
from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


def main():
    init_db()
    logger.info("Tables created/verified")

    with open(settings.seed_data_path, encoding="utf-8") as f:
        phones = json.load(f)

    for phone in phones:
        PhoneService.upsert_phone(phone)

    logger.info(f"Loaded {len(phones)} phones into the database")


if __name__ == "__main__":
    main()
