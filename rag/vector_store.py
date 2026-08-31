import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.utils import embedding_functions
from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

COLLECTION_NAME = "phone_specs"


class VectorStore:
    def __init__(self):
        self.client = chromadb.PersistentClient(
            path=settings.chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=settings.embedding_model
        )
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME, embedding_function=self.embed_fn
        )

    def phone_to_document(self, phone: dict) -> str:
        spec = phone.get("specification", {})
        parts = [f"{phone['name']} (released {phone.get('release_year')}, price ${phone.get('price_usd')})"]
        for key, value in spec.items():
            if value:
                parts.append(f"{key.replace('_', ' ')}: {value}")
        return ". ".join(parts)

    def rebuild(self, phones: list[dict]) -> None:
        ids = [str(p["id"]) for p in phones]
        docs = [self.phone_to_document(p) for p in phones]
        metadatas = [{"phone_id": p["id"], "name": p["name"]} for p in phones]

        existing = self.collection.get()
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])

        self.collection.add(ids=ids, documents=docs, metadatas=metadatas)
        logger.info(f"Vector store rebuilt with {len(phones)} phones")

    def query(self, text: str, top_k: int = 3) -> list[dict]:
        result = self.collection.query(query_texts=[text], n_results=top_k)
        hits = []
        for doc, meta in zip(result["documents"][0], result["metadatas"][0]):
            hits.append({"document": doc, "phone_id": meta["phone_id"], "name": meta["name"]})
        return hits
