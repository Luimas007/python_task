"""Request and response models for the public API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000,
                          examples=["What are the camera specs of the Galaxy S23?"])
    session_key: str | None = Field(
        None, max_length=120,
        description="Groups related questions into one stored conversation.",
    )


class RefreshRequest(BaseModel):
    """Body for POST /api/knowledge/refresh. Every field is optional."""

    limit: int | None = Field(
        None, ge=1, le=100,
        description="How many phones to load. Defaults to SCRAPE_DEMO_COUNT (10).",
    )
    replace: bool = Field(
        True,
        description="Replace the knowledge base with exactly these phones. "
                    "Set false to add to what is already stored.",
    )
    offline: bool = Field(
        False,
        description="Skip the network entirely and rebuild from data/pages/. "
                    "The pipeline also switches to this on its own the first "
                    "time GSMArena denies access.",
    )


class Grounding(BaseModel):
    verdict: str
    numeric_claims: int
    supported: int
    unsupported: list[str] = []
    support_ratio: float
    evidence_chars: int = 0


class QueryResponse(BaseModel):
    run_id: str
    question: str
    answer: str
    intent: str
    agents_used: list[str]
    pipeline: list[str]
    devices: list[str]
    unresolved: list[str] = []
    grounding: Grounding | None = None
    extras: dict[str, Any] = {}
    latency_ms: float
    trace: list[dict[str, Any]] = []


class PhoneSummary(BaseModel):
    # `model_name` is a database column, not a pydantic reserved field.
    model_config = ConfigDict(protected_namespaces=())

    phone_id: int
    model_name: str
    short_name: str | None = None
    series: str | None = None
    tier: str | None = None
    is_flagship: bool = False
    popularity_rank: int | None = None
    display_size_in: float | None = None
    chipset: str | None = None
    battery_capacity_mah: int | None = None
    main_camera_mp: float | None = None
    price_usd: float | None = None


class HealthResponse(BaseModel):
    status: str
    database: dict[str, Any]
    llm: dict[str, Any]
    embedding: dict[str, Any]
    corpus: dict[str, Any]
