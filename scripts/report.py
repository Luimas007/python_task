"""Print what the database actually holds.

Useful as a hand-in artefact: it shows the corpus, per-device spec coverage and
which attributes are NULL across the catalogue -- i.e. evidence that absent
facts are recorded as NULL rather than invented.
"""
from __future__ import annotations

import sys

from database import engine
from database import loader
from database import repository as repo


def line(char: str = "-", n: int = 96) -> None:
    print(char * n)


def main() -> int:
    if not engine.healthcheck()["ok"]:
        print("PostgreSQL unavailable")
        return 1

    stats = repo.corpus_stats()
    line("=")
    print("  SAMSUNG PHONE KNOWLEDGE BASE")
    line("=")
    for k, v in stats.items():
        print(f"  {k:<16} {v}")

    print("\n  Series coverage")
    line()
    for s in repo.series_breakdown():
        span = (f"gen {s['oldest']}-{s['newest']}"
                if s["oldest"] != s["newest"] else f"gen {s['oldest']}")
        print(f"  {str(s['series']):<16} {s['n']:>2} device(s)   {span}")

    print("\n  Per-device coverage")
    line()
    print(f"  {'#':>3} {'model':<32} {'series':<14} {'specs':>6} {'null':>5} {'chunks':>7}")
    line()
    for i, r in enumerate(loader.coverage_report(), 1):
        print(f"  {i:>3} {r['model_name'][:32]:<32} {str(r['series'])[:14]:<14} "
              f"{r['spec_rows']:>6} {r['spec_null']:>5} {r['chunks']:>7}")

    print("\n  Attribute fill rate across the catalogue")
    print("  (a low count is a real gap in the source, not a parser failure)")
    line()
    rows = loader.attribute_null_stats()
    for r in rows[:18]:
        pct = 100 * r["present"] / r["total"] if r["total"] else 0
        bar = "#" * int(pct / 4)
        print(f"  {r['column']:<26} {r['present']:>3}/{r['total']:<3} "
              f"{pct:5.1f}%  {bar}")
    filled = sum(1 for r in rows if r["null"] == 0)
    print(f"\n  {filled}/{len(rows)} attributes are populated for every device.")

    print("\n  Sample: fields the source did not publish")
    line()
    missing = engine.fetch_all(
        """SELECT p.model_name, s.category, s.spec_key
             FROM specifications s JOIN phones p USING (phone_id)
            WHERE s.spec_value IS NULL
            ORDER BY p.popularity_rank, s.category LIMIT 15""",
        audit=False,
    )
    for m in missing:
        print(f"  {m['model_name'][:30]:<30} {m['category']:<14} {m['spec_key']}  -> NULL")
    line("=")
    return 0


if __name__ == "__main__":
    sys.exit(main())
