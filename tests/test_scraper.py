"""Fetcher fallback and refresh-pipeline tests.

The behaviour under test is the one that matters when GSMArena says no: the
first access denial must end live fetching for that run and hand over to the
locally saved pages, without a single retry.
"""
from __future__ import annotations

import pytest

from backend.config.settings import settings
from scraper import pipeline
from scraper.fetcher import ACCESS_DENIED_CODES, FetchError, PageFetcher

pytestmark = pytest.mark.scraper


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}


class CountingSession:
    """Stands in for curl_cffi, recording how many requests were attempted."""

    def __init__(self, status: int = 429):
        self.status = status
        self.calls = 0

    def get(self, url, **kw):
        self.calls += 1
        return FakeResponse(self.status, "blocked")

    def close(self):
        pass


@pytest.fixture
def saved_slug() -> str:
    pages = [p for p in settings.paths.pages.glob("*.html")
             if not p.name.startswith("_") and p.stat().st_size > 2000]
    if not pages:
        pytest.skip("no saved pages on disk")
    return pages[0].stem


# ------------------------------------------------------------------ denial
@pytest.mark.parametrize("code", sorted(ACCESS_DENIED_CODES))
def test_access_denial_switches_to_local_pages(code, saved_slug):
    f = PageFetcher(use_cache=False)
    f._session = CountingSession(code)

    res = f.fetch("https://www.gsmarena.com/x.php", cache_key=saved_slug)

    assert res.source == "fallback"
    assert f.offline is True
    assert str(code) in f.block_reason
    assert len(res.html) > 2000


def test_denial_is_never_retried(saved_slug):
    """Retrying a block only extends it, so one attempt is all we make."""
    f = PageFetcher(use_cache=False)
    session = CountingSession(429)
    f._session = session

    f.fetch("https://www.gsmarena.com/x.php", cache_key=saved_slug)
    assert session.calls == 1, f"made {session.calls} requests to a blocked origin"


def test_offline_sticks_for_every_later_page(saved_slug):
    f = PageFetcher(use_cache=False)
    session = CountingSession(403)
    f._session = session

    for _ in range(4):
        f.fetch("https://www.gsmarena.com/x.php", cache_key=saved_slug)

    # Only the first page tried the network; the rest went straight to disk.
    assert session.calls == 1
    assert f.stats["fallback"] == 4


def test_transient_error_does_not_trigger_offline(saved_slug):
    """A timeout is not an access denial: stay online for the next page."""
    class Boom:
        calls = 0

        def get(self, url, **kw):
            Boom.calls += 1
            raise TimeoutError("connection timed out")

        def close(self):
            pass

    f = PageFetcher(use_cache=False)
    f._session = Boom()
    res = f.fetch("https://www.gsmarena.com/x.php", cache_key=saved_slug)

    assert res.source == "fallback"      # used the local copy for this page
    assert f.offline is False            # but did not give up on the network
    assert Boom.calls == settings.scraper.max_retries


def test_missing_page_with_no_local_copy_raises():
    f = PageFetcher(use_cache=False, offline=True)
    with pytest.raises(FetchError) as exc:
        f.fetch("https://www.gsmarena.com/nope.php", cache_key="does_not_exist_xyz")
    assert "no local copy" in str(exc.value)


def test_offline_fetcher_never_builds_a_session():
    """Starting offline must not open a network session at all."""
    f = PageFetcher(offline=True)
    assert f._session is None
    assert f.block_reason == "started in offline mode"


# ----------------------------------------------------------------- caching
def test_local_copy_is_preferred_over_the_network(saved_slug):
    f = PageFetcher(use_cache=True)
    session = CountingSession(200)
    f._session = session

    res = f.fetch("https://www.gsmarena.com/x.php", cache_key=saved_slug)
    assert res.source == "cache"
    assert session.calls == 0, "hit the network despite holding a local copy"


# ---------------------------------------------------------------- pipeline
@pytest.mark.slow
def test_refresh_emits_a_full_progress_stream():
    from database import engine

    if not engine.healthcheck()["ok"]:
        pytest.skip("PostgreSQL unavailable")

    # replace=False: a test run must never wipe the knowledge base the user
    # just loaded. rebuild_index=False keeps it quick.
    events = list(pipeline.refresh(limit=2, offline=True,
                                   replace=False, rebuild_index=False))
    phases = [e["phase"] for e in events]

    assert phases[0] == "discover"
    assert "catalogue" in phases
    assert "cleared" not in phases, "replace=False must not clear the corpus"
    assert phases[-1] == "done"
    assert every_phone_has_both_states(events)

    done = events[-1]
    assert done["added"] >= 1
    assert done["phones"] >= 1
    # Offline means the network was never consulted.
    assert done["offline"] is True


def every_phone_has_both_states(events) -> bool:
    """Each device must report `scraping` before `added` or `failed`."""
    seen: dict[str, list[str]] = {}
    for e in events:
        if e["phase"] == "phone":
            seen.setdefault(e["name"], []).append(e["status"])
    return all(
        states[0] == "scraping" and states[-1] in ("added", "failed")
        for states in seen.values()
    )
