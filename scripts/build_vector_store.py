"""Build/refresh the Chroma vector index from the database.

Usage: python -m scripts.build_vector_store
"""
from database.phone_service import PhoneService
from rag.vector_store import VectorStore
from utils.logger import get_logger

logger = get_logger(__name__)


def main():
    phones = PhoneService.list_phones()
    if not phones:
        logger.error("No phones in database. Run scripts.setup_db first.")
        return
    store = VectorStore()
    store.rebuild(phones)


if __name__ == "__main__":
    main()
