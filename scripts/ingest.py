"""Step 3 -- parse every saved page and load it into PostgreSQL.

Reads only from data/pages/, so it never touches the network. Safe to re-run.
"""
from __future__ import annotations

import sys

from backend.config.settings import settings
from backend.core.logging_setup import get_logger
from database import engine, loader
from scraper import catalog
from scraper.parser import parse

log = get_logger("scripts.ingest")


def main() -> int:
    entries = catalog.load()
    run_id = loader.start_run(notes=f"ingest of {len(entries)} catalogued phones")
    loaded, skipped = 0, []

    for i, e in enumerate(entries, 1):
        path = settings.paths.pages / f"{e.slug}.html"
        if not path.exists():
            log.warning("[%2d/%2d] %-30s no saved page -- skipped", i, len(entries), e.short_name)
            skipped.append(e.short_name)
            continue

        html = path.read_text(encoding="utf-8", errors="replace")
        page = parse(html, e.slug)
        if not page.specs:
            log.error("[%2d/%2d] %-30s parsed 0 specs -- skipped", i, len(entries), e.short_name)
            skipped.append(e.short_name)
            continue

        phone_id = loader.upsert_phone(
            e, page,
            local_path=str(path.relative_to(settings.paths.root)),
            sha256=_sha_of(path),
            page_bytes=path.stat().st_size,
            scrape_run_id=run_id,
        )
        n_specs = loader.replace_specifications(phone_id, page)
        attrs = loader.upsert_attributes(phone_id, page)
        filled = sum(1 for v in attrs.values() if v is not None)
        present, total = page.coverage()
        loaded += 1
        log.info(
            "[%2d/%2d] %-30s id=%-3d specs=%d (%d null)  attrs=%d/%d",
            i, len(entries), e.short_name, phone_id, n_specs,
            total - present, filled, len(attrs),
        )

    loader.finish_run(run_id, fetched=loaded, failed=len(skipped))

    log.info("-" * 78)
    log.info("loaded %d phones, skipped %d", loaded, len(skipped))
    if skipped:
        log.warning("skipped: %s", ", ".join(skipped))

    totals = engine.fetch_one(
        """
        SELECT (SELECT count(*) FROM phones)             AS phones,
               (SELECT count(*) FROM specifications)     AS specs,
               (SELECT count(*) FROM specifications WHERE spec_value IS NULL) AS spec_nulls,
               (SELECT count(*) FROM phone_attributes)   AS attr_rows
        """,
        audit=False,
    )
    log.info("database now holds: %s", totals)
    return 0 if loaded else 1


def _sha_of(path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
