"""Database integrity, retrieval and NULL-policy tests."""
from __future__ import annotations

import pytest

from database import engine
from database import repository as repo

pytestmark = pytest.mark.database


@pytest.fixture(scope="module", autouse=True)
def require_db():
    health = engine.healthcheck()
    if not health["ok"]:
        pytest.skip(f"PostgreSQL unavailable: {health.get('error')}")
    row = engine.fetch_one("SELECT count(*) AS n FROM phones", audit=False)
    if not row or row["n"] == 0:
        pytest.skip("database is empty; run python -m scripts.refresh")


# --------------------------------------------------------------- structure
def test_expected_tables_exist():
    rows = engine.fetch_all(
        """SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'""",
        audit=False,
    )
    names = {r["table_name"] for r in rows}
    assert {"phones", "specifications", "phone_attributes", "knowledge_chunks",
            "query_log", "conversations", "messages", "scrape_runs"} <= names


def test_cosine_similarity_function():
    r = engine.fetch_one(
        """SELECT cosine_similarity(ARRAY[1,0,0]::real[], ARRAY[1,0,0]::real[]) AS same,
                  cosine_similarity(ARRAY[1,0]::real[],   ARRAY[0,1]::real[])   AS orth,
                  cosine_similarity(ARRAY[1,0]::real[],   ARRAY[-1,0]::real[])  AS opp""",
        audit=False,
    )
    assert r["same"] == pytest.approx(1.0, abs=1e-6)
    assert r["orth"] == pytest.approx(0.0, abs=1e-6)
    assert r["opp"] == pytest.approx(-1.0, abs=1e-6)


def test_every_phone_has_attributes_and_specs():
    rows = engine.fetch_all(
        """SELECT p.phone_id, p.model_name,
                  (SELECT count(*) FROM specifications s WHERE s.phone_id=p.phone_id) AS specs,
                  (SELECT count(*) FROM phone_attributes a WHERE a.phone_id=p.phone_id) AS attrs
             FROM phones p""",
        audit=False,
    )
    for r in rows:
        assert r["specs"] > 30, f"{r['model_name']} has only {r['specs']} spec rows"
        assert r["attrs"] == 1, f"{r['model_name']} has no attribute row"


def test_no_orphan_rows():
    for table in ("specifications", "phone_attributes", "knowledge_chunks"):
        r = engine.fetch_one(
            f"""SELECT count(*) AS n FROM {table} t
                 LEFT JOIN phones p USING (phone_id)
                 WHERE t.phone_id IS NOT NULL AND p.phone_id IS NULL""",
            audit=False,
        )
        assert r["n"] == 0, f"{table} has {r['n']} orphans"


# ------------------------------------------------------------- NULL policy
def test_absent_facts_are_null_never_placeholder():
    """No spec value may be an empty string or a fabricated stand-in."""
    r = engine.fetch_one(
        """SELECT count(*) AS n FROM specifications
            WHERE spec_value IS NOT NULL
              AND (btrim(spec_value) = ''
                   OR lower(btrim(spec_value)) IN ('n/a','na','none','null','-','unknown','tbd'))""",
        audit=False,
    )
    assert r["n"] == 0, f"{r['n']} placeholder values found where NULL is required"


def test_nulls_are_actually_recorded():
    """The corpus must contain explicit NULLs, or absence is not being captured."""
    r = engine.fetch_one(
        "SELECT count(*) AS n FROM specifications WHERE spec_value IS NULL", audit=False
    )
    assert r["n"] > 0, "no NULL spec rows at all -- absence is not being recorded"


def test_numeric_attributes_are_plausible():
    checks = [
        ("battery_capacity_mah", 1000, 12000),
        ("display_size_in", 3.0, 9.0),
        ("weight_g", 90, 400),
        ("main_camera_mp", 1, 250),
        ("display_refresh_hz", 30, 240),
        ("max_ram_gb", 1, 32),
        ("thickness_mm", 3, 20),
    ]
    for col, lo, hi in checks:
        bad = engine.fetch_all(
            f"""SELECT p.model_name, a.{col} AS v FROM phone_attributes a
                 JOIN phones p USING (phone_id)
                WHERE a.{col} IS NOT NULL AND (a.{col} < %s OR a.{col} > %s)""",
            (lo, hi),
            audit=False,
        )
        assert not bad, f"{col} out of range: {bad}"


