"""Refresh the knowledge base from the command line.

Same pipeline the console's "Refresh knowledge base" button runs, so the CLI and
the UI can never drift apart.

    python -m scripts.refresh                # top 10, live with local fallback
    python -m scripts.refresh --limit 30     # the full catalogue
    python -m scripts.refresh --offline      # never touch the network
    python -m scripts.refresh --add          # add to the corpus instead of replacing
"""
from __future__ import annotations

import argparse
import sys

from backend.core.logging_setup import get_logger
from scraper import pipeline

log = get_logger("scripts.refresh")

ICON = {"scraping": "  ...", "added": "  [ok]", "failed": "  [--]"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts.refresh")
    ap.add_argument("--limit", type=int, default=None,
                    help="how many phones to load (default: SCRAPE_DEMO_COUNT)")
    ap.add_argument("--offline", action="store_true",
                    help="skip the network and rebuild from data/pages/")
    ap.add_argument("--add", action="store_true",
                    help="add to the knowledge base instead of replacing it")
    ap.add_argument("--no-index", action="store_true",
                    help="skip rebuilding the embeddings")
    args = ap.parse_args(argv)

    final: dict = {}
    for ev in pipeline.refresh(
        limit=args.limit,
        offline=args.offline,
        replace=not args.add,
        rebuild_index=not args.no_index,
    ):
        final = ev
        if ev["phase"] == "phone":
            extra = ""
            if ev["status"] == "added":
                extra = (f"  {ev['specs']} specs, {ev['nulls']} NULL, "
                         f"{ev['attributes']}/70 attributes  [{ev['source']}]")
            print(f"{ICON[ev['status']]} {ev['name']}{extra}")
        else:
            print(f"[{ev['phase']}] {ev.get('message', '')}")

    if final.get("phase") != "done":
        return 1
    if final.get("offline"):
        print(f"\nnote: finished from the local pages ({final.get('block_reason')})")
    for f in final.get("failures", []):
        print(f"  unavailable: {f['name']} -- {f['error'][:90]}")
    print(f"\nknowledge base: {final['phones']} phones, {final['chunks']} chunks")
    return 0 if final.get("added") else 1


if __name__ == "__main__":
    sys.exit(main())
