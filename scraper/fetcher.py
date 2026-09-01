"""Polite HTTP fetcher that persists every page it downloads.

GSMArena fingerprints TLS clients, so requests go out through curl_cffi with a
real Chrome impersonation profile. Every response body is written to
data/pages/<slug>.html before parsing, giving the pipeline a local, replayable
copy of the source material.
"""
from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests as curl_requests

from backend.config.settings import settings
from backend.core.logging_setup import get_logger

log = get_logger("scraper.fetcher")

MAX_COOLDOWN = 240.0

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


@dataclass
class FetchResult:
    url: str
    path: Path
    html: str
    sha256: str
    bytes: int
    from_cache: bool


class FetchError(RuntimeError):
    pass


class PageFetcher:
    def __init__(self, use_cache: bool = True, pages_dir: Path | None = None) -> None:
        self.use_cache = use_cache
        self.dir = pages_dir or settings.paths.pages
        self.dir.mkdir(parents=True, exist_ok=True)
        self._session = curl_requests.Session(impersonate=settings.scraper.impersonate)
        self.stats = {"fetched": 0, "cached": 0, "failed": 0, "throttled": 0}
        # Grows when the origin returns 429; decays after clean responses.
        self._cooldown = 0.0

    # ------------------------------------------------------------------
    def path_for(self, cache_key: str) -> Path:
        return self.dir / f"{cache_key}.html"

    def get(self, url: str, cache_key: str) -> str:
        """Return page HTML, reading from the local copy when available."""
        return self.fetch(url, cache_key).html

    def fetch(self, url: str, cache_key: str) -> FetchResult:
        path = self.path_for(cache_key)
        if self.use_cache and path.exists() and path.stat().st_size > 2000:
            html = path.read_text(encoding="utf-8", errors="replace")
            self.stats["cached"] += 1
            log.debug("cache hit %s (%d bytes)", cache_key, len(html))
            return FetchResult(url, path, html, _sha(html), len(html.encode()), True)

        last_error: Exception | None = None
        for attempt in range(1, settings.scraper.max_retries + 1):
            throttled = False
            try:
                resp = self._session.get(
                    url, headers=HEADERS, timeout=settings.scraper.timeout
                )
                if resp.status_code == 200 and len(resp.text) > 2000:
                    html = resp.text
                    path.write_text(html, encoding="utf-8")
                    self.stats["fetched"] += 1
                    # One clean response means the throttle has lifted.
                    self._cooldown = max(0.0, self._cooldown - 5)
                    log.info("fetched %-46s %6d bytes -> %s",
                             cache_key, len(html), path.name)
                    return FetchResult(url, path, html, _sha(html), len(html.encode()), False)
                throttled = resp.status_code == 429
                if throttled:
                    # Honour the origin's own figure when it sends one; our
                    # guessed backoff is only a fallback.
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.strip().isdigit():
                        requested_cooldown = min(MAX_COOLDOWN, float(retry_after))
                        self._cooldown = max(self._cooldown, requested_cooldown)
                        log.info("origin asked for Retry-After: %ss", retry_after)
                last_error = FetchError(
                    f"HTTP {resp.status_code}, {len(resp.text)} bytes"
                )
            except Exception as exc:  # network / TLS / timeout
                last_error = exc

            if throttled:
                # GSMArena's limiter needs minutes, not seconds. Grow a sticky
                # cooldown that also slows every subsequent request.
                self.stats["throttled"] += 1
                self._cooldown = min(MAX_COOLDOWN, max(self._cooldown, 20) * 1.8)
                backoff = self._cooldown + random.uniform(0, 5)
                log.warning("attempt %d/%d THROTTLED (429) on %s; cooling down %.0fs",
                            attempt, settings.scraper.max_retries, cache_key, backoff)
            else:
                backoff = settings.scraper.delay_seconds * attempt + random.uniform(0, 1.5)
                log.warning("attempt %d/%d failed for %s (%s); retry in %.1fs",
                            attempt, settings.scraper.max_retries, cache_key,
                            last_error, backoff)
            time.sleep(backoff)

        self.stats["failed"] += 1
        raise FetchError(f"could not fetch {url}: {last_error}")

    def polite_pause(self) -> None:
        """Base crawl delay, extended while the origin is throttling us."""
        delay = settings.scraper.delay_seconds + random.uniform(0, 1.2) + self._cooldown
        time.sleep(delay)

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
