"""Hybrid retrieval, executed entirely inside PostgreSQL.

Dense pass  -- cosine_similarity(embedding, :qvec) over knowledge_chunks
Lexical pass -- pg_trgm similarity over the same rows, which rescues exact model
                names and part numbers that a 384-dim embedding blurs together

Scores are fused with a weighted sum. Both passes are ordinary SQL against the
one database; there is no external index and no network call.
"""
from __future__ import annotations

from dataclasses import dataclass
from backend.config.settings import settings
from backend.core.events import RunTrace
from backend.core.logging_setup import get_logger
from backend.core.protocols import VEC_SQL
from database import engine
from backend.rag import embedder

log = get_logger("rag.retriever")

DENSE_WEIGHT = 0.75
LEXICAL_WEIGHT = 0.25


@dataclass
class Hit:
    chunk_id: int
    phone_id: int | None
    model_name: str | None
    section: str
    heading: str
    content: str
    dense_score: float
    lexical_score: float
    score: float

    def cite(self) -> str:
        return f"[{self.model_name or 'unknown'} :: {self.section}]"


HYBRID_SQL = """
WITH scored AS (
    SELECT k.chunk_id,
           k.phone_id,
           p.model_name,
           k.section,
           k.heading,
           k.content,
           cosine_similarity(k.embedding, %(qvec)s::real[]) AS dense_score,
           similarity(k.content, %(qtext)s)                 AS lexical_score
    FROM knowledge_chunks k
    LEFT JOIN phones p USING (phone_id)
    WHERE k.embedding IS NOT NULL
      AND (%(phone_ids)s::int[] IS NULL OR k.phone_id = ANY(%(phone_ids)s::int[]))
      AND (%(sections)s::text[] IS NULL OR k.section = ANY(%(sections)s::text[]))
)
SELECT *,
       (%(w_dense)s * dense_score + %(w_lex)s * lexical_score) AS score
FROM scored
WHERE (%(w_dense)s * dense_score + %(w_lex)s * lexical_score) >= %(min_score)s
ORDER BY score DESC
LIMIT %(k)s
"""


def search(
    query: str,
    *,
    top_k: int | None = None,
    phone_ids: list[int] | None = None,
    sections: list[str] | None = None,
    min_score: float | None = None,
    trace: RunTrace | None = None,
    agent: str | None = None,
) -> list[Hit]:
    top_k = top_k or settings.rag.top_k
    min_score = settings.rag.min_score if min_score is None else min_score

    qvec = embedder.embed_query(query)
    params = {
        "qvec": qvec,
        "qtext": query,
        "phone_ids": phone_ids,
        "sections": sections,
        "w_dense": DENSE_WEIGHT,
        "w_lex": LEXICAL_WEIGHT,
        "min_score": min_score,
        "k": top_k,
    }
    rows = engine.fetch_all(
        HYBRID_SQL,
        params,
        trace=trace,
        agent=agent,
        operation="VECTOR SEARCH knowledge_chunks",
    )

    hits = [
        Hit(
            chunk_id=r["chunk_id"],
            phone_id=r["phone_id"],
            model_name=r["model_name"],
            section=r["section"],
            heading=r["heading"],
            content=r["content"],
            dense_score=round(float(r["dense_score"]), 4),
            lexical_score=round(float(r["lexical_score"] or 0.0), 4),
            score=round(float(r["score"]), 4),
        )
        for r in rows
    ]

    if trace is not None:
        trace.protocol(
            "rag.search",
            f"hybrid retrieval returned {len(hits)} chunk(s), best score "
            f"{hits[0].score if hits else 0:.3f}",
            agent=agent,
            protocol=VEC_SQL.id,
            detail={
                "query": query,
                "top_k": top_k,
                "phone_filter": phone_ids,
                "section_filter": sections,
                "weights": {"dense": DENSE_WEIGHT, "lexical": LEXICAL_WEIGHT},
                "hits": [
                    {
                        "model": h.model_name,
                        "section": h.section,
                        "score": h.score,
                        "dense": h.dense_score,
                        "lexical": h.lexical_score,
                    }
                    for h in hits
                ],
            },
        )
    log.debug("retrieved %d hits for %r", len(hits), query[:60])
    return hits


def as_context(hits: list[Hit], max_chars: int = 6000) -> str:
    """Render hits into a numbered, citable context block for the LLM."""
    parts, used = [], 0
    for i, h in enumerate(hits, 1):
        block = f"[{i}] {h.heading}\n{h.content}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)
