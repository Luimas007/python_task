"""Agent-level tests, including the grounding guarantee.

The tests that need the LLM are marked `llm` and skip cleanly when Ollama is not
running, so the suite stays useful on a machine without the model pulled.
"""
from __future__ import annotations

import pytest

from agents.base import AgentContext, Envelope
from agents.nexus import orchestrator
from agents.sentinel import SentinelAgent
from backend.core.events import RunTrace
from database import engine
from backend.llm.ollama_client import client

pytestmark = pytest.mark.agents


@pytest.fixture(scope="module", autouse=True)
def require_db():
    if not engine.healthcheck()["ok"]:
        pytest.skip("PostgreSQL unavailable")
    row = engine.fetch_one("SELECT count(*) AS n FROM phones", audit=False)
    if not row or row["n"] == 0:
        pytest.skip("database is empty")


@pytest.fixture(scope="module")
def llm_ready():
    ok, msg = client().available()
    if not ok:
        pytest.skip(f"Ollama unavailable: {msg}")
    return True


# ------------------------------------------------------------------ roster
def test_roster_is_complete_and_well_formed():
    roster = orchestrator().roster()
    names = {a["name"] for a in roster}
    assert names == {"NEXUS", "ATLAS", "SPECTRA", "ORACLE",
                     "RANKER", "VERSUS", "CRITIC", "SENTINEL"}
    for a in roster:
        assert a["role"] and a["summary"] and a["accent"]
        assert a["capabilities"] and a["protocols"]


def test_only_designated_agents_touch_the_llm():
    """Retrieval agents must stay deterministic."""
    by_name = {a["name"]: a for a in orchestrator().roster()}
    for retriever_agent in ("SPECTRA", "ORACLE", "RANKER", "SENTINEL"):
        assert by_name[retriever_agent]["uses_llm"] is False


# ------------------------------------------------------------------- ATLAS
@pytest.mark.parametrize("question,expected", [
    ("What are the camera specs of the Galaxy S25 Ultra?", "spec_lookup"),
    ("How does the Galaxy S25 Ultra compare to the S24 Ultra?", "compare"),
    ("Which Samsung phone has the best battery life?", "ranking"),
    ("Write a review of the Galaxy S25 Ultra", "review"),
])
def test_atlas_classifies_intent(question, expected):
    ctx = AgentContext(trace=RunTrace(), question=question)
    reply = orchestrator().atlas.run(
        Envelope("TEST", "ATLAS", "analyse.query", {"question": question}), ctx
    )
    assert reply.payload["intent"] == expected


def test_atlas_selects_the_right_metric():
    ctx = AgentContext(trace=RunTrace(), question="best battery life")
    reply = orchestrator().atlas.run(
        Envelope("TEST", "ATLAS", "analyse.query",
                 {"question": "Which phone has the best battery life?"}), ctx
    )
    assert reply.payload["metric"] == "battery_endurance_hours"
    assert reply.payload["direction"] == "desc"


def test_atlas_flips_direction_for_lowest():
    ctx = AgentContext(trace=RunTrace(), question="lightest")
    reply = orchestrator().atlas.run(
        Envelope("TEST", "ATLAS", "analyse.query",
                 {"question": "Which Samsung phone is the lightest?"}), ctx
    )
    assert reply.payload["metric"] == "weight_g"
    assert reply.payload["direction"] == "asc"


# ----------------------------------------------------------------- SPECTRA
def test_spectra_returns_a_sheet_and_marks_nulls():
    from database import repository as repo

    target = repo.list_phones()[0]
    ctx = AgentContext(trace=RunTrace(), question="specs")
    reply = orchestrator().spectra.run(
        Envelope("TEST", "SPECTRA", "fetch.specs",
                 {"phone_ids": [target["phone_id"]]}), ctx
    )
    assert reply.payload["sheets"]
    rendered = reply.payload["rendered"][0]
    assert target["model_name"] in rendered
    # Missing facts must be labelled, not silently dropped.
    assert "NOT PUBLISHED" in rendered or reply.payload["null_fields"] == 0


# ---------------------------------------------------------------- SENTINEL
def test_sentinel_passes_grounded_claims():
    ctx = AgentContext(trace=RunTrace(), question="q")
    reply = SentinelAgent().run(
        Envelope("TEST", "SENTINEL", "audit.answer", {
            "answer": "The battery is 5000 mAh and the screen is 6.8 inches.",
            "evidence": "battery_capacity_mah=5000 display_size_in=6.8",
        }), ctx
    )
    assert reply.payload["verdict"] == "grounded"
    assert reply.payload["unsupported"] == []


