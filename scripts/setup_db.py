"""Step 1 -- create the database and apply the schema. Idempotent."""
from __future__ import annotations

import sys

from backend.core.logging_setup import get_logger
from database import engine

log = get_logger("scripts.setup_db")


def main() -> int:
    created = engine.create_database()
    log.info("database %s", "CREATED" if created else "already present")
    engine.apply_schema()

    health = engine.healthcheck()
    if not health["ok"]:
        log.error("healthcheck failed: %s", health["error"])
        return 1
    log.info("connected: %s (%s)", health["server"], health["endpoint"])

    tables = engine.fetch_all(
        """
        SELECT table_name,
               (SELECT count(*) FROM information_schema.columns c
                 WHERE c.table_name = t.table_name AND c.table_schema='public') AS cols
        FROM information_schema.tables t
        WHERE table_schema='public' AND table_type='BASE TABLE'
        ORDER BY table_name
        """,
        audit=False,
    )
    for t in tables:
        log.info("  table %-20s %2d columns", t["table_name"], t["cols"])

    fn = engine.fetch_one(
        "SELECT cosine_similarity(ARRAY[1,0,1]::real[], ARRAY[1,0,1]::real[]) AS s",
        audit=False,
    )
    log.info("  cosine_similarity(self) = %.4f (expect 1.0)", fn["s"])
    fn2 = engine.fetch_one(
        "SELECT cosine_similarity(ARRAY[1,0]::real[], ARRAY[0,1]::real[]) AS s",
        audit=False,
    )
    log.info("  cosine_similarity(orthogonal) = %.4f (expect 0.0)", fn2["s"])

    views = engine.fetch_all(
        "SELECT table_name FROM information_schema.views WHERE table_schema='public' ORDER BY 1",
        audit=False,
    )
    log.info("  views: %s", ", ".join(v["table_name"] for v in views))
    log.info("setup_db complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
