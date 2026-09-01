"""Knowledge-base refresh: scrape -> extract -> store -> index.

One generator, `refresh`, walks the whole flow and yields a progress event after
every step. The API relays those events to the browser, so the console can show
each phone appearing in the knowledge base as it lands.

    Start
      |
      v
    discover   crawl GSMArena's popularity ranking (or read the cached listing)
      |
      v
    for each phone:
        fetch   live, or from data/pages/ once access has been denied
        parse   HTML -> verbatim spec rows (absent facts become NULL)
        store   UPSERT into phones / specifications / phone_attributes
      |
      v
    index      rebuild knowledge_chunks + embeddings from the stored rows
      |
      v
    ready
"""
from __future__ import annotations

from typing import Any, Iterator

from backend.config.settings import settings
from backend.core.logging_setup import get_logger
from database import loader
from database import repository as repo
from scraper import catalog
from scraper.fetcher import FetchError, PageFetcher
from scraper.parser import parse

log = get_logger("scraper.pipeline")


def _event(phase: str, **kw: Any) -> dict[str, Any]:
    return {"phase": phase, **kw}


def refresh(
    limit: int | None = None,
    *,
    offline: bool = False,
    rebuild_index: bool = True,
    replace: bool = True,
) -> Iterator[dict[str, Any]]:
    """Refresh the knowledge base, yielding a progress event per step.

    `limit` caps how many phones are loaded -- 10 for the standard demo. The
    selection still runs the full popularity + flagship-quota algorithm, so the
    top N are the genuinely most popular devices, not an arbitrary slice.
    """
    limit = limit or settings.scraper.demo_count
    fetcher = PageFetcher(offline=offline)
    added: list[str] = []
    failed: list[dict[str, str]] = []

    try:
        # ---- 1. which phones -------------------------------------------
        yield _event("discover", message="Finding the most popular Samsung phones")
        try:
            entries = catalog.select(catalog.discover(fetcher), target=limit)
            catalog.save(entries)
        except FetchError as exc:
            yield _event("error", message=f"Could not build the catalogue: {exc}")
            return

        # With no live access, a phone we hold no page for can never be loaded;
        # drop it now rather than reporting a failure per device later.
        if fetcher.offline:
            usable = [e for e in entries if fetcher.has_local(e.slug)]
            if len(usable) < len(entries):
                skipped = len(entries) - len(usable)
                log.info("offline: %d catalogued phone(s) have no local page", skipped)
                pool = catalog.select(catalog.discover(fetcher), target=200)
                for e in pool:
                    if len(usable) >= limit:
                        break
                    if fetcher.has_local(e.slug) and e.slug not in {u.slug for u in usable}:
                        usable.append(e)
            entries = sorted(usable, key=lambda e: e.popularity_rank)[:limit]

        yield _event(
            "catalogue",
            total=len(entries),
            offline=fetcher.offline,
            source="local pages" if fetcher.offline else "gsmarena.com",
            phones=[{"name": e.short_name, "rank": e.popularity_rank} for e in entries],
            message=f"Selected the top {len(entries)} Samsung phones",
        )

        # A refresh replaces the knowledge base rather than adding to it, so
        # what the console lists afterwards is exactly what was just loaded.
        if replace:
            loader.clear_corpus()
            yield _event("cleared", message="Cleared the previous knowledge base")

        run_id = loader.start_run(notes=f"knowledge base refresh ({len(entries)} phones)")

        # ---- 2. each phone ----------------------------------------------
        for i, e in enumerate(entries, 1):
            yield _event("phone", status="scraping", index=i, total=len(entries),
                         name=e.model_name,
                         message=f"{e.model_name} - scraping")
            try:
                res = fetcher.fetch(e.url, cache_key=e.slug)
                page = parse(res.html, e.slug)
                if not page.specs:
                    raise FetchError("page parsed but contained no specifications")

                phone_id = loader.upsert_phone(
                    e, page,
                    local_path=str(res.path.relative_to(settings.paths.root)),
                    sha256=res.sha256,
                    page_bytes=res.bytes,
                    scrape_run_id=run_id,
                )
                n_specs = loader.replace_specifications(phone_id, page)
                attrs = loader.upsert_attributes(phone_id, page)

                present, total = page.coverage()
                added.append(e.model_name)
                yield _event(
                    "phone", status="added", index=i, total=len(entries),
                    name=e.model_name, phone_id=phone_id,
                    specs=n_specs, nulls=total - present,
                    attributes=sum(1 for v in attrs.values() if v is not None),
                    source=res.source,
                    message=f"{e.model_name} - added to the knowledge base",
                )
                if res.source == "network":
                    fetcher.polite_pause()

            except FetchError as exc:
                failed.append({"name": e.model_name, "error": str(exc)})
                yield _event("phone", status="failed", index=i, total=len(entries),
                             name=e.model_name, error=str(exc),
                             message=f"{e.model_name} - unavailable")

        loader.finish_run(run_id, fetched=len(added), failed=len(failed))

        # ---- 3. index ----------------------------------------------------
        if rebuild_index and added:
            yield _event("indexing", message="Building the searchable index")
            from backend.rag import indexer

            stats = indexer.rebuild()
            yield _event("indexed", message="Index ready", **stats)

        # ---- 4. done -----------------------------------------------------
        corpus = repo.corpus_stats()
        yield _event(
            "done",
            added=len(added),
            failed=len(failed),
            failures=failed,
            offline=fetcher.offline,
            block_reason=fetcher.block_reason,
            phones=int(corpus.get("phones") or 0),
            chunks=int(corpus.get("chunks") or 0),
            message=(f"Knowledge base ready - {len(added)} phone(s) loaded"
                     + (f", {len(failed)} unavailable" if failed else "")),
        )
    finally:
        fetcher.close()

