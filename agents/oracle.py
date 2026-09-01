"""ORACLE -- Semantic Retrieval.

Runs the RAG pass: embeds the question and searches knowledge_chunks with the
in-database hybrid scorer. Used when the question is open-ended, or alongside
SPECTRA to pick up phrasing that does not map onto a typed column.
"""
from __future__ import annotations

from agents.base import Agent, AgentCard, AgentContext, Envelope
from backend.core.logging_setup import get_logger
from backend.rag import retriever

log = get_logger("agents.oracle")


class OracleAgent(Agent):
    card = AgentCard(
        name="ORACLE",
        role="Semantic Retrieval",
        summary="Embeds the question and runs hybrid dense + trigram search "
                "inside PostgreSQL over the chunked knowledge base.",
        icon="search",
        accent="#14b8a6",
        capabilities=(
            "MiniLM query embedding",
            "in-database cosine similarity",
            "lexical trigram fusion",
        ),
        protocols=("ACP/1.0", "VEC-SQL/1.0", "PG-WIRE/3.0"),
        reads_database=True,
        uses_llm=False,
    )

    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        query = msg.payload.get("query", ctx.question)
        phone_ids = msg.payload.get("phone_ids") or None
        top_k = int(msg.payload.get("top_k", 8))

        hits = retriever.search(
            query,
            top_k=top_k,
            phone_ids=phone_ids,
            trace=ctx.trace,
            agent=self.name,
        )
        context = retriever.as_context(hits)
        ctx.note("rag_hits", hits)

        ctx.trace.agent(
            "agent.finding",
            f"{len(hits)} chunk(s) above threshold"
            + (f"; top match {hits[0].model_name} :: {hits[0].section} "
               f"@ {hits[0].score:.3f}" if hits else "; nothing passed the threshold"),
            agent=self.name,
            detail={
                "hits": [
                    {"model": h.model_name, "section": h.section, "score": h.score}
                    for h in hits
                ],
                "context_chars": len(context),
            },
        )
        return msg.reply(
            "retrieval.result",
            {
                "context": context,
                "citations": [
                    {
                        "n": i,
                        "model": h.model_name,
                        "section": h.section,
                        "score": h.score,
                        "chunk_id": h.chunk_id,
                    }
                    for i, h in enumerate(hits, 1)
                ],
                "hit_count": len(hits),
            },
        )
