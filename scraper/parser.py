"""Turn a saved GSMArena page into a verbatim, structured record.

This layer performs *no* interpretation beyond reading the document: whatever the
page publishes is captured as-is, and anything the page omits becomes None. Typed
interpretation happens later, in scraper/normalizer.py.

GSMArena tags every spec cell with a stable `data-spec` code (`displaysize`,
`batdescription1`, ...). Those codes are the primary key for extraction, with the
visible row label kept alongside for human-readable output.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from backend.core.logging_setup import get_logger

log = get_logger("scraper.parser")

# Every spec code GSMArena can publish on a phone page. Codes absent from a given
# page are still recorded -- with a NULL value -- so "not published" is an
# explicit fact in the database rather than a missing row.
CANONICAL_SPECS: list[tuple[str, str, str]] = [
    # (category, label, data-spec code)
    ("Network", "Technology", "nettech"),
    ("Network", "2G bands", "net2g"),
    ("Network", "3G bands", "net3g"),
    ("Network", "4G bands", "net4g"),
    ("Network", "5G bands", "net5g"),
    ("Network", "Speed", "speed"),
    ("Launch", "Announced", "year"),
    ("Launch", "Status", "status"),
    ("Body", "Dimensions", "dimensions"),
    ("Body", "Weight", "weight"),
    ("Body", "Build", "build"),
    ("Body", "SIM", "sim"),
    ("Body", "Other", "bodyother"),
    ("Display", "Type", "displaytype"),
    ("Display", "Size", "displaysize"),
    ("Display", "Resolution", "displayresolution"),
    ("Display", "Protection", "displayprotection"),
    ("Platform", "OS", "os"),
    ("Platform", "Chipset", "chipset"),
    ("Platform", "CPU", "cpu"),
    ("Platform", "GPU", "gpu"),
    ("Memory", "Card slot", "memoryslot"),
    ("Memory", "Internal", "internalmemory"),
    ("Memory", "Other", "memoryother"),
    ("Main Camera", "Modules", "cam1modules"),
    ("Main Camera", "Features", "cam1features"),
    ("Main Camera", "Video", "cam1video"),
    ("Selfie Camera", "Modules", "cam2modules"),
    ("Selfie Camera", "Features", "cam2features"),
    ("Selfie Camera", "Video", "cam2video"),
    ("Sound", "Loudspeaker", "speakers"),
    ("Sound", "3.5mm jack", "3.5mm-jack"),
    ("Sound", "Other", "optionalother"),
    ("Comms", "WLAN", "wlan"),
    ("Comms", "Bluetooth", "bluetooth"),
    ("Comms", "Positioning", "gps"),
    ("Comms", "NFC", "nfc"),
    ("Comms", "Radio", "radio"),
    ("Comms", "USB", "usb"),
    ("Features", "Sensors", "sensors"),
    ("Features", "Other", "featuresother"),
    ("Battery", "Type", "batdescription1"),
    ("Battery", "Charging", "charging"),
    ("Battery", "Endurance rating", "batlife2"),
    ("Misc", "Colors", "colors"),
    ("Misc", "Models", "models"),
    ("Misc", "SAR", "sar-us"),
    ("Misc", "SAR EU", "sar-eu"),
    ("Misc", "Price", "price"),
    ("Tests", "Benchmarks", "tbench"),
    ("Tests", "Display test", "tdisplay"),
    ("Tests", "Loudspeaker test", "tloudspeaker"),
    ("Tests", "Battery life", "tbatlife"),
]

CODE_TO_CANON = {code: (cat, label) for cat, label, code in CANONICAL_SPECS}

# GSMArena omits `data-spec` on a handful of rows (Network>Technology,
# Sound>Loudspeaker, Sound>3.5mm jack, Battery>Charging). Recover the code from
# the visible label so those values still reach `by_code`.
LABEL_TO_CODE = {(cat, label.lower()): code for cat, label, code in CANONICAL_SPECS}


@dataclass
class SpecRow:
    category: str
    key: str
    code: str | None
    value: str | None
    position: int = 0


@dataclass
class ParsedPage:
    slug: str
    model_name: str | None
    gsmarena_id: int | None
    popularity_hits: int | None
    popularity_pct: float | None
    fan_count: int | None
    release_headline: str | None
    highlights: dict[str, str] = field(default_factory=dict)
    specs: list[SpecRow] = field(default_factory=list)
    by_code: dict[str, str] = field(default_factory=dict)
    narrative: str | None = None
    warnings: list[str] = field(default_factory=list)

    def coverage(self) -> tuple[int, int]:
        present = sum(1 for s in self.specs if s.value is not None)
        return present, len(self.specs)


# ------------------------------------------------------------------ helpers
_WS_RE = re.compile(r"[ \t    ]+")


def clean(text: str | None) -> str | None:
    """Normalise whitespace and unicode; empty / placeholder text becomes None."""
    if text is None:
        return None
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("‑", "-").replace("–", "-").replace("’", "'")
    t = _WS_RE.sub(" ", t).strip(" \r\n\t-")
    # GSMArena uses these to mean "no data"
    if t in {"", "-", "--", "N/A", "n/a", "No data", "?"}:
        return None
    return t


def _int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"[\d,]+", text)
    return int(m.group().replace(",", "")) if m else None


def _float(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"[\d.]+", text)
    try:
        return float(m.group()) if m else None
    except ValueError:
        return None


# ------------------------------------------------------------------- parse
def parse(html: str, slug: str) -> ParsedPage:
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.find("h1", class_="specs-phone-name-title") or soup.find("h1")
    model_name = clean(h1.get_text(" ", strip=True)) if h1 else None

    gid = None
    if m := re.search(r"-(\d+)$", slug):
        gid = int(m.group(1))

    page = ParsedPage(
        slug=slug,
        model_name=model_name,
        gsmarena_id=gid,
        popularity_hits=None,
        popularity_pct=None,
        fan_count=None,
        release_headline=None,
    )

    _parse_popularity(soup, page)
    _parse_highlights(soup, page)
    _parse_spec_tables(soup, page)
    _fill_absent_specs(page)
    _parse_narrative(soup, page)

    present, total = page.coverage()
    log.debug("%s: %d/%d specs present", slug, present, total)
    return page


def _parse_popularity(soup: BeautifulSoup, page: ParsedPage) -> None:
    pop = soup.select_one("li.help-popularity")
    if pop:
        text = pop.get_text(" ", strip=True)          # "15% 11,171,798 hits"
        page.popularity_hits = _int(text.split("%")[-1] if "%" in text else text)
        if m := re.search(r"([\d.]+)\s*%", text):
            page.popularity_pct = float(m.group(1))
    fans = soup.select_one("li.help-fans")
    if fans:
        page.fan_count = _int(fans.get_text(" ", strip=True))


def _parse_highlights(soup: BeautifulSoup, page: ParsedPage) -> None:
    """The spotlight strip above the spec table (`*-hl` codes)."""
    for el in soup.select("[data-spec]"):
        code = el.get("data-spec") or ""
        if not code.endswith("-hl") and code != "modelname":
            continue
        val = clean(el.get_text(" ", strip=True))
        if val:
            page.highlights[code] = val
    page.release_headline = page.highlights.get("released-hl")


def _parse_spec_tables(soup: BeautifulSoup, page: ParsedPage) -> None:
    """Walk #specs-list verbatim; keep every row the page actually shows."""
    container = soup.find("div", id="specs-list") or soup
    seen: set[tuple[str, str, int]] = set()

    for table in container.find_all("table"):
        th = table.find("th")
        category = clean(th.get_text(" ", strip=True)) if th else "Other"
        if not category:
            category = "Other"
        last_key = None
        for tr in table.find_all("tr"):
            ttl = tr.find("td", class_="ttl")
            nfo = tr.find("td", class_="nfo")
            if nfo is None:
                continue
            key = clean(ttl.get_text(" ", strip=True)) if ttl else None
            if key:
                last_key = key
            else:
                # Continuation row: GSMArena leaves the label cell empty.
                key = f"{last_key} (cont.)" if last_key else "Other"
            code = nfo.get("data-spec") or LABEL_TO_CODE.get((category, key.lower()))
            value = clean(nfo.get_text(" ", strip=True))

            pos = 0
            while (category, key, pos) in seen:
                pos += 1
            seen.add((category, key, pos))
            page.specs.append(SpecRow(category, key, code, value, pos))
            if code and value and code not in page.by_code:
                page.by_code[code] = value

    if not page.specs:
        page.warnings.append("no spec rows found -- page layout may have changed")


