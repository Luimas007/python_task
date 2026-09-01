"""Build the retrieval corpus *from database rows*.

Every chunk is rendered out of `phones` / `phone_attributes` / `specifications`.
Nothing is written that is not already stored, so semantic search can only ever
surface facts the database holds. Where a fact is NULL the chunk says so
explicitly -- that way the LLM is told "not published" instead of being left to
fill the silence itself.
"""
from __future__ import annotations

from typing import Any, Iterable

from backend.core.logging_setup import get_logger
from database import engine

log = get_logger("rag.chunker")

NOT_PUBLISHED = "not published by the source"


def _fmt(value: Any, unit: str = "", *, none: str = NOT_PUBLISHED) -> str:
    if value is None:
        return none
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "/".join(str(v) for v in value) + unit if value else none
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value}{unit}"


def _line(label: str, value: Any, unit: str = "") -> str:
    return f"{label}: {_fmt(value, unit)}."


def fetch_phone_rows() -> list[dict[str, Any]]:
    return engine.fetch_all(
        """
        SELECT p.*, a.*
        FROM phones p
        LEFT JOIN phone_attributes a USING (phone_id)
        ORDER BY p.popularity_rank
        """,
        audit=False,
    )


def fetch_spec_rows(phone_id: int) -> list[dict[str, Any]]:
    return engine.fetch_all(
        """
        SELECT category, spec_key, spec_value
        FROM specifications
        WHERE phone_id = %s
        ORDER BY spec_id
        """,
        (phone_id,),
        audit=False,
    )


# ------------------------------------------------------------------ chunks
def build_chunks(row: dict[str, Any], specs: list[dict[str, Any]]) -> list[dict[str, str]]:
    name = row["model_name"]
    out: list[dict[str, str]] = []

    def add(section: str, heading: str, lines: Iterable[str]) -> None:
        body = " ".join(l for l in lines if l)
        out.append(
            {
                "section": section,
                "heading": f"{name} - {heading}",
                "content": f"{name} - {heading}. {body}",
            }
        )

    add("overview", "Overview", [
        f"{name} is a Samsung {_fmt(row.get('tier'), none='phone')} in the"
        f" {_fmt(row.get('series'), none='Galaxy')} line.",
        _line("Form factor", row.get("form_factor")),
        _line("Announced", row.get("announced_text")),
        _line("Market status", row.get("release_status")),
        _line("GSMArena popularity rank at capture", row.get("popularity_rank")),
        _line("Page views", row.get("popularity_hits")),
        f"Flagship model: {'yes' if row.get('is_flagship') else 'no'}.",
    ])

    add("display", "Display", [
        _line("Screen size", row.get("display_size_in"), " inches"),
        _line("Panel type", row.get("display_type")),
        _line("Resolution", row.get("display_resolution")),
        _line("Refresh rate", row.get("display_refresh_hz"), " Hz"),
        _line("Pixel density", row.get("display_ppi"), " ppi"),
        _line("Peak brightness", row.get("peak_brightness_nits"), " nits"),
        _line("Screen protection", row.get("display_protection")),
    ])

    add("performance", "Performance and memory", [
        _line("Chipset", row.get("chipset")),
        _line("Chipset vendor", row.get("chipset_vendor")),
        _line("Fabrication process", row.get("fabrication_nm"), " nm"),
        _line("CPU", row.get("cpu")),
        _line("GPU", row.get("gpu")),
        _line("RAM options", row.get("ram_options_gb"), " GB"),
        _line("Maximum RAM", row.get("max_ram_gb"), " GB"),
        _line("Storage options", row.get("storage_options_gb"), " GB"),
        _line("Maximum storage", row.get("max_storage_gb"), " GB"),
        _line("Expandable storage via card slot", row.get("card_slot")),
        _line("AnTuTu benchmark score", row.get("antutu_score")),
        _line("GeekBench score", row.get("geekbench_score")),
        _line("Launch software", row.get("os_launch")),
        _line("Promised major OS updates", row.get("os_updates_promised")),
    ])

    add("camera", "Cameras", [
        _line("Rear camera setup", row.get("main_camera_setup")),
        _line("Highest rear sensor resolution", row.get("main_camera_mp"), " MP"),
        _line("Rear camera modules", row.get("main_camera_modules")),
        _line("Rear camera features", row.get("main_camera_features")),
        _line("Rear video recording", row.get("main_camera_video")),
        _line("Maximum video resolution", row.get("max_video_resolution")),
        _line("Front camera setup", row.get("selfie_camera_setup")),
        _line("Front camera resolution", row.get("selfie_camera_mp"), " MP"),
        _line("Front camera module", row.get("selfie_camera_modules")),
        _line("Front video recording", row.get("selfie_camera_video")),
    ])

    add("battery", "Battery and charging", [
        _line("Battery", row.get("battery_type")),
        _line("Battery capacity", row.get("battery_capacity_mah"), " mAh"),
        _line("Charging summary", row.get("charging_text")),
        _line("Wired charging power", row.get("charging_wired_w"), " W"),
        _line("Wireless charging power", row.get("charging_wireless_w"), " W"),
        _line("Reverse wireless charging", row.get("reverse_wireless_w"), " W"),
        _line("Measured active-use endurance", row.get("battery_endurance_hours"), " hours"),
    ])

    add("design", "Design and build", [
        _line("Dimensions", row.get("dimensions_text")),
        _line("Thickness", row.get("thickness_mm"), " mm"),
        _line("Weight", row.get("weight_g"), " g"),
        _line("Build materials", row.get("build_text")),
        _line("Ingress protection rating", row.get("ip_rating")),
        _line("SIM support", row.get("sim_text")),
        _line("Colour options", row.get("colors_text")),
    ])

    add("connectivity", "Connectivity and features", [
        _line("Network technology", row.get("network_technology")),
        _line("5G support", row.get("has_5g")),
        _line("Wi-Fi", row.get("wlan")),
        _line("Bluetooth version", row.get("bluetooth_version")),
        _line("NFC", row.get("has_nfc")),
        _line("FM radio", row.get("has_fm_radio")),
        _line("3.5mm headphone jack", row.get("has_headphone_jack")),
        _line("Stereo speakers", row.get("has_stereo_speakers")),
        _line("USB", row.get("usb_text")),
        _line("Sensors", row.get("sensors_text")),
    ])

    add("pricing", "Pricing and identifiers", [
        _line("Listed price", row.get("price_text")),
        _line("Price in USD", row.get("price_usd")),
        _line("Price in EUR", row.get("price_eur")),
        _line("Price in INR", row.get("price_inr")),
        _line("Model codes", row.get("model_codes")),
    ])

    # A verbatim dump of the source spec sheet, so exact strings stay retrievable.
    by_cat: dict[str, list[str]] = {}
    for s in specs:
        val = s["spec_value"]
        by_cat.setdefault(s["category"], []).append(
            f"{s['spec_key']}: {val if val is not None else NOT_PUBLISHED}"
        )
    for cat, lines in by_cat.items():
        out.append(
            {
                "section": f"spec_sheet:{cat.lower()}",
                "heading": f"{name} - {cat} specification sheet",
                "content": f"{name} - {cat} specifications. " + " | ".join(lines),
            }
        )

    return out


def build_all() -> list[dict[str, Any]]:
    rows = fetch_phone_rows()
    chunks: list[dict[str, Any]] = []
    for row in rows:
        specs = fetch_spec_rows(row["phone_id"])
        for c in build_chunks(row, specs):
            chunks.append({"phone_id": row["phone_id"], **c})
    log.info("built %d chunks from %d phones", len(chunks), len(rows))
    return chunks
