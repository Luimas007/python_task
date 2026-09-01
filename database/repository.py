"""Every read the agents are allowed to perform.

This module is the system's entire knowledge surface. If a fact cannot be
produced by one of these queries, no agent can state it.
"""
from __future__ import annotations

import re
from typing import Any

from backend.core.events import RunTrace
from backend.core.logging_setup import get_logger
from database import engine

log = get_logger("db.repository")

# ---------------------------------------------------------------------------
#  Entity resolution
# ---------------------------------------------------------------------------
# Model mentions we can recognise in free text: "Galaxy S23 Ultra", "S22",
# "Z Fold8", "Note20 Ultra", "A56", "S25 FE".
MENTION_RE = re.compile(
    r"\b(?:samsung\s+)?(?:galaxy\s+)?"
    r"(z\s*(?:fold|flip)\s*\d{0,2}(?:\s*(?:ultra|wide|special))?"
    r"|note\s?\d{1,2}(?:\s*(?:ultra|plus))?\+?"
    r"|[sam]\s?\d{2}(?:\s*(?:ultra|plus|fe|edge))?\+?"
    r"|[sam]\s?\d{1,2}(?:\s*(?:ultra|plus|fe|edge))?\+?)"
    r"(?:\s*5g)?\b",
    re.IGNORECASE,
)

_NOISE_RE = re.compile(r"\b(samsung|galaxy|5g|4g|lte|phone|smartphone)\b", re.IGNORECASE)


def normalise_name(text: str) -> str:
    """Collapse a model name to a comparison key: 'Galaxy S23 Ultra' -> 's23ultra'."""
    t = _NOISE_RE.sub(" ", text.lower())
    t = t.replace("+", " plus ")
    return re.sub(r"[^a-z0-9]+", "", t)


def extract_mentions(query: str) -> list[str]:
    """Ordered, de-duplicated model mentions found in the user's text."""
    seen, out = set(), []
    for m in MENTION_RE.finditer(query):
        raw = " ".join(m.group(0).split())
        key = normalise_name(raw)
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out


CANDIDATE_SQL = """
SELECT phone_id, slug, model_name, short_name, series, generation, tier,
       is_flagship, popularity_rank
FROM phones
"""


def _all_phones_cached(trace: RunTrace | None, agent: str | None) -> list[dict[str, Any]]:
    return engine.fetch_all(
        CANDIDATE_SQL + " ORDER BY popularity_rank",
        trace=trace,
        agent=agent,
        operation="SELECT phones (resolution candidates)",
    )


