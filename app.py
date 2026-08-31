"""
Single entry point. Run this to start everything:

    python app.py

Does a preflight check (DB reachable + seeded, vector index built, Ollama
reachable) and auto-fixes what it safely can (creates tables, loads seed
data, builds the vector index) before starting the web app on one port.
"""
import json
import sys

from config.settings import settings
from database.db import init_db
from database.phone_service import PhoneService
from rag.vector_store import VectorStore
from chatbot.llm_client import LLMClient
from utils.logger import get_logger

logger = get_logger(__name__)


def check_database() -> bool:
    try:
        init_db()
    except Exception as exc:
        logger.error(f"Cannot reach PostgreSQL at {settings.db_host}:{settings.db_port}: {exc}")
        logger.error("Check your .env DB_* settings and that PostgreSQL is running.")
        return False

    phones = PhoneService.list_phones()
    if not phones:
        logger.info("Database is empty - loading seed data")
        with open(settings.seed_data_path, encoding="utf-8") as f:
            for phone in json.load(f):
                PhoneService.upsert_phone(phone)
        logger.info("Seed data loaded")
    else:
        logger.info(f"Database OK ({len(phones)} phones)")
    return True


def check_vector_store() -> bool:
    try:
        store = VectorStore()
        phones = PhoneService.list_phones()
        if store.collection.count() != len(phones) and phones:
            logger.info(
                f"Vector index out of sync ({store.collection.count()} indexed vs "
                f"{len(phones)} in DB) - rebuilding"
            )
            store.rebuild(phones)
        else:
            logger.info(f"Vector index OK ({store.collection.count()} entries)")
        return True
    except Exception as exc:
        logger.error(f"Vector store setup failed: {exc}")
        return False


def check_ollama() -> None:
    if LLMClient().is_reachable():
        logger.info(f"Ollama OK (model: {settings.ollama_model})")
    else:
        logger.warning(
            f"Ollama not reachable at {settings.ollama_host}. "
            f"Start it with 'ollama serve' and 'ollama pull {settings.ollama_model}'. "
            "The app will still start, but chat/review requests will fail until then."
        )


def main() -> None:
    logger.info("Running preflight checks...")
    if not check_database():
        sys.exit(1)
    if not check_vector_store():
        sys.exit(1)
    check_ollama()

    logger.info(f"Starting web app on http://{settings.api_host}:{settings.api_port}")
    import uvicorn
    from api.main import app
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
