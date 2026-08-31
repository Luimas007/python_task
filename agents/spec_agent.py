from database.phone_service import PhoneService
from rag.vector_store import VectorStore
from utils.logger import get_logger

logger = get_logger(__name__)


class SpecAgent:
    """Retrieves phone specifications from the database, using the
    vector store to resolve which phone(s) a natural-language query refers to."""

    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store

    def find_relevant_phones(self, query: str, top_k: int = 3) -> list[dict]:
        hits = self.vector_store.query(query, top_k=top_k)
        phones = []
        seen = set()
        for hit in hits:
            if hit["phone_id"] in seen:
                continue
            seen.add(hit["phone_id"])
            phone = PhoneService.get_phone(hit["phone_id"])
            if phone:
                phones.append(phone)
        logger.info(f"SpecAgent resolved {len(phones)} phones for query: {query}")
        return phones

    def get_phone_by_name(self, name: str) -> dict | None:
        return PhoneService.get_phone_by_name(name)

    def list_all(self) -> list[dict]:
        return PhoneService.list_phones()
