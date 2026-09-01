"""One-command build: database -> scrape -> ingest -> index -> verify.

    python -m scripts.run_pipeline              # full build
    python -m scripts.run_pipeline --skip-scrape  # rebuild from saved pages
"""
from __future__ import annotations

import argparse
import sys
import time

from backend.core.logging_setup import get_logger
from database import engine

log = get_logger("scripts.pipeline")


def banner(step: str, n: int, total: int) -> None:
    log.info("")
    log.info("=" * 74)
    log.info("  STEP %d/%d  %s", n, total, step)
    log.info("=" * 74)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-scrape", action="store_true",
                    help="reuse the pages already in data/pages/")
    ap.add_argument("--reuse-catalog", action="store_true",
                    help="do not re-crawl the popularity ranking")
    args = ap.parse_args(argv)

    total, t0 = 5, time.time()

    banner("PostgreSQL schema", 1, total)
    from scripts import setup_db
    if setup_db.main() != 0:
        log.error("database setup failed; aborting")
        return 1

    banner("Scrape GSMArena", 2, total)
    if args.skip_scrape:
        log.info("skipped (--skip-scrape)")
    else:
        from scripts import scrape_pages
        rc = scrape_pages.main(refresh_catalog=not args.reuse_catalog)
        if rc != 0:
            log.warning("some pages could not be fetched; continuing with what "
                        "is on disk (run scripts.fill_missing later)")

    banner("Load into PostgreSQL", 3, total)
    from scripts import ingest
    if ingest.main() != 0:
        log.error("ingest produced no rows; aborting")
        return 1

    banner("Build the retrieval index", 4, total)
    from scripts import build_index
    if build_index.main() != 0:
        log.error("index build failed; aborting")
        return 1

    banner("Verify", 5, total)
    rc = verify()

    log.info("")
    log.info("pipeline finished in %.1fs -- start the service with: "
             "python -m api.main", time.time() - t0)
    return rc


def verify() -> int:
    from database import repository as repo
    from backend.llm.ollama_client import client

    stats = repo.corpus_stats()
    problems: list[str] = []

    log.info("corpus:")
    for k, v in stats.items():
        log.info("   %-18s %s", k, v)

    if not stats.get("phones"):
        problems.append("no phones loaded")
    if stats.get("chunks", 0) != stats.get("embedded", 0):
        problems.append("some chunks are missing embeddings")
    if not stats.get("spec_nulls"):
        problems.append("no NULL spec rows -- absence is not being recorded")

    log.info("series coverage:")
    for s in repo.series_breakdown():
        log.info("   %-16s %2d device(s)", s["series"], s["n"])

    row = engine.fetch_one(
        "SELECT cosine_similarity(ARRAY[1,0]::real[], ARRAY[1,0]::real[]) AS s",
        audit=False,
    )
    if not row or abs(float(row["s"]) - 1.0) > 1e-6:
        problems.append("cosine_similarity() is not behaving")
    else:
        log.info("in-database vector search: OK")

    ok, msg = client().available()
    log.info("llm: %s", msg)
    if not ok:
        problems.append(f"llm unavailable ({msg})")

    if problems:
        for p in problems:
            log.error("PROBLEM: %s", p)
        return 1
    log.info("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
