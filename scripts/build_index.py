"""Step 4 -- generate the RAG corpus from DB rows and embed it in place."""
from __future__ import annotations

import sys

from backend.core.logging_setup import get_logger
from database import engine
from backend.rag import indexer

log = get_logger("scripts.build_index")


def main() -> int:
    stats = indexer.rebuild()
    if not stats.get("chunks"):
        return 1

    sample = engine.fetch_all(
        """SELECT section, count(*) AS n, round(avg(char_len)) AS avg_chars
             FROM knowledge_chunks GROUP BY section ORDER BY n DESC LIMIT 15""",
        audit=False,
    )
    for s in sample:
        log.info("  %-28s %3d chunks  avg %4d chars", s["section"], s["n"], s["avg_chars"])
    log.info("index build complete: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
