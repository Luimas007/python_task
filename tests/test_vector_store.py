"""Sanity test for document formatting (no DB/network required)."""
from rag.vector_store import VectorStore


def test_phone_to_document_includes_name_and_specs():
    store = VectorStore.__new__(VectorStore)  # skip __init__ (no chroma client needed)
    phone = {
        "name": "Samsung Galaxy S23",
        "release_year": 2023,
        "price_usd": 799.0,
        "specification": {"display_size": "6.1 inches", "chipset": "Snapdragon 8 Gen 2"},
    }
    doc = VectorStore.phone_to_document(store, phone)
    assert "Samsung Galaxy S23" in doc
    assert "6.1 inches" in doc
    assert "Snapdragon 8 Gen 2" in doc
