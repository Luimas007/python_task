"""Derive typed attributes from the verbatim spec text.

Rules of the house:
  * Every extractor returns None when the source text does not state the fact.
  * Nothing is inferred from a sibling device, a series convention, or a guess.
  * The verbatim text always survives alongside the typed value, so an answer can
    always be traced back to the exact string GSMArena published.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from backend.core.logging_setup import get_logger
from scraper.parser import ParsedPage

log = get_logger("scraper.normalizer")

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], 1)
}


# ------------------------------------------------------------- primitives
def num(text: str | None, pattern: str, group: int = 1, cast=float) -> Any:
    if not text:
        return None
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(group)
    if raw is None:
        return None
    try:
        return cast(raw.replace(",", ""))
    except (ValueError, TypeError):
        return None


def yes_no(text: str | None) -> bool | None:
    """GSMArena writes 'Yes', 'No', or a description that implies presence."""
    if not text:
        return None
    t = text.strip().lower()
    if t.startswith("no"):
        return False
    if t.startswith("yes"):
        return True
    # A described feature ("microSDXC", "USB Type-C 3.2") means it exists.
    return True


def parse_month_date(text: str | None) -> date | None:
    """'2023, February 17' / '2023, February' -> date (day defaults to 1)."""
    if not text:
        return None
    m = re.search(r"(20\d{2}|19\d{2})\s*,\s*([A-Za-z]+)(?:\s+(\d{1,2}))?", text)
    if not m:
        return None
    year, month_name, day = int(m.group(1)), m.group(2).lower(), m.group(3)
    month = MONTHS.get(month_name)
    if not month:
        return None
    try:
        return date(year, month, int(day) if day else 1)
    except ValueError:
        return None


# ------------------------------------------------------------- extractors
def _display(c: dict[str, str], out: dict[str, Any]) -> None:
    dt = c.get("displaytype")
    out["display_type"] = dt
    out["display_size_in"] = num(c.get("displaysize"), r"([\d.]+)\s*inch")
    out["display_refresh_hz"] = num(dt, r"(\d{2,3})\s*Hz", cast=int)
    # "1200 nits (HBM), 1750 nits (peak)" -- take the highest figure quoted.
    if dt:
        nits = [int(n) for n in re.findall(r"(\d{3,5})\s*nits", dt, re.IGNORECASE)]
        out["peak_brightness_nits"] = max(nits) if nits else None

    res = c.get("displayresolution")
    out["display_resolution"] = res
    if res:
        if m := re.search(r"(\d{3,5})\s*[x×]\s*(\d{3,5})", res):
            out["display_width_px"] = int(m.group(1))
            out["display_height_px"] = int(m.group(2))
        out["display_ppi"] = num(res, r"(\d{2,4})\s*ppi", cast=int)
    out["display_protection"] = c.get("displayprotection")


def _body(c: dict[str, str], out: dict[str, Any]) -> None:
    dims = c.get("dimensions")
    out["dimensions_text"] = dims
    if dims:
        m = re.search(r"([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)\s*mm", dims, re.IGNORECASE)
        if m:
            out["height_mm"] = float(m.group(1))
            out["width_mm"] = float(m.group(2))
            out["thickness_mm"] = float(m.group(3))
    out["weight_g"] = num(c.get("weight"), r"([\d.]+)\s*g\b")
    out["build_text"] = c.get("build")
    out["sim_text"] = c.get("sim")

    ip_source = " ".join(filter(None, [c.get("bodyother"), c.get("build")]))
    if m := re.search(r"\b(IP\s?[0-9X]{2}\w*)\b", ip_source, re.IGNORECASE):
        out["ip_rating"] = m.group(1).replace(" ", "").upper()


def _platform(c: dict[str, str], out: dict[str, Any]) -> None:
    os_text = c.get("os")
    out["os_launch"] = os_text
    out["android_version_launch"] = num(os_text, r"Android\s+([\d.]+)")
    out["os_updates_promised"] = num(
        os_text, r"up to (\d+)\s+major", cast=int
    )

    chip = c.get("chipset")
    out["chipset"] = chip
    out["fabrication_nm"] = num(chip, r"\(([\d.]+)\s*nm\)")
    if chip:
        low = chip.lower()
        for vendor in ("Qualcomm", "Exynos", "Mediatek", "Google", "Kirin"):
            if vendor.lower() in low:
                out["chipset_vendor"] = "Samsung" if vendor == "Exynos" else vendor
                break
    out["cpu"] = c.get("cpu")
    out["gpu"] = c.get("gpu")


def _memory(c: dict[str, str], out: dict[str, Any]) -> None:
    slot = c.get("memoryslot")
    out["card_slot"] = yes_no(slot)
    mem = c.get("internalmemory")
    out["internal_memory_text"] = mem
    if not mem:
        return
    # "256GB 8GB RAM, 512GB 12GB RAM, 1TB 12GB RAM"
    rams = sorted({int(r) for r in re.findall(r"(\d+)GB\s*RAM", mem, re.IGNORECASE)})
    storage: set[int] = set()
    for value, unit in re.findall(r"(\d+)\s*(TB|GB)(?!\s*RAM)", mem, re.IGNORECASE):
        gb = int(value) * (1024 if unit.upper() == "TB" else 1)
        storage.add(gb)
    if rams:
        out["ram_options_gb"] = rams
        out["max_ram_gb"] = max(rams)
    if storage:
        out["storage_options_gb"] = sorted(storage)
        out["max_storage_gb"] = max(storage)


def _cameras(page: ParsedPage, c: dict[str, str], out: dict[str, Any]) -> None:
    # The row label carries the setup ("Quad", "Triple", "Dual", "Single").
    setup_by_code = {
        s.code: s.key for s in page.specs if s.code in ("cam1modules", "cam2modules")
    }
    out["main_camera_setup"] = setup_by_code.get("cam1modules")
    out["selfie_camera_setup"] = setup_by_code.get("cam2modules")

    main = c.get("cam1modules")
    out["main_camera_modules"] = main
    out["main_camera_mp"] = _max_mp(main)
    out["main_camera_features"] = c.get("cam1features")
    out["main_camera_video"] = c.get("cam1video")
    out["max_video_resolution"] = _max_video(c.get("cam1video"))

    selfie = c.get("cam2modules")
    out["selfie_camera_modules"] = selfie
    out["selfie_camera_mp"] = _max_mp(selfie)
    out["selfie_camera_video"] = c.get("cam2video")


def _max_mp(text: str | None) -> float | None:
    if not text:
        return None
    mps = [float(m) for m in re.findall(r"([\d.]+)\s*MP", text, re.IGNORECASE)]
    return max(mps) if mps else None


def _max_video(text: str | None) -> str | None:
    """Highest capture resolution quoted, e.g. '8K' beats '4K' beats '1080p'."""
    if not text:
        return None
    order = [("8K", 8000), ("4320p", 8000), ("4K", 4000), ("2160p", 4000),
             ("1440p", 1440), ("1080p", 1080), ("720p", 720)]
    best, best_rank = None, -1
    up = text.upper()
    for token, rank in order:
        if token.upper() in up and rank > best_rank:
            best, best_rank = token, rank
    return best


def _battery(c: dict[str, str], out: dict[str, Any]) -> None:
    bat = c.get("batdescription1")
    out["battery_type"] = bat
    out["battery_capacity_mah"] = num(bat, r"([\d,]+)\s*mAh", cast=int)

    chg = c.get("charging")
    out["charging_text"] = chg
    if chg:
        out["charging_wired_w"] = num(chg, r"([\d.]+)\s*W\s*wired")
        out["charging_wireless_w"] = num(chg, r"([\d.]+)\s*W\s*wireless")
        out["reverse_wireless_w"] = num(chg, r"([\d.]+)\s*W\s*reverse")
        if out["charging_wired_w"] is None:
            # Older pages write "Fast charging 25W" with no 'wired' keyword.
            out["charging_wired_w"] = num(chg, r"([\d.]+)\s*W")

    # "Active use score 13:24h" -> 13.4 hours
    life = c.get("batlife2")
    if life and (m := re.search(r"(\d{1,3}):(\d{2})\s*h", life)):
        out["battery_endurance_hours"] = round(int(m.group(1)) + int(m.group(2)) / 60, 1)


def _connectivity(c: dict[str, str], out: dict[str, Any]) -> None:
    tech = c.get("nettech")
    out["network_technology"] = tech
    if tech or c.get("net5g"):
        out["has_5g"] = bool(c.get("net5g")) or bool(tech and "5G" in tech)
    out["wlan"] = c.get("wlan")
    out["bluetooth_version"] = num(c.get("bluetooth"), r"^\s*([\d.]+)")
    out["has_nfc"] = yes_no(c.get("nfc"))
    out["has_fm_radio"] = yes_no(c.get("radio"))
    out["has_headphone_jack"] = yes_no(c.get("3.5mm-jack"))
    out["usb_text"] = c.get("usb")
    out["sensors_text"] = c.get("sensors")
    spk = c.get("speakers")
    if spk:
        out["has_stereo_speakers"] = "stereo" in spk.lower()


def _misc(c: dict[str, str], out: dict[str, Any]) -> None:
    out["colors_text"] = c.get("colors")
    out["model_codes"] = c.get("models")

    price = c.get("price")
    out["price_text"] = price
    if price:
        # "$ 434.99 / € 425.99 / £ 424.99 / ₹ 84,999"
        out["price_usd"] = num(price, r"\$\s*([\d,.]+)")
        out["price_eur"] = num(price, r"€\s*([\d,.]+)")
        out["price_inr"] = num(price, r"₹\s*([\d,.]+)")

    bench = c.get("tbench")
    if bench:
        out["antutu_score"] = num(bench, r"AnTuTu:\s*([\d,]+)", cast=int)
        out["geekbench_score"] = num(bench, r"GeekBench:\s*([\d,.]+)")


def _launch(c: dict[str, str], out: dict[str, Any]) -> None:
    out["announced_text"] = c.get("year")
    out["announced_date"] = parse_month_date(c.get("year"))
    status = c.get("status")
    out["release_status"] = status
    if status and "released" in status.lower():
        out["release_date"] = parse_month_date(status)


# ------------------------------------------------------------------ entry
ATTRIBUTE_COLUMNS = [
    "announced_text", "announced_date", "release_status", "release_date",
    "display_size_in", "display_type", "display_resolution", "display_width_px",
    "display_height_px", "display_refresh_hz", "display_ppi", "display_protection",
    "peak_brightness_nits", "dimensions_text", "height_mm", "width_mm",
    "thickness_mm", "weight_g", "build_text", "ip_rating", "sim_text",
    "os_launch", "android_version_launch", "os_updates_promised", "chipset",
    "chipset_vendor", "fabrication_nm", "cpu", "gpu", "card_slot",
    "internal_memory_text", "ram_options_gb", "max_ram_gb", "storage_options_gb",
    "max_storage_gb", "main_camera_setup", "main_camera_mp", "main_camera_modules",
    "main_camera_features", "main_camera_video", "max_video_resolution",
    "selfie_camera_setup", "selfie_camera_mp", "selfie_camera_modules",
    "selfie_camera_video", "battery_type", "battery_capacity_mah", "charging_text",
    "charging_wired_w", "charging_wireless_w", "reverse_wireless_w",
    "battery_endurance_hours", "network_technology", "has_5g", "wlan",
    "bluetooth_version", "has_nfc", "has_fm_radio", "has_headphone_jack",
    "usb_text", "sensors_text", "has_stereo_speakers", "colors_text",
    "model_codes", "price_text", "price_eur", "price_usd", "price_inr",
    "antutu_score", "geekbench_score",
]


def normalize(page: ParsedPage) -> dict[str, Any]:
    """ParsedPage -> one dict keyed by phone_attributes column name."""
    out: dict[str, Any] = {col: None for col in ATTRIBUTE_COLUMNS}
    c = page.by_code

    _launch(c, out)
    _display(c, out)
    _body(c, out)
    _platform(c, out)
    _memory(c, out)
    _cameras(page, c, out)
    _battery(c, out)
    _connectivity(c, out)
    _misc(c, out)

    filled = sum(1 for v in out.values() if v is not None)
    log.debug("%s: %d/%d typed attributes resolved", page.slug, filled, len(out))
    return out
