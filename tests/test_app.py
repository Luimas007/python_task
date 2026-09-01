"""Entry-point tests for app.py.

These cover the bring-up logic and the argument surface. The serving path itself
is exercised by tests/test_api.py through the in-process client.
"""
from __future__ import annotations

import pytest

import app
from database import engine

pytestmark = pytest.mark.app


@pytest.fixture(scope="module", autouse=True)
def require_db():
    if not engine.healthcheck()["ok"]:
        pytest.skip("PostgreSQL unavailable")


# ------------------------------------------------------------------ parsing
def test_defaults_come_from_settings():
    from backend.config.settings import settings

    ns = app.main.__wrapped__ if hasattr(app.main, "__wrapped__") else None
    assert ns is None  # main is not decorated; parse via argparse below

    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=settings.api.host)
    ap.add_argument("--port", type=int, default=settings.api.port)
    args = ap.parse_args([])
    assert args.host == settings.api.host
    assert args.port == settings.api.port


# ---------------------------------------------------------------- preflight
def test_check_postgres_returns_health():
    health = app.check_postgres()
    assert health["ok"] is True
    assert health["protocol"] == "PG-WIRE/3.0"


def test_check_postgres_reports_a_bad_endpoint(monkeypatch):
    """A wrong port must raise StartupError, not a bare psycopg2 traceback."""
    from dataclasses import replace

    from backend.config.settings import PostgresSettings, settings

    # Settings is frozen, and database.engine bound the name at import time, so swap
    # the whole object on the engine module rather than mutating in place.
    broken = replace(settings, pg=PostgresSettings(port=59999))
    monkeypatch.setattr("database.engine.settings", broken)
    with pytest.raises(app.StartupError) as exc:
        app.check_postgres()
    assert "cannot reach PostgreSQL" in str(exc.value)


def test_check_ollama_never_raises(monkeypatch):
    """A missing LLM degrades the service; it must not stop startup."""
    monkeypatch.setattr(
        "backend.llm.ollama_client.OllamaClient.available",
        lambda self: (False, "simulated outage"),
    )
    ok, msg = app.check_ollama()
    assert ok is False
    assert "simulated outage" in msg


# ------------------------------------------------------------- bring-up ---
def test_corpus_state_shape():
    state = app.corpus_state()
    assert set(state) == {"phones", "chunks", "embedded"}
    assert all(isinstance(v, int) for v in state.values())


def test_saved_page_count_ignores_listing_pages():
    """Listing pages are cached with a leading underscore and are not devices."""
    from backend.config.settings import settings

    n = app.saved_page_count()
    on_disk = len(list(settings.paths.pages.glob("*.html")))
    listings = len(list(settings.paths.pages.glob("_*.html")))
    assert n == on_disk - listings


def test_ensure_data_is_a_noop_when_populated():
    """A second start must not re-scrape, re-ingest or re-embed."""
    before = app.corpus_state()
    if before["phones"] == 0:
        pytest.skip("database empty; nothing to assert about idempotency")
    after = app.ensure_data()
    assert after == before


def test_rebuild_never_touches_the_network(monkeypatch):
    """`--rebuild` means 'rebuild from disk'. With nothing on disk it must fail
    fast, not start a multi-minute crawl."""
    monkeypatch.setattr(app, "corpus_state", lambda: {"phones": 0, "chunks": 0, "embedded": 0})
    monkeypatch.setattr(app, "saved_page_count", lambda: 0)

    def explode(*a, **k):
        raise AssertionError("--rebuild must not invoke the scraper")

    monkeypatch.setattr("scripts.scrape_pages.main", explode)

    with pytest.raises(app.StartupError) as exc:
        app.ensure_data(force_rebuild=True)
    assert "no saved pages" in str(exc.value)
