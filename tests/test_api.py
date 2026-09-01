"""API surface tests, driven through FastAPI's in-process test client."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.main import app
from database import engine
from backend.llm.ollama_client import client as llm_client

pytestmark = pytest.mark.api


@pytest.fixture(scope="module")
def api():
    if not engine.healthcheck()["ok"]:
        pytest.skip("PostgreSQL unavailable")
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def llm_ready():
    ok, msg = llm_client().available()
    if not ok:
        pytest.skip(f"Ollama unavailable: {msg}")
    return True


# -------------------------------------------------------------------- meta
def test_health(api):
    r = api.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert d["database"]["ok"] is True
    assert d["database"]["protocol"] == "PG-WIRE/3.0"
    assert d["corpus"]["phones"] > 0
    assert d["embedding"]["dim"] == 384


def test_agents_endpoint(api):
    d = api.get("/api/agents").json()
    assert len(d["agents"]) == 8
    assert {a["name"] for a in d["agents"]} >= {"NEXUS", "SPECTRA", "SENTINEL"}


def test_protocols_endpoint(api):
    d = api.get("/api/protocols").json()
    ids = {p["id"] for p in d["protocols"]}
    assert {"PG-WIRE/3.0", "ACP/1.0", "OLLAMA-HTTP/1.1", "VEC-SQL/1.0"} <= ids
    for p in d["protocols"]:
        assert p["transport"] and p["description"]


def test_console_is_served(api):
    r = api.get("/")
    assert r.status_code == 200
    assert "Samsung Phone Intelligence" in r.text


# ----------------------------------------------------------------- catalog
def test_phones_listing(api):
    d = api.get("/api/phones").json()
    assert d["count"] > 0
    assert len(d["phones"]) == d["count"]
    first = d["phones"][0]
    assert "model_name" in first and "phone_id" in first
    json.dumps(d)   # must be fully JSON-clean


def test_phone_detail_and_404(api):
    pid = api.get("/api/phones").json()["phones"][0]["phone_id"]
    d = api.get(f"/api/phones/{pid}").json()
    assert d["phone"]["model_name"]
    assert d["specs_by_category"]
    assert api.get("/api/phones/999999").status_code == 404


def test_rankings_endpoint(api):
    d = api.get("/api/rankings?metric=battery_capacity_mah&limit=5").json()
    assert d["column"] == "battery_capacity_mah"
    assert len(d["rows"]) <= 5
    assert all(r["metric_value"] is not None for r in d["rows"])


def test_rankings_rejects_unknown_metric(api):
    assert api.get("/api/rankings?metric=model_name").status_code == 400
    assert api.get("/api/rankings?metric=;DROP TABLE phones").status_code == 400


def test_rankable_metrics_listed(api):
    d = api.get("/api/rankable").json()
    assert "battery_endurance_hours" in d["metrics"]


# --------------------------------------------------------------------- ask
def test_ask_rejects_empty_question(api):
    # Pydantic validation -> 422, the correct code for a malformed body.
    assert api.post("/api/ask", json={"question": ""}).status_code == 422
    assert api.post("/api/ask", json={}).status_code == 422


@pytest.mark.llm
def test_ask_returns_grounded_answer_with_trace(api, llm_ready):
    r = api.post("/api/ask", json={
        "question": "What is the battery capacity of the Galaxy S23?",
        "session_key": "pytest",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["answer"]
    assert d["intent"] == "spec_lookup"
    assert "SPECTRA" in d["agents_used"]
    assert d["grounding"]["verdict"] in ("grounded", "partially-grounded")

    # The trace must carry real protocol frames, not just agent chatter.
    protocols = {e.get("protocol") for e in d["trace"]}
    assert "PG-WIRE/3.0" in protocols
    assert "ACP/1.0" in protocols
    assert any(e["type"] == "acp.message" for e in d["trace"])
    json.dumps(d["trace"])


@pytest.mark.llm
def test_conversation_is_persisted(api, llm_ready):
    api.post("/api/ask", json={"question": "Screen size of the Galaxy S23?",
                               "session_key": "pytest-history"})
    d = api.get("/api/history?session_key=pytest-history").json()
    roles = [m["role"] for m in d["messages"]]
    assert "user" in roles and "assistant" in roles


def test_query_log_records_database_traffic(api):
    api.get("/api/phones")
    d = api.get("/api/query-log?limit=10").json()
    assert d["entries"], "no audit rows written"
    assert all(e["protocol"] == "PG-WIRE/3.0" for e in d["entries"])


# --------------------------------------------------------------- websocket
def test_trace_websocket_streams_events(api):
    with api.websocket_connect("/ws/trace") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "trace.hello"
        assert isinstance(hello["history"], list)

        # Emitting from this thread must reach the subscriber.
        from backend.core.events import RunTrace
        from backend.core.protocols import PG_WIRE

        RunTrace().protocol(
            "db.query", "unit-test frame", protocol=PG_WIRE.id,
            detail={"sql": "SELECT 1"},
        )
        event = ws.receive_json()
        assert event["summary"] == "unit-test frame"
        assert event["protocol"] == "PG-WIRE/3.0"
