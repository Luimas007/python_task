"""Samsung Phone Query and Review System -- single entry point.

    python app.py

Runs preflight checks, brings the database up to a usable state if it is not
already, then serves the chatbot console and API on http://127.0.0.1:8000.

It is safe to run repeatedly. Work already done is detected and skipped, so a
second start takes a couple of seconds.

Options
    --port N        serve on a different port
    --host H        bind a different interface
    --scrape        refresh the knowledge base before starting
    --limit N       how many phones to load when refreshing (default 10)
    --rebuild       re-ingest saved pages and rebuild the vector index
    --reset         drop every row and rebuild from the saved pages
    --no-browser    do not open a browser window
    --check         run the preflight checks and exit
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser

from backend.config.settings import settings
from backend.core.logging_setup import get_logger, setup_logging

setup_logging()
log = get_logger("app")

BANNER = r"""
  ____
 / ___|  __ _ _ __ ___  ___ _   _ _ __   __ _
 \___ \ / _` | '_ ` _ \/ __| | | | '_ \ / _` |    Phone Query & Review System
  ___) | (_| | | | | | \__ \ |_| | | | | (_| |    multi-agent | RAG | PostgreSQL
 |____/ \__,_|_| |_| |_|___/\__,_|_| |_|\__, |
                                        |___/
"""


class StartupError(RuntimeError):
    """A prerequisite is missing and the operator has to fix it."""


# ---------------------------------------------------------------- preflight
def check_postgres() -> dict:
    from database import engine

    try:
        engine.create_database()          # no-op when it already exists
    except Exception as exc:
        raise StartupError(
            f"cannot reach PostgreSQL at {settings.pg.endpoint}: {exc}\n"
            "  - is the PostgreSQL service running?\n"
            "  - do PG_USER / PG_PASSWORD in .env match your server?"
        ) from exc

    engine.apply_schema()                 # idempotent
    health = engine.healthcheck()
    if not health["ok"]:
        raise StartupError(f"database unusable: {health.get('error')}")
    return health


def check_ollama() -> tuple[bool, str]:
    from backend.llm.ollama_client import client

    ok, msg = client().available()
    if not ok:
        log.warning("LLM unavailable: %s", msg)
        log.warning("  the API and database still work, but answers will be")
        log.warning("  returned as raw database rows instead of prose.")
        log.warning("  fix with:  ollama serve   and   ollama pull %s",
                    settings.llm.model)
    return ok, msg


def check_embedder() -> bool:
    """Load the embedding model now so the first question is not slow."""
    try:
        from backend.rag import embedder

        t0 = time.perf_counter()
        dim = embedder.dimension()
        log.info("embedding model ready: %s (%d-dim, %s) in %.1fs",
                 settings.embedding.model_name, dim, settings.embedding.device,
                 time.perf_counter() - t0)
        return True
    except Exception as exc:
        log.warning("embedding model unavailable (%s); semantic search will fail "
                    "but structured lookups still work", exc)
        return False


# ------------------------------------------------------------- data bring-up
def corpus_state() -> dict:
    from database import repository as repo

    stats = repo.corpus_stats()
    return {
        "phones": int(stats.get("phones") or 0),
        "chunks": int(stats.get("chunks") or 0),
        "embedded": int(stats.get("embedded") or 0),
    }


def saved_page_count() -> int:
    return len([p for p in settings.paths.pages.glob("*.html")
                if not p.name.startswith("_")])


def ensure_data(force_scrape: bool = False, force_rebuild: bool = False,
                limit: int | None = None) -> dict:
    """Bring the knowledge base up to a state the agents can serve from.

    The console can refresh on demand, so startup only does work when there is
    nothing to serve at all -- or when the operator asked for it explicitly.
    """
    state = corpus_state()
    pages = saved_page_count()

    if state["phones"] and not (force_scrape or force_rebuild):
        # Already populated. Repair the index if it went missing, then leave it.
        if state["chunks"] == 0 or state["chunks"] != state["embedded"]:
            log.info("rebuilding the retrieval index ...")
            from backend.rag import indexer

            indexer.rebuild()
            state = corpus_state()
        return state

    # `--rebuild` means "rebuild from what is on disk", so it must never reach
    # for the network. Everything else may, falling back to the local pages the
    # moment GSMArena denies access.
    if force_rebuild and pages == 0 and state["phones"] == 0:
        raise StartupError(
            "the knowledge base is empty and there are no saved pages to load.\n"
            "  run:  python app.py --scrape"
        )

    from scraper import pipeline

    offline = force_rebuild
    log.info("loading the knowledge base%s ...",
             " from saved pages" if offline else "")
    final: dict = {}
    for event in pipeline.refresh(limit=limit, offline=offline):
        final = event
        if event["phase"] == "phone" and event["status"] != "scraping":
            log.info("   %-34s %s", event["name"], event["status"])
        elif event["phase"] != "phone":
            log.info("   %s", event.get("message", event["phase"]))

    if final.get("offline") and not offline:
        log.warning("GSMArena denied access (%s); finished from the local pages",
                    final.get("block_reason"))

    state = corpus_state()
    if state["phones"] == 0:
        raise StartupError(
            "no phones could be loaded.\n"
            "  there are no saved pages in data/pages/ and GSMArena is unreachable.\n"
            "  you can also load the knowledge base from the console once it starts."
        )
    return state


def reset_database() -> None:
    from database import engine

    log.warning("--reset: deleting every row (saved pages on disk are kept)")
    engine.execute(
        "TRUNCATE phones, specifications, phone_attributes, knowledge_chunks, "
        "query_log, conversations, messages, scrape_runs RESTART IDENTITY CASCADE"
    )


# -------------------------------------------------------------------- serve
def open_browser_when_ready(url: str, timeout: float = 30.0) -> None:
    """Poll the health endpoint in the background, then open a window."""
    import httpx

    def worker() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if httpx.get(f"{url}/api/health", timeout=2).status_code == 200:
                    webbrowser.open(url)
                    return
            except Exception:
                time.sleep(0.5)
        log.debug("server did not become ready in time; not opening a browser")

    threading.Thread(target=worker, daemon=True).start()


def serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=False, log_level="info")


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="app.py",
        description="Start the Samsung phone chatbot (console + API).",
    )
    ap.add_argument("--host", default=settings.api.host)
    ap.add_argument("--port", type=int, default=settings.api.port)
    ap.add_argument("--scrape", action="store_true",
                    help="refresh the knowledge base before starting")
    ap.add_argument("--limit", type=int, default=None,
                    help="phones to load when refreshing (default: 10)")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-ingest saved pages and rebuild the vector index")
    ap.add_argument("--reset", action="store_true",
                    help="drop every row, then rebuild from the saved pages")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="run preflight checks and exit")
    args = ap.parse_args(argv)

    print(BANNER)
    t0 = time.time()

    try:
        # ---- preflight --------------------------------------------------
        log.info("[1/4] PostgreSQL")
        health = check_postgres()
        log.info("      %s", health["server"])
        log.info("      connected to %s", health["endpoint"])

        log.info("[2/4] local LLM")
        llm_ok, llm_msg = check_ollama()
        log.info("      %s", llm_msg)

        log.info("[3/4] knowledge base")
        if args.reset:
            reset_database()
        state = ensure_data(force_scrape=args.scrape,
                            force_rebuild=args.rebuild or args.reset,
                            limit=args.limit)
        log.info("      %d phones, %d chunks, %d embedded",
                 state["phones"], state["chunks"], state["embedded"])

        log.info("[4/4] embedding model")
        check_embedder()

    except StartupError as exc:
        log.error("")
        log.error("STARTUP FAILED")
        for line in str(exc).splitlines():
            log.error("  %s", line)
        return 1
    except KeyboardInterrupt:
        log.info("cancelled")
        return 130

    url = f"http://{args.host}:{args.port}"
    log.info("")
    log.info("ready in %.1fs", time.time() - t0)
    log.info("-" * 62)
    log.info("  console   %s", url)
    log.info("  API docs  %s/docs", url)
    log.info("  trace     %s/ws/trace", url.replace("http", "ws"))
    if not llm_ok:
        log.info("  NOTE      running without the LLM: answers will be raw rows")
    log.info("-" * 62)
    log.info("press Ctrl+C to stop")
    log.info("")

    if args.check:
        log.info("--check passed; exiting without serving")
        return 0

    if not args.no_browser:
        open_browser_when_ready(url)

    try:
        serve(args.host, args.port)
    except KeyboardInterrupt:
        pass
    finally:
        from database import engine

        engine.close_pool()
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
