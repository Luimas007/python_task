"""Step 2 -- pick the phones, then download and persist every source page."""
from __future__ import annotations

import json
import sys
import time

from backend.config.settings import settings
from backend.core.logging_setup import get_logger
from scraper import catalog
from scraper.fetcher import FetchError, PageFetcher

log = get_logger("scripts.scrape")


def main(refresh_catalog: bool = True) -> int:
    fetcher = PageFetcher()
    if refresh_catalog or not settings.paths.catalog.exists():
        entries = catalog.select(catalog.discover(fetcher))
        catalog.save(entries)
    else:
        entries = catalog.load()
        log.info("re-using catalog of %d phones", len(entries))

    manifest, failures = [], []
    t0 = time.time()
    for i, e in enumerate(entries, 1):
        try:
            res = fetcher.fetch(e.url, cache_key=e.slug)
            manifest.append(
                {
                    "slug": e.slug,
                    "short_name": e.short_name,
                    "url": e.url,
                    "path": str(res.path.relative_to(settings.paths.root)),
                    "sha256": res.sha256,
                    "bytes": res.bytes,
                    "from_cache": res.from_cache,
                }
            )
            log.info("[%2d/%2d] %-34s %s", i, len(entries), e.short_name,
                     "cached" if res.from_cache else "downloaded")
            if not res.from_cache:
                fetcher.polite_pause()
        except FetchError as exc:
            log.error("[%2d/%2d] %-34s FAILED: %s", i, len(entries), e.short_name, exc)
            failures.append({"slug": e.slug, "error": str(exc)})

    fetcher.close()
    (settings.paths.data / "page_manifest.json").write_text(
        json.dumps({"pages": manifest, "failures": failures}, indent=2), encoding="utf-8"
    )
    log.info(
        "done in %.1fs | downloaded=%d cached=%d failed=%d | pages in %s",
        time.time() - t0, fetcher.stats["fetched"], fetcher.stats["cached"],
        len(failures), settings.paths.pages,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(refresh_catalog="--reuse" not in sys.argv))
