"""Write parsed pages into PostgreSQL.

Upsert semantics throughout, so the pipeline can be re-run over the cached pages
without duplicating anything. Absent facts are written as SQL NULL -- never as
an empty string, a zero, or a value borrowed from a sibling device.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg2.extras

from backend.core.logging_setup import get_logger
from database import engine
from scraper.catalog import CatalogEntry
from scraper.normalizer import ATTRIBUTE_COLUMNS, normalize
from scraper.parser import ParsedPage

log = get_logger("db.loader")


def start_run(notes: str | None = None) -> int:
    row = engine.execute(
        "INSERT INTO scrape_runs (notes) VALUES (%s) RETURNING run_id",
        (notes,),
        returning=True,
    )
    return int(row["run_id"])


def finish_run(run_id: int, fetched: int, failed: int) -> None:
    engine.execute(
        """UPDATE scrape_runs
              SET finished_at = now(), pages_fetched = %s, pages_failed = %s
            WHERE run_id = %s""",
        (fetched, failed, run_id),
    )


# ------------------------------------------------------------------ phones
PHONE_UPSERT = """
INSERT INTO phones (slug, gsmarena_id, brand, model_name, short_name, series,
                    generation, tier, form_factor, is_flagship, popularity_rank,
                    popularity_hits, popularity_pct, fan_count, source_url,
                    local_page_path, page_sha256, page_bytes, scraped_at,
                    scrape_run_id)
VALUES (%(slug)s, %(gsmarena_id)s, %(brand)s, %(model_name)s, %(short_name)s,
        %(series)s, %(generation)s, %(tier)s, %(form_factor)s, %(is_flagship)s,
        %(popularity_rank)s, %(popularity_hits)s, %(popularity_pct)s,
        %(fan_count)s, %(source_url)s, %(local_page_path)s, %(page_sha256)s,
        %(page_bytes)s, %(scraped_at)s, %(scrape_run_id)s)
ON CONFLICT (slug) DO UPDATE SET
    gsmarena_id     = EXCLUDED.gsmarena_id,
    model_name      = EXCLUDED.model_name,
    short_name      = EXCLUDED.short_name,
    series          = EXCLUDED.series,
    generation      = EXCLUDED.generation,
    tier            = EXCLUDED.tier,
    form_factor     = EXCLUDED.form_factor,
    is_flagship     = EXCLUDED.is_flagship,
    popularity_rank = EXCLUDED.popularity_rank,
    popularity_hits = EXCLUDED.popularity_hits,
    popularity_pct  = EXCLUDED.popularity_pct,
    fan_count       = EXCLUDED.fan_count,
    source_url      = EXCLUDED.source_url,
    local_page_path = EXCLUDED.local_page_path,
    page_sha256     = EXCLUDED.page_sha256,
    page_bytes      = EXCLUDED.page_bytes,
    scraped_at      = EXCLUDED.scraped_at,
    scrape_run_id   = EXCLUDED.scrape_run_id,
    updated_at      = now()
RETURNING phone_id
"""


def upsert_phone(
    entry: CatalogEntry,
    page: ParsedPage,
    *,
    local_path: str | None,
    sha256: str | None,
    page_bytes: int | None,
    scrape_run_id: int | None,
) -> int:
    params = {
        "slug": entry.slug,
        "gsmarena_id": entry.gsmarena_id or page.gsmarena_id,
        "brand": "Samsung",
        # Prefer the on-page H1 over the listing label -- it is the canonical name.
        "model_name": page.model_name or entry.model_name,
        "short_name": entry.short_name,
        "series": entry.series,
        "generation": entry.generation,
        "tier": entry.tier,
        "form_factor": entry.form_factor,
        "is_flagship": entry.is_flagship,
        "popularity_rank": entry.popularity_rank,
        "popularity_hits": page.popularity_hits,
        "popularity_pct": page.popularity_pct,
        "fan_count": page.fan_count,
        "source_url": entry.url,
        "local_page_path": local_path,
        "page_sha256": sha256,
        "page_bytes": page_bytes,
        "scraped_at": datetime.now(timezone.utc),
        "scrape_run_id": scrape_run_id,
    }
    row = engine.execute(PHONE_UPSERT, params, returning=True, operation="UPSERT phones")
    return int(row["phone_id"])


# ---------------------------------------------------------- specifications
SPEC_INSERT = """
INSERT INTO specifications (phone_id, category, spec_key, spec_code, spec_value, position)
VALUES %s
ON CONFLICT (phone_id, category, spec_key, position) DO UPDATE SET
    spec_code  = EXCLUDED.spec_code,
    spec_value = EXCLUDED.spec_value
"""


def replace_specifications(phone_id: int, page: ParsedPage) -> int:
    rows = [
        (phone_id, s.category, s.key, s.code, s.value, s.position)
        for s in page.specs
    ]
    with engine.cursor(dict_rows=False) as cur:
        cur.execute("DELETE FROM specifications WHERE phone_id = %s", (phone_id,))
        psycopg2.extras.execute_values(cur, SPEC_INSERT, rows, page_size=200)
    return len(rows)


# -------------------------------------------------------- phone_attributes
def upsert_attributes(phone_id: int, page: ParsedPage) -> dict[str, Any]:
    attrs = normalize(page)
    cols = ATTRIBUTE_COLUMNS
    placeholders = ", ".join(f"%({c})s" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    sql = f"""
        INSERT INTO phone_attributes (phone_id, {", ".join(cols)})
        VALUES (%(phone_id)s, {placeholders})
        ON CONFLICT (phone_id) DO UPDATE SET {updates}, updated_at = now()
    """
    params = {"phone_id": phone_id, **{c: attrs.get(c) for c in cols}}
    engine.execute(sql, params, operation="UPSERT phone_attributes")
    return attrs


# ------------------------------------------------------------------ report
def coverage_report() -> list[dict[str, Any]]:
    return engine.fetch_all(
        """
        SELECT p.model_name,
               p.series,
               p.popularity_rank,
               c.spec_rows,
               c.spec_present,
               c.spec_null,
               c.chunks,
               (SELECT count(*) FROM phone_attributes a WHERE a.phone_id = p.phone_id) AS has_attrs
        FROM phones p
        JOIN v_coverage c USING (phone_id)
        ORDER BY p.popularity_rank
        """,
        audit=False,
    )


def attribute_null_stats() -> list[dict[str, Any]]:
    """Per-column fill rate across the corpus -- proves NULLs are real."""
    parts = [
        f"count({c}) AS {c}" for c in ATTRIBUTE_COLUMNS
    ]
    row = engine.fetch_one(
        f"SELECT count(*) AS total, {', '.join(parts)} FROM phone_attributes",
        audit=False,
    )
    if not row:
        return []
    total = row.pop("total")
    return sorted(
        (
            {"column": k, "present": v, "null": total - v, "total": total}
            for k, v in row.items()
        ),
        key=lambda d: d["present"],
    )