def resolve_phone(
    mention: str,
    *,
    trace: RunTrace | None = None,
    agent: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Match one text mention onto exactly one row in `phones`.

    Exact normalised equality wins outright, which is what keeps "S23" from
    being captured by "S23 Ultra". Otherwise the shortest containing name wins.
    """
    rows = candidates if candidates is not None else _all_phones_cached(trace, agent)
    key = normalise_name(mention)
    if not key:
        return None

    exact, prefix, contains = [], [], []
    for r in rows:
        for field in (r["short_name"], r["model_name"]):
            if not field:
                continue
            cand = normalise_name(field)
            if cand == key:
                exact.append(r)
                break
            if cand.startswith(key):
                prefix.append((len(cand), r))
                break
            if key in cand:
                contains.append((len(cand), r))
                break

    if exact:
        return exact[0]
    # Prefer the tightest fit, e.g. "fold8" -> "Galaxy Z Fold8" over "Fold8 Ultra".
    for bucket in (prefix, contains):
        if bucket:
            return min(bucket, key=lambda t: t[0])[1]
    return None


def resolve_phones(
    query: str, *, trace: RunTrace | None = None, agent: str | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve every mention in a query. Returns (matched rows, unmatched text)."""
    mentions = extract_mentions(query)
    if not mentions:
        return [], []
    candidates = _all_phones_cached(trace, agent)
    matched, unresolved, seen = [], [], set()
    for m in mentions:
        row = resolve_phone(m, candidates=candidates)
        if row and row["phone_id"] not in seen:
            seen.add(row["phone_id"])
            matched.append(row)
        elif not row:
            unresolved.append(m)
    return matched, unresolved


# ---------------------------------------------------------------------------
#  Specification retrieval
# ---------------------------------------------------------------------------
def phone_by_id(
    phone_id: int, *, trace: RunTrace | None = None, agent: str | None = None
) -> dict[str, Any] | None:
    return engine.fetch_one(
        """
        SELECT p.*, a.*
        FROM phones p
        LEFT JOIN phone_attributes a USING (phone_id)
        WHERE p.phone_id = %s
        """,
        (phone_id,),
        trace=trace,
        agent=agent,
        operation="SELECT phone + attributes",
    )


def specifications(
    phone_id: int,
    categories: list[str] | None = None,
    *,
    include_nulls: bool = True,
    trace: RunTrace | None = None,
    agent: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT category, spec_key, spec_code, spec_value
        FROM specifications
        WHERE phone_id = %(pid)s
          AND (%(cats)s::text[] IS NULL OR category = ANY(%(cats)s::text[]))
    """
    if not include_nulls:
        sql += " AND spec_value IS NOT NULL"
    sql += " ORDER BY spec_id"
    return engine.fetch_all(
        sql,
        {"pid": phone_id, "cats": categories},
        trace=trace,
        agent=agent,
        operation="SELECT specifications",
    )


def spec_sheet(
    phone_id: int,
    categories: list[str] | None = None,
    *,
    trace: RunTrace | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Full record for one phone: identity + typed attributes + verbatim specs."""
    phone = phone_by_id(phone_id, trace=trace, agent=agent)
    if not phone:
        return {}
    specs = specifications(phone_id, categories, trace=trace, agent=agent)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for s in specs:
        grouped.setdefault(s["category"], []).append(
            {"key": s["spec_key"], "value": s["spec_value"]}
        )
    return {"phone": phone, "specs_by_category": grouped}


# ---------------------------------------------------------------------------
#  Ranking / aggregation
# ---------------------------------------------------------------------------
# Whitelisted so a user query can never reach an arbitrary column.
RANKABLE: dict[str, dict[str, str]] = {
    "battery_capacity_mah":    {"label": "battery capacity",        "unit": "mAh", "dir": "desc"},
    "battery_endurance_hours": {"label": "measured battery endurance", "unit": "hours", "dir": "desc"},
    "charging_wired_w":        {"label": "wired charging power",    "unit": "W", "dir": "desc"},
    "charging_wireless_w":     {"label": "wireless charging power", "unit": "W", "dir": "desc"},
    "main_camera_mp":          {"label": "main camera resolution",  "unit": "MP", "dir": "desc"},
    "selfie_camera_mp":        {"label": "front camera resolution", "unit": "MP", "dir": "desc"},
    "display_size_in":         {"label": "screen size",             "unit": "inches", "dir": "desc"},
    "display_refresh_hz":      {"label": "refresh rate",            "unit": "Hz", "dir": "desc"},
    "display_ppi":             {"label": "pixel density",           "unit": "ppi", "dir": "desc"},
    "peak_brightness_nits":    {"label": "peak brightness",         "unit": "nits", "dir": "desc"},
    "antutu_score":            {"label": "AnTuTu benchmark score",  "unit": "points", "dir": "desc"},
    "geekbench_score":         {"label": "GeekBench score",         "unit": "points", "dir": "desc"},
    "max_ram_gb":              {"label": "maximum RAM",             "unit": "GB", "dir": "desc"},
    "max_storage_gb":          {"label": "maximum storage",         "unit": "GB", "dir": "desc"},
    "weight_g":                {"label": "weight",                  "unit": "g", "dir": "asc"},
    "thickness_mm":            {"label": "thickness",               "unit": "mm", "dir": "asc"},
    "price_usd":               {"label": "price in USD",            "unit": "USD", "dir": "asc"},
    "price_eur":               {"label": "price in EUR",            "unit": "EUR", "dir": "asc"},
}


def rank_by(
    column: str,
    *,
    direction: str | None = None,
    limit: int = 5,
    flagship_only: bool = False,
    series: str | None = None,
    trace: RunTrace | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Order phones by one whitelisted metric. NULLs are excluded and counted."""
    if column not in RANKABLE:
        raise ValueError(f"{column!r} is not a rankable attribute")
    meta = RANKABLE[column]
    direction = (direction or meta["dir"]).lower()
    order = "DESC" if direction == "desc" else "ASC"

    sql = f"""
        SELECT p.phone_id, p.model_name, p.series, p.tier, p.is_flagship,
               a.{column} AS metric_value
        FROM phones p
        JOIN phone_attributes a USING (phone_id)
        WHERE a.{column} IS NOT NULL
          AND (%(flagship)s = FALSE OR p.is_flagship)
          AND (%(series)s::text IS NULL OR p.series = %(series)s)
        ORDER BY a.{column} {order}
        LIMIT %(limit)s
    """
    rows = engine.fetch_all(
        sql,
        {"flagship": flagship_only, "series": series, "limit": limit},
        trace=trace,
        agent=agent,
        operation=f"RANK BY {column} {order}",
    )
    missing = engine.fetch_one(
        f"""SELECT count(*) AS n FROM phones p
             JOIN phone_attributes a USING (phone_id)
            WHERE a.{column} IS NULL""",
        trace=trace,
        agent=agent,
        operation=f"COUNT NULL {column}",
    )
    return {
        "column": column,
        "label": meta["label"],
        "unit": meta["unit"],
        "direction": order,
        "rows": rows,
        "excluded_null_count": int(missing["n"]) if missing else 0,
    }


def compare_matrix(
    phone_ids: list[int],
    columns: list[str] | None = None,
    *,
    trace: RunTrace | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Side-by-side typed attributes for two or more phones."""
    cols = columns or [
        "release_date", "display_size_in", "display_type", "display_refresh_hz",
        "display_ppi", "peak_brightness_nits", "chipset", "cpu", "gpu",
        "fabrication_nm", "antutu_score", "geekbench_score", "max_ram_gb",
        "max_storage_gb", "main_camera_mp", "main_camera_modules",
        "max_video_resolution", "selfie_camera_mp", "battery_capacity_mah",
        "battery_endurance_hours", "charging_wired_w", "charging_wireless_w",
        "weight_g", "thickness_mm", "ip_rating", "os_launch", "price_usd",
        "price_eur",
    ]
    select = ", ".join(f"a.{c}" for c in cols)
    rows = engine.fetch_all(
        f"""
        SELECT p.phone_id, p.model_name, p.series, p.tier, {select}
        FROM phones p
        LEFT JOIN phone_attributes a USING (phone_id)
        WHERE p.phone_id = ANY(%s::int[])
        ORDER BY array_position(%s::int[], p.phone_id)
        """,
        (phone_ids, phone_ids),
        trace=trace,
        agent=agent,
        operation="COMPARE MATRIX",
    )
    return {"columns": cols, "phones": rows}


# ---------------------------------------------------------------------------
#  Catalogue / diagnostics
# ---------------------------------------------------------------------------
def list_phones(
    *, trace: RunTrace | None = None, agent: str | None = None
) -> list[dict[str, Any]]:
    return engine.fetch_all(
        "SELECT * FROM v_phone_overview ORDER BY popularity_rank",
        trace=trace,
        agent=agent,
        operation="SELECT v_phone_overview",
    )


def corpus_stats() -> dict[str, Any]:
    row = engine.fetch_one(
        """
        SELECT (SELECT count(*) FROM phones)                       AS phones,
               (SELECT count(*) FROM phones WHERE is_flagship)     AS flagships,
               (SELECT count(*) FROM specifications)               AS spec_rows,
               (SELECT count(*) FROM specifications WHERE spec_value IS NULL) AS spec_nulls,
               (SELECT count(*) FROM knowledge_chunks)             AS chunks,
               (SELECT count(*) FROM knowledge_chunks WHERE embedding IS NOT NULL) AS embedded,
               (SELECT count(DISTINCT series) FROM phones)         AS series_count,
               (SELECT max(scraped_at) FROM phones)                AS last_scraped
        """,
        audit=False,
    )
    return row or {}


def series_breakdown() -> list[dict[str, Any]]:
    return engine.fetch_all(
        """SELECT series, count(*) AS n, min(generation) AS oldest, max(generation) AS newest
             FROM phones GROUP BY series ORDER BY n DESC""",
        audit=False,
    )


def recent_queries(limit: int = 25) -> list[dict[str, Any]]:
    return engine.fetch_all(
        """SELECT agent, protocol, operation, row_count, duration_ms, created_at
             FROM query_log ORDER BY log_id DESC LIMIT %s""",
        (limit,),
        audit=False,
    )


def display_names(phone_ids: list[int]) -> str:
    """Human-readable device list for the activity line shown in the chat.

    Deliberately un-traced and cached-free: it is cosmetic, and a label lookup
    should never add a protocol frame to the operator's trace.
    """
    if not phone_ids:
        return ""
    rows = engine.fetch_all(
        """SELECT model_name FROM phones
            WHERE phone_id = ANY(%s::int[])
            ORDER BY array_position(%s::int[], phone_id)""",
        (phone_ids, phone_ids),
        audit=False,
    )
    names = [r["model_name"] for r in rows]
    if len(names) <= 1:
        return names[0] if names else ""
    return " and ".join([", ".join(names[:-1]), names[-1]])