def _fill_absent_specs(page: ParsedPage) -> None:
    """Record canonical specs the page did not publish, explicitly as NULL.

    A spec counts as present if either its code or its (category, label) pair
    already appeared, since not every row carries a `data-spec` attribute.
    """
    have_codes = {s.code for s in page.specs if s.code}
    have_labels = {(s.category, s.key.lower()) for s in page.specs}
    for category, label, code in CANONICAL_SPECS:
        if code in have_codes or (category, label.lower()) in have_labels:
            continue
        page.specs.append(SpecRow(category, label, code, None, 0))


def _parse_narrative(soup: BeautifulSoup, page: ParsedPage) -> None:
    """Any editorial blurb GSMArena ships with the spec sheet."""
    parts: list[str] = []
    for sel in ("p.article-info-description", "div.article-info p", "#specs-list p"):
        for el in soup.select(sel):
            txt = clean(el.get_text(" ", strip=True))
            if txt and len(txt) > 40:
                parts.append(txt)
    if parts:
        page.narrative = " ".join(dict.fromkeys(parts))[:4000]


def as_dict(page: ParsedPage) -> dict[str, Any]:
    return {
        "slug": page.slug,
        "model_name": page.model_name,
        "gsmarena_id": page.gsmarena_id,
        "popularity_hits": page.popularity_hits,
        "popularity_pct": page.popularity_pct,
        "fan_count": page.fan_count,
        "release_headline": page.release_headline,
        "highlights": page.highlights,
        "by_code": page.by_code,
        "narrative": page.narrative,
        "warnings": page.warnings,
        "specs": [
            {
                "category": s.category,
                "key": s.key,
                "code": s.code,
                "value": s.value,
                "position": s.position,
            }
            for s in page.specs
        ],
    }
