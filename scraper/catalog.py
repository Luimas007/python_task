"""Decide *which* Samsung phones to scrape.

Selection is driven by GSMArena's own popularity ranking (the `-r1-` sort on the
Samsung brand listing), then corrected so that every major flagship line is
represented rather than letting cheap high-volume A-series models crowd them out.

Output: a deterministic, ranked catalogue of N devices written to data/catalog.json.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Iterable

from bs4 import BeautifulSoup

from backend.config.settings import settings
from backend.core.logging_setup import get_logger
from scraper.fetcher import PageFetcher

log = get_logger("scraper.catalog")

# Product families on the Samsung brand listing that are not phones.
NON_PHONE_PATTERNS = re.compile(
    r"\b(tab|tablet|watch|book|buds|gear|fit\d?|ring|view|home|"
    r"chromebook|galaxy\s+view)\b",
    re.IGNORECASE,
)


@dataclass
class CatalogEntry:
    slug: str
    gsmarena_id: int | None
    short_name: str          # "Galaxy S23 Ultra"
    model_name: str          # "Samsung Galaxy S23 Ultra"
    url: str
    popularity_rank: int     # 1 = most popular on GSMArena right now
    series: str | None
    generation: int | None
    variant: str | None      # Ultra | Plus | FE | Edge | base
    family_key: str | None   # "Galaxy S:Ultra" -- used for flagship quotas
    tier: str
    form_factor: str
    is_flagship: bool
    selected_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- classify
_S_RE = re.compile(r"^Galaxy S(\d{1,2})(?:\s*(Ultra|Edge|FE))?(\+)?", re.IGNORECASE)
_NOTE_RE = re.compile(r"^Galaxy Note\s?(\d{1,2})(?:\s*(Ultra))?(\+)?", re.IGNORECASE)
_FOLD_RE = re.compile(r"^Galaxy Z Fold\s?(\d{1,2})?(?:\s*(Ultra|Wide|Special))?", re.IGNORECASE)
_FLIP_RE = re.compile(r"^Galaxy Z Flip\s?(\d{1,2})?", re.IGNORECASE)
_LETTER_RE = re.compile(r"^Galaxy ([AMFJ])(\d{2,3})(\w*)", re.IGNORECASE)

# Preserve real product casing -- str.title() renders "FE" as "Fe", which would
# silently break the flagship-quota lookup further down.
_VARIANT_CASE = {"fe": "FE", "ultra": "Ultra", "edge": "Edge",
                 "plus": "Plus", "wide": "Wide", "special": "Special"}


def classify(short_name: str) -> dict:
    """Map a listing name onto series / generation / variant / tier."""
    name = short_name.strip()

    if m := _FOLD_RE.match(name):
        gen, sub = m.group(1), (m.group(2) or "")
        variant = _VARIANT_CASE.get(sub.lower(), sub.title()) if sub else "base"
        return dict(
            series="Galaxy Z Fold",
            generation=int(gen) if gen else None,
            variant=variant,
            family_key="Galaxy Z Fold",
            tier="foldable-flagship",
            form_factor="book-fold",
            is_flagship=True,
        )

    if m := _FLIP_RE.match(name):
        gen = m.group(1)
        return dict(
            series="Galaxy Z Flip",
            generation=int(gen) if gen else None,
            variant="base",
            family_key="Galaxy Z Flip",
            tier="foldable-flagship",
            form_factor="clamshell-fold",
            is_flagship=True,
        )

    if m := _NOTE_RE.match(name):
        gen, sub, plus = m.group(1), m.group(2), m.group(3)
        variant = "Ultra" if sub else ("Plus" if plus else "base")
        return dict(
            series="Galaxy Note",
            generation=int(gen),
            variant=variant,
            family_key="Galaxy Note",
            tier="flagship",
            form_factor="bar",
            is_flagship=True,
        )

    if m := _S_RE.match(name):
        gen, sub, plus = int(m.group(1)), m.group(2), m.group(3)
        sub = _VARIANT_CASE.get((sub or "").lower(), (sub or "").title())
        variant = sub if sub else ("Plus" if plus else "base")
        return dict(
            series="Galaxy S",
            generation=gen,
            variant=variant,
            family_key=f"Galaxy S:{variant}",
            tier="flagship-lite" if variant == "FE" else "flagship",
            form_factor="bar",
            is_flagship=True,
        )

    if m := _LETTER_RE.match(name):
        letter, digits = m.group(1).upper(), m.group(2)
        num = int(digits)
        # A-series: first digit is the class (A5x upper-mid, A3x mid, A1x budget)
        cls = num // 10 if num >= 10 else num
        if letter == "A":
            if cls >= 70:
                tier = "upper-mid-range"
            elif cls >= 50:
                tier = "upper-mid-range"
            elif cls >= 30:
                tier = "mid-range"
            else:
                tier = "budget"
        elif letter == "M":
            tier = "mid-range" if cls >= 30 else "budget"
        else:
            tier = "budget"
        return dict(
            series=f"Galaxy {letter}",
            generation=num,
            variant="base",
            family_key=f"Galaxy {letter}",
            tier=tier,
            form_factor="bar",
            is_flagship=False,
        )

    return dict(
        series=None,
        generation=None,
        variant=None,
        family_key=None,
        tier="other",
        form_factor="bar",
        is_flagship=False,
    )


# --------------------------------------------------------------- discover
def _parse_listing(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    makers = soup.find("div", class_="makers")
    if not makers:
        return []
    out: list[tuple[str, str]] = []
    for li in makers.find_all("li"):
        a = li.find("a")
        if not a or not a.get("href"):
            continue
        span = a.find("span")
        name = span.get_text(" ", strip=True) if span else a.get_text(" ", strip=True)
        out.append((name, a["href"]))
    return out


def discover(fetcher: PageFetcher, pages: int | None = None) -> list[CatalogEntry]:
    """Crawl the popularity-sorted brand listing and classify every phone."""
    pages = pages or settings.scraper.popularity_pages
    entries: list[CatalogEntry] = []
    seen: set[str] = set()
    rank = 0

    for page in range(1, pages + 1):
        url = settings.scraper.popularity_url.format(page=page)
        html = fetcher.get(url, cache_key=f"_listing_pop_p{page}")
        rows = _parse_listing(html)
        log.info("popularity page %d -> %d listings", page, len(rows))
        for name, href in rows:
            rank += 1
            slug = href.rsplit("/", 1)[-1].removesuffix(".php")
            if slug in seen:
                continue
            seen.add(slug)
            if NON_PHONE_PATTERNS.search(name):
                continue
            gid = None
            if m := re.search(r"-(\d+)$", slug):
                gid = int(m.group(1))
            cls = classify(name)
            entries.append(
                CatalogEntry(
                    slug=slug,
                    gsmarena_id=gid,
                    short_name=name,
                    model_name=f"Samsung {name}",
                    url=f"{settings.scraper.base_url}/{href}",
                    popularity_rank=rank,
                    **cls,
                )
            )
        if page < pages:
            time.sleep(settings.scraper.delay_seconds)

    log.info("discovered %d Samsung phones (non-phone products filtered out)", len(entries))
    return entries


# ----------------------------------------------------------------- select
# Guarantees that every major flagship line is present. Quotas are filled
# from the popularity ranking, so within a family the most-viewed win.
FLAGSHIP_QUOTAS: dict[str, int] = {
    # Six deep on the two headline lines so every generation from the S21 to the
    # current release is covered, rather than only the newest five.
    "Galaxy S:Ultra": 6,
    "Galaxy S:base": 6,
    "Galaxy S:Plus": 2,
    "Galaxy S:FE": 2,
    "Galaxy S:Edge": 1,
    "Galaxy Z Fold": 3,
    "Galaxy Z Flip": 2,
    "Galaxy Note": 2,
}


def select(entries: Iterable[CatalogEntry], target: int | None = None) -> list[CatalogEntry]:
    target = target or settings.scraper.target_count
    pool = sorted(entries, key=lambda e: e.popularity_rank)
    chosen: dict[str, CatalogEntry] = {}

    # Pass 1 -- flagship coverage.
    for family, quota in FLAGSHIP_QUOTAS.items():
        taken = 0
        for e in pool:
            if taken >= quota or e.slug in chosen:
                continue
            if e.family_key == family:
                e.selected_reason = f"flagship quota [{family}] #{taken + 1}/{quota}"
                chosen[e.slug] = e
                taken += 1
        if taken < quota:
            log.warning("flagship family %-18s only %d/%d available", family, taken, quota)

    if len(chosen) > target:
        log.warning("flagship quotas (%d) exceed target (%d); trimming least popular",
                    len(chosen), target)
        keep = sorted(chosen.values(), key=lambda e: e.popularity_rank)[:target]
        chosen = {e.slug: e for e in keep}

    # Pass 2 -- fill remaining slots with the most popular phones overall.
    for e in pool:
        if len(chosen) >= target:
            break
        if e.slug in chosen:
            continue
        e.selected_reason = f"popularity rank #{e.popularity_rank}"
        chosen[e.slug] = e

    final = sorted(chosen.values(), key=lambda e: e.popularity_rank)
    log.info(
        "selected %d phones | %d flagship, %d other",
        len(final),
        sum(1 for e in final if e.is_flagship),
        sum(1 for e in final if not e.is_flagship),
    )
    return final


def save(entries: list[CatalogEntry]) -> None:
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "gsmarena.com popularity ranking (brand 9, sort r1)",
        "count": len(entries),
        "phones": [e.to_dict() for e in entries],
    }
    settings.paths.catalog.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("catalog written -> %s", settings.paths.catalog)


def load() -> list[CatalogEntry]:
    data = json.loads(settings.paths.catalog.read_text(encoding="utf-8"))
    return [CatalogEntry(**p) for p in data["phones"]]
