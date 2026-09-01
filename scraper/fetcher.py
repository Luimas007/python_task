"""Fetch source pages, with an automatic fallback to the local copies.

GSMArena fingerprints TLS clients, so live requests go out through curl_cffi
with a real Chrome impersonation profile, and every response body is written to
`data/pages/<slug>.html`.

Access control
--------------
GSMArena rate-limits by IP and, once it starts refusing, keeps refusing for a
long while regardless of TLS fingerprint -- retrying only extends the block. So
the first access denial (HTTP 429/403/503) flips this fetcher into **offline
mode** for the rest of its life: no further network calls are attempted, and
every page is served from the local copy in `data/pages/`.

Those local pages are the same bytes a live fetch would have written, so the
parsing pipeline downstream cannot tell the difference.
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

# Status codes that mean "you are not welcome right now". Any one of them ends
# live fetching for this run.
ACCESS_DENIED_CODES = {401, 403, 429, 503}

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
    source: str          # "network" | "cache" | "fallback"

    @property
    def from_cache(self) -> bool:
        return self.source != "network"


class FetchError(RuntimeError):
    """No copy of the page could be obtained, live or local."""


class PageFetcher:
    def __init__(self, use_cache: bool = True, pages_dir: Path | None = None,
                 offline: bool = False) -> None:
        self.use_cache = use_cache
        self.dir = pages_dir or settings.paths.pages
        self.dir.mkdir(parents=True, exist_ok=True)
        self._session: curl_requests.Session | None = None
        self.stats = {"network": 0, "cache": 0, "fallback": 0, "missing": 0}

        # Set on the first access denial; never reset. `offline=True` starts
        # here, which is how tests and demos avoid touching the network at all.
        self.offline = offline
        self.block_reason: str | None = "started in offline mode" if offline else None

    # ------------------------------------------------------------------
    @property
    def session(self) -> curl_requests.Session:
        if self._session is None:
            self._session = curl_requests.Session(
                impersonate=settings.scraper.impersonate
            )
        return self._session

    def path_for(self, cache_key: str) -> Path:
        return self.dir / f"{cache_key}.html"

    def has_local(self, cache_key: str) -> bool:
        p = self.path_for(cache_key)
        return p.exists() and p.stat().st_size > 2000

    def get(self, url: str, cache_key: str) -> str:
        return self.fetch(url, cache_key).html

    # ------------------------------------------------------------------
    def fetch(self, url: str, cache_key: str) -> FetchResult:
        """Return the page, preferring a local copy, then the network."""
        path = self.path_for(cache_key)

        if self.use_cache and self.has_local(cache_key):
            self.stats["cache"] += 1
            return self._from_disk(url, path, "cache")

        if self.offline:
            return self._fallback(url, cache_key, path)

        try:
            return self._from_network(url, cache_key, path)
        except _AccessDenied as denial:
            self._go_offline(str(denial))
            return self._fallback(url, cache_key, path)
        except FetchError:
            # A transient failure (timeout, DNS) is not an access denial, so we
            # stay online for the next page -- but still use a local copy now.
            if self.has_local(cache_key):
                log.warning("%s failed live; using the local copy", cache_key)
                self.stats["fallback"] += 1
                return self._from_disk(url, path, "fallback")
            raise

    # ------------------------------------------------------------------
    def _from_network(self, url: str, cache_key: str, path: Path) -> FetchResult:
        last_error: Exception | None = None
        for attempt in range(1, settings.scraper.max_retries + 1):
            try:
                resp = self.session.get(
                    url, headers=HEADERS, timeout=settings.scraper.timeout
                )
            except Exception as exc:                     # network / TLS / timeout
                last_error = exc
            else:
                if resp.status_code in ACCESS_DENIED_CODES:
                    # Do not retry. Retrying is what keeps the block alive.
                    raise _AccessDenied(
                        f"HTTP {resp.status_code} from {url.rsplit('/', 1)[-1]}"
                    )
                if resp.status_code == 200 and len(resp.text) > 2000:
                    html = resp.text
                    path.write_text(html, encoding="utf-8")
                    self.stats["network"] += 1
                    log.info("fetched %-46s %6d bytes", cache_key, len(html))
                    return FetchResult(url, path, html, _sha(html),
                                       len(html.encode()), "network")
                last_error = FetchError(
                    f"HTTP {resp.status_code}, {len(resp.text)} bytes"
                )

            if attempt < settings.scraper.max_retries:
                backoff = settings.scraper.delay_seconds * attempt + random.uniform(0, 1)
                log.warning("attempt %d/%d failed for %s (%s); retry in %.1fs",
                            attempt, settings.scraper.max_retries, cache_key,
                            last_error, backoff)
                time.sleep(backoff)

        raise FetchError(f"could not fetch {url}: {last_error}")

    def _fallback(self, url: str, cache_key: str, path: Path) -> FetchResult:
        if self.has_local(cache_key):
            self.stats["fallback"] += 1
            log.info("offline: serving %s from the local page", cache_key)
            return self._from_disk(url, path, "fallback")
        self.stats["missing"] += 1
        raise FetchError(
            f"{cache_key} is not available live ({self.block_reason}) and there "
            f"is no local copy at {path}"
        )

    def _from_disk(self, url: str, path: Path, source: str) -> FetchResult:
        html = path.read_text(encoding="utf-8", errors="replace")
        return FetchResult(url, path, html, _sha(html), len(html.encode()), source)

    def _go_offline(self, reason: str) -> None:
        if self.offline:
            return
        self.offline = True
        self.block_reason = reason
        log.warning("=" * 66)
        log.warning("ACCESS DENIED BY GSMARENA: %s", reason)
        log.warning("switching to the local pages in %s for the rest of this run", self.dir)
        log.warning("(no further requests will be sent -- retrying only extends the block)")
        log.warning("=" * 66)

    # ------------------------------------------------------------------
    def polite_pause(self) -> None:
        """Crawl delay between live requests. Free when serving from disk."""
        if not self.offline:
            time.sleep(settings.scraper.delay_seconds + random.uniform(0, 1))

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None


class _AccessDenied(RuntimeError):
    """The origin refused us. Internal: callers see the offline switch instead."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
