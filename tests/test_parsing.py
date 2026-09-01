"""Parser and normalizer tests -- run against the real saved pages."""
from __future__ import annotations

import pytest

from backend.config.settings import settings
from scraper import catalog
from scraper.normalizer import ATTRIBUTE_COLUMNS, normalize, parse_month_date, yes_no
from scraper.parser import CANONICAL_SPECS, clean, parse

pytestmark = pytest.mark.parsing


def saved_pages() -> list:
    return sorted(
        p for p in settings.paths.pages.glob("*.html") if not p.name.startswith("_")
    )


@pytest.fixture(scope="module")
def pages():
    files = saved_pages()
    if not files:
        pytest.skip("no scraped pages on disk; run scripts.scrape_pages first")
    return files


# ------------------------------------------------------------------ helpers
def test_clean_normalises_and_nulls_placeholders():
    assert clean("  Li-Ion   5000  mAh ") == "Li-Ion 5000 mAh"
    assert clean("") is None
    assert clean("-") is None
    assert clean("N/A") is None
    assert clean(None) is None


def test_yes_no_reads_gsmarena_conventions():
    assert yes_no("Yes, with stereo speakers") is True
    assert yes_no("No") is False
    assert yes_no("microSDXC") is True       # described feature means present
    assert yes_no(None) is None              # unknown stays unknown


def test_parse_month_date_handles_both_shapes():
    assert parse_month_date("2023, February 17").isoformat() == "2023-02-17"
    assert parse_month_date("2023, February").isoformat() == "2023-02-01"
    assert parse_month_date("Exp. release 2026") is None
    assert parse_month_date(None) is None


# ------------------------------------------------------------------- parser
def test_every_saved_page_parses(pages):
    for path in pages:
        page = parse(path.read_text(encoding="utf-8", errors="replace"), path.stem)
        assert page.model_name, f"{path.name}: no model name"
        assert page.specs, f"{path.name}: no spec rows"
        assert not page.warnings, f"{path.name}: {page.warnings}"


def test_canonical_specs_always_present(pages):
    """Absent facts must exist as explicit NULL rows, not vanish."""
    codes = {code for _, _, code in CANONICAL_SPECS}
    for path in pages[:5]:
        page = parse(path.read_text(encoding="utf-8", errors="replace"), path.stem)
        seen = {s.code for s in page.specs if s.code}
        assert codes.issubset(seen), f"{path.name} missing {codes - seen}"


def test_null_means_absent_not_empty_string(pages):
    for path in pages:
        page = parse(path.read_text(encoding="utf-8", errors="replace"), path.stem)
        for s in page.specs:
            assert s.value is None or s.value.strip(), \
                f"{path.name}: {s.key} stored an empty string instead of NULL"


# --------------------------------------------------------------- normalizer
def test_normalizer_returns_full_column_set(pages):
    page = parse(pages[0].read_text(encoding="utf-8", errors="replace"), pages[0].stem)
    attrs = normalize(page)
    assert set(attrs) == set(ATTRIBUTE_COLUMNS)


def test_s23_ultra_known_values():
    """Spot-check against figures published on the S23 Ultra page."""
    path = settings.paths.pages / "samsung_galaxy_s23_ultra-12024.html"
    if not path.exists():
        pytest.skip("S23 Ultra page not scraped")
    attrs = normalize(parse(path.read_text(encoding="utf-8"), path.stem))
    assert attrs["display_size_in"] == 6.8
    assert attrs["display_refresh_hz"] == 120
    assert attrs["battery_capacity_mah"] == 5000
    assert attrs["charging_wired_w"] == 45.0
    assert attrs["charging_wireless_w"] == 15.0
    assert attrs["main_camera_mp"] == 200.0
    assert attrs["max_ram_gb"] == 12
    assert attrs["max_storage_gb"] == 1024
    assert attrs["ip_rating"] == "IP68"
    assert attrs["card_slot"] is False
    assert attrs["has_5g"] is True
    assert attrs["chipset_vendor"] == "Qualcomm"
    assert attrs["max_video_resolution"] == "8K"


def test_budget_phone_has_genuine_nulls():
    """A cheap device should show real gaps, proving NULLs are not fabricated."""
    path = settings.paths.pages / "samsung_galaxy_a07-14066.html"
    if not path.exists():
        pytest.skip("A07 page not scraped")
    attrs = normalize(parse(path.read_text(encoding="utf-8"), path.stem))
    assert attrs["charging_wireless_w"] is None   # the A07 has no wireless charging
    assert attrs["display_protection"] is None    # none published
    assert attrs["has_5g"] is False               # 4G-only model
    assert attrs["battery_capacity_mah"] == 5000  # but real values still land


# ------------------------------------------------------------------ catalog
def test_catalog_covers_all_flagship_lines():
    if not settings.paths.catalog.exists():
        pytest.skip("catalog not built")
    entries = catalog.load()
    assert len(entries) == settings.scraper.target_count

    series = {e.series for e in entries}
    for required in ("Galaxy S", "Galaxy Z Fold", "Galaxy Z Flip", "Galaxy Note"):
        assert required in series, f"no {required} device selected"

    s_gens = sorted({e.generation for e in entries
                     if e.series == "Galaxy S" and e.generation})
    assert len(s_gens) >= 5, f"too few Galaxy S generations: {s_gens}"

    variants = {e.variant for e in entries if e.series == "Galaxy S"}
    assert {"base", "Ultra"}.issubset(variants)


def test_catalog_excludes_non_phones():
    if not settings.paths.catalog.exists():
        pytest.skip("catalog not built")
    for e in catalog.load():
        low = e.short_name.lower()
        assert not any(w in low for w in ("tab", "watch", "book", "buds")), \
            f"{e.short_name} is not a phone"


def test_classifier_variant_casing():
    """FE must not be title-cased to 'Fe' -- that breaks quota matching."""
    assert catalog.classify("Galaxy S25 FE")["variant"] == "FE"
    assert catalog.classify("Galaxy S24 Ultra")["variant"] == "Ultra"
    assert catalog.classify("Galaxy S23")["variant"] == "base"
    assert catalog.classify("Galaxy S26+")["variant"] == "Plus"
    assert catalog.classify("Galaxy Z Fold7")["series"] == "Galaxy Z Fold"
    assert catalog.classify("Galaxy Z Flip7")["is_flagship"] is True
    assert catalog.classify("Galaxy A56")["is_flagship"] is False