def test_sentinel_flags_invented_numbers():
    ctx = AgentContext(trace=RunTrace(), question="q")
    reply = SentinelAgent().run(
        Envelope("TEST", "SENTINEL", "audit.answer", {
            "answer": "The battery is 7400 mAh and it charges at 240W.",
            "evidence": "battery_capacity_mah=5000 charging_wired_w=45",
        }), ctx
    )
    assert reply.payload["verdict"] != "grounded"
    assert "7400" in reply.payload["unsupported"]
    assert "240" in reply.payload["unsupported"]


def test_sentinel_ignores_years_and_list_markers():
    ctx = AgentContext(trace=RunTrace(), question="q")
    reply = SentinelAgent().run(
        Envelope("TEST", "SENTINEL", "audit.answer", {
            "answer": "Released in 2023. 1. First point 2. Second point.",
            "evidence": "nothing numeric here",
        }), ctx
    )
    assert reply.payload["numeric_claims"] == 0


# ------------------------------------------------------------ full journey
@pytest.mark.llm
def test_spec_lookup_end_to_end(llm_ready):
    from database import repository as repo

    loaded = repo.list_phones()[0]["model_name"]
    r = orchestrator().answer(
        f"What are the camera specs of the {loaded}?", RunTrace()
    )
    assert r["intent"] == "spec_lookup"
    assert "SPECTRA" in r["agents_used"]
    assert loaded in r["devices"]
    assert len(r["answer"]) > 40
    assert r["grounding"]["verdict"] in ("grounded", "partially-grounded")


@pytest.mark.llm
def test_comparison_end_to_end(llm_ready):
    from database import repository as repo

    phones = repo.list_phones()
    if len(phones) < 2:
        pytest.skip("need two phones to compare")
    a, b = phones[0]["model_name"], phones[1]["model_name"]
    r = orchestrator().answer(
        f"How does the {a} compare to the {b} in terms of performance?",
        RunTrace(),
    )
    assert r["intent"] == "compare"
    assert "VERSUS" in r["agents_used"]
    assert len(r["devices"]) == 2
    assert r["extras"]["deltas"], "no deltas computed"


@pytest.mark.llm
def test_ranking_end_to_end(llm_ready):
    r = orchestrator().answer("Which Samsung phone has the best battery life?", RunTrace())
    assert r["intent"] == "ranking"
    assert "RANKER" in r["agents_used"]
    ranking = r["extras"]["ranking"]
    assert ranking["column"] == "battery_endurance_hours"
    assert ranking["rows"]


@pytest.mark.llm
def test_review_end_to_end(llm_ready):
    from database import repository as repo

    loaded = repo.list_phones()[0]["model_name"]
    r = orchestrator().answer(f"Write a review of the {loaded}", RunTrace())
    assert r["intent"] == "review"
    assert "CRITIC" in r["agents_used"]
    assert len(r["answer"]) > 200


@pytest.mark.llm
def test_unknown_device_is_declared_not_invented(llm_ready):
    r = orchestrator().answer(
        "What is the battery capacity of the Galaxy S99 Omega?", RunTrace()
    )
    assert r["unresolved"], "an unknown model was not reported as unresolved"
    assert "not in this database" in r["answer"].lower()


# ------------------------------------------------------------------- trace
def test_run_emits_a_full_protocol_trace():
    trace = RunTrace()
    ctx = AgentContext(trace=trace, question="test")
    orchestrator().atlas.run(
        Envelope("NEXUS", "ATLAS", "analyse.query", {"question": "Galaxy S23 specs"}), ctx
    )
    events = trace.events()
    assert events
    types = {e["type"] for e in events}
    assert "acp.message" in types, "no agent-to-agent message recorded"
    assert "agent.start" in types and "agent.end" in types
    assert any(e.get("protocol") == "PG-WIRE/3.0" for e in events), \
        "no database round-trip recorded"


def test_trace_events_are_json_serialisable():
    """A non-encodable detail would drop every WebSocket subscriber."""
    import json

    trace = RunTrace()
    ctx = AgentContext(trace=trace, question="q")
    orchestrator().ranker.run(
        Envelope("NEXUS", "RANKER", "rank.devices",
                 {"metric": "battery_capacity_mah"}), ctx
    )
    for e in trace.events():
        json.dumps(e)   # raises TypeError if a Decimal or datetime leaked through