def test_no_duplicate_devices():
    dupes = engine.fetch_all(
        "SELECT model_name, count(*) AS n FROM phones GROUP BY model_name HAVING count(*) > 1",
        audit=False,
    )
    assert not dupes, f"duplicate phones: {dupes}"


# -------------------------------------------------------------- resolution
@pytest.fixture(scope="module")
def loaded():
    """Model names actually in the knowledge base right now."""
    return [r["model_name"] for r in repo.list_phones()]


def test_every_loaded_phone_resolves_from_its_own_name(loaded):
    for name in loaded:
        row = repo.resolve_phone(name)
        assert row is not None, f"{name} does not resolve to itself"
        assert row["model_name"] == name


def test_short_name_resolves(loaded):
    """'Galaxy S25 Ultra' must find 'Samsung Galaxy S25 Ultra'."""
    for name in loaded:
        short = name.replace("Samsung ", "")
        row = repo.resolve_phone(short)
        assert row is not None, f"{short!r} did not resolve"
        assert row["model_name"] == name


def test_base_model_does_not_match_a_variant(loaded):
    """The tightest fit wins: 'S25' must not be captured by 'S25 Ultra'."""
    pairs = [
        (base, variant)
        for base in loaded
        for variant in loaded
        if variant != base and variant.startswith(base + " ")
    ]
    if not pairs:
        pytest.skip("corpus holds no base/variant pair")
    for base, variant in pairs:
        row = repo.resolve_phone(base.replace("Samsung ", ""))
        assert row is not None and row["model_name"] == base, (
            f"{base!r} resolved to {row and row['model_name']!r}, "
            f"expected the base model rather than {variant!r}"
        )


def test_unknown_device_resolves_to_nothing():
    assert repo.resolve_phone("Galaxy S99 Omega") is None
    matched, unresolved = repo.resolve_phones("Tell me about the Galaxy S99 Omega")
    assert matched == []
    assert unresolved


def test_multiple_mentions_resolve_in_order(loaded):
    if len(loaded) < 2:
        pytest.skip("need two phones")
    a, b = loaded[0], loaded[1]
    matched, _ = repo.resolve_phones(f"Compare the {a} with the {b}")
    assert [m["model_name"] for m in matched] == [a, b]


def test_display_names_reads_well(loaded):
    ids = [r["phone_id"] for r in repo.list_phones()][:2]
    if len(ids) < 2:
        pytest.skip("need two phones")
    text = repo.display_names(ids)
    assert " and " in text
    assert repo.display_names([]) == ""


# ----------------------------------------------------------------- ranking
def test_rank_by_excludes_nulls_and_counts_them():
    result = repo.rank_by("battery_endurance_hours", limit=5)
    assert all(r["metric_value"] is not None for r in result["rows"])
    assert result["excluded_null_count"] >= 0
    values = [float(r["metric_value"]) for r in result["rows"]]
    assert values == sorted(values, reverse=True)


def test_rank_ascending_for_lower_is_better():
    result = repo.rank_by("weight_g", limit=5)
    values = [float(r["metric_value"]) for r in result["rows"]]
    assert values == sorted(values), "weight should rank lightest first"


def test_rank_rejects_arbitrary_columns():
    with pytest.raises(ValueError):
        repo.rank_by("model_name")
    with pytest.raises(ValueError):
        repo.rank_by("1; DROP TABLE phones")


# --------------------------------------------------------------- retrieval
def test_vector_search_returns_relevant_chunks():
    from backend.rag import retriever

    name = repo.list_phones()[0]["model_name"]
    hits = retriever.search(f"battery capacity of the {name}", top_k=5)
    assert hits, "no chunks retrieved"
    assert all(0.0 <= h.score <= 1.0 for h in hits)
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)


def test_vector_search_can_filter_by_phone():
    from backend.rag import retriever

    target = repo.list_phones()[0]
    hits = retriever.search("camera", top_k=5, phone_ids=[target["phone_id"]])
    assert hits
    assert {h.phone_id for h in hits} == {target["phone_id"]}


def test_all_chunks_are_embedded():
    r = engine.fetch_one(
        """SELECT count(*) AS total, count(embedding) AS embedded,
                  array_length(min(embedding),1) AS dim FROM knowledge_chunks""",
        audit=False,
    )
    assert r["total"] > 0
    assert r["total"] == r["embedded"], "some chunks have no embedding"
    assert r["dim"] == 384
