"""Populate knowledge_chunks with text and embeddings."""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg2.extras

from backend.config.settings import settings
from backend.core.logging_setup import get_logger
from database import engine
from backend.rag import chunker, embedder

log = get_logger("rag.indexer")

INSERT = """
INSERT INTO knowledge_chunks
    (phone_id, section, heading, content, char_len, embedding, embedding_model, embedded_at)
VALUES %s
"""


def rebuild() -> dict[str, int]:
    """Drop and rebuild the whole corpus. Idempotent."""
    chunks = chunker.build_all()
    if not chunks:
        log.warning("no chunks to index -- is the phones table empty?")
        return {"chunks": 0, "phones": 0}

    texts = [c["content"] for c in chunks]
    log.info("embedding %d chunks with %s ...", len(texts), settings.embedding.model_name)
    vectors = embedder.embed_texts(texts, show_progress=False)
    log.info("embeddings ready: shape=%s dtype=%s", vectors.shape, vectors.dtype)

    now = datetime.now(timezone.utc)
    rows = [
        (
            c["phone_id"],
            c["section"],
            c["heading"],
            c["content"],
            len(c["content"]),
            [float(x) for x in vec],
            settings.embedding.model_name,
            now,
        )
        for c, vec in zip(chunks, vectors)
    ]

    with engine.cursor(dict_rows=False) as cur:
        cur.execute("TRUNCATE knowledge_chunks RESTART IDENTITY")
        psycopg2.extras.execute_values(cur, INSERT, rows, page_size=100)

    stats = engine.fetch_one(
        """
        SELECT count(*) AS chunks,
               count(DISTINCT phone_id) AS phones,
               count(embedding) AS embedded,
               round(avg(char_len)) AS avg_chars,
               array_length(min(embedding), 1) AS dim
        FROM knowledge_chunks
        """,
        audit=False,
    )
    log.info("indexed: %s", stats)
    return {k: (int(v) if v is not None else 0) for k, v in stats.items()}
