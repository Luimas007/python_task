"""Fetch only the catalogued phones whose page is not yet saved locally.

GSMArena rate-limits by IP, and once it starts returning 429 it stays that way
for a while regardless of TLS fingerprint. Hammering it is counterproductive, so
this script waits out the block rather than retrying tightly:

  * an optional long delay before the first request  (--wait)
  * generous, jittered spacing between requests      (--spacing)
  * several full passes over whatever is still missing (--rounds)

Safe to run repeatedly: anything already on disk is skipped, and the main
scrape/ingest pipeline is untouched.
"""
from __future__ import annotations

import argparse
import random
import sys
import time

from backend.config.settings import settings
from backend.core.logging_setup import get_logger
from scraper import catalog
from scraper.fetcher import FetchError, PageFetcher

log = get_logger("scripts.fill_missing")


def missing() -> list:
    return [
        e for e in catalog.load()
        if not (settings.paths.pages / f"{e.slug}.html").exists()
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=0,
                    help="seconds to wait before the first request")
    ap.add_argument("--spacing", type=int, default=75,
                    help="base seconds between requests")
    ap.add_argument("--rounds", type=int, default=6,
                    help="passes over the remaining gaps")
    args = ap.parse_args(argv)

    todo = missing()
    if not todo:
        log.info("nothing missing -- all %d catalogued pages are on disk",
                 len(catalog.load()))
        return 0
    log.info("%d page(s) missing: %s", len(todo),
             ", ".join(e.short_name for e in todo))

    if args.wait:
        log.info("waiting %ds for the origin rate limit to lapse ...", args.wait)
        time.sleep(args.wait)

    fetcher = PageFetcher()
    for rnd in range(1, args.rounds + 1):
        todo = missing()
        if not todo:
            break
        log.info("--- round %d/%d, %d remaining ---", rnd, args.rounds, len(todo))
        for e in todo:
            try:
                res = fetcher.fetch(e.url, cache_key=e.slug)
                log.info("OK  %-30s %d bytes", e.short_name, res.bytes)
            except FetchError as exc:
                log.warning("MISS %-30s %s", e.short_name, str(exc)[-60:])
            delay = args.spacing + random.uniform(0, args.spacing * 0.4)
            time.sleep(delay)
        if missing() and rnd < args.rounds:
            cool = args.spacing * 4
            log.info("round %d done, %d still missing; cooling %ds",
                     rnd, len(missing()), cool)
            time.sleep(cool)

    fetcher.close()
    left = missing()
    log.info("finished: %d/%d catalogued pages on disk",
             len(catalog.load()) - len(left), len(catalog.load()))
    if left:
        log.warning("still missing: %s", ", ".join(e.short_name for e in left))
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
