"""Named wire/message protocols surfaced to the operator console.

These are not decorative: each constant labels a real transport used by the
system, and every event on the bus is tagged with the one it travelled over.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Protocol:
    id: str
    label: str
    transport: str
    description: str


PG_WIRE = Protocol(
    id="PG-WIRE/3.0",
    label="PostgreSQL Frontend/Backend Protocol v3.0",
    transport="TCP 5432 (psycopg2 connection pool)",
    description="Every knowledge lookup. Parameterised SQL only; no external calls.",
)

ACP = Protocol(
    id="ACP/1.0",
    label="Agent Communication Protocol",
    transport="in-process typed message envelopes",
    description="Structured request/response envelopes passed between named agents.",
)

OLLAMA = Protocol(
    id="OLLAMA-HTTP/1.1",
    label="Ollama Local Inference API",
    transport="HTTP 127.0.0.1:11434 (loopback only)",
    description="Local open-source LLM. Receives DB-sourced context; never browses.",
)

WS_TRACE = Protocol(
    id="WS-TRACE/1.0",
    label="Live Trace Stream",
    transport="WebSocket /ws/trace",
    description="Pushes agent activity and protocol frames to the console.",
)

VEC_SQL = Protocol(
    id="VEC-SQL/1.0",
    label="In-Database Vector Search",
    transport="PL/pgSQL cosine_similarity(real[], real[]) over TCP 5432",
    description="Similarity is computed inside PostgreSQL; no external vector store.",
)

ALL_PROTOCOLS = [PG_WIRE, VEC_SQL, ACP, OLLAMA, WS_TRACE]

PROTOCOL_INDEX = {p.id: p for p in ALL_PROTOCOLS}
