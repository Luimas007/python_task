"""Sentence embeddings, CPU-bound.

The installed torch build is CPU-only and the host GPU (RTX 3050, 4 GB) is
reserved for the Ollama LLM, so MiniLM runs on the CPU -- roughly 20 ms per
chunk on this machine, which is well inside budget for a corpus of this size.

The model is loaded lazily and once per process.
"""
from __future__ import annotations

import threading

import numpy as np

from backend.config.settings import settings
from backend.core.logging_setup import get_logger

log = get_logger("rag.embedder")

_MODEL = None
_LOCK = threading.Lock()


def get_model():
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            from sentence_transformers import SentenceTransformer

            log.info("loading embedding model %s on %s",
                     settings.embedding.model_name, settings.embedding.device)
            _MODEL = SentenceTransformer(
                settings.embedding.model_name, device=settings.embedding.device
            )
            dim = _MODEL.get_sentence_embedding_dimension()
            if dim != settings.embedding.dim:
                log.warning("EMBED_DIM=%d but model reports %d; using %d",
                            settings.embedding.dim, dim, dim)
        return _MODEL


def embed_texts(texts: list[str], show_progress: bool = False) -> np.ndarray:
    """L2-normalised float32 embeddings, shape (len(texts), dim)."""
    if not texts:
        return np.zeros((0, settings.embedding.dim), dtype=np.float32)
    model = get_model()
    vecs = model.encode(
        texts,
        batch_size=settings.embedding.batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=show_progress,
    )
    return vecs.astype(np.float32)


def embed_query(text: str) -> list[float]:
    """Single query vector as a plain Python list, ready for psycopg2."""
    return [float(x) for x in embed_texts([text])[0]]


def dimension() -> int:
    return int(get_model().get_sentence_embedding_dimension())
