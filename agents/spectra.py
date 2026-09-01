"""SPECTRA -- Specification Retrieval.

The only agent that reads full device records. It pulls typed attributes and the
verbatim spec sheet straight out of PostgreSQL and hands them to whichever
analyst agent asked, which is the "one agent fetches, another reasons" split the
brief calls for.

SPECTRA never writes prose and never calls the LLM.
"""
from __future__ import annotations

from typing import Any

from agents.base import Agent, AgentCard, AgentContext, Envelope
from backend.core.logging_setup import get_logger
from database import repository as repo

log = get_logger("agents.spectra")

# Category subsets so a focused question does not drag the whole sheet along.
FOCUS_CATEGORIES: dict[str, list[str]] = {
    "camera": ["Main Camera", "Selfie Camera"],
    "battery": ["Battery"],
    "display": ["Display"],
    "performance": ["Platform", "Memory", "Tests"],
    "design": ["Body"],
    "connectivity": ["Network", "Comms", "Sound", "Features"],
    "pricing": ["Misc"],
}


class SpectraAgent(Agent):
    card = AgentCard(
        name="SPECTRA",
        role="Specification Retrieval",
        summary="Fetches typed attributes and the verbatim spec sheet for named "
                "devices directly from PostgreSQL.",
        icon="database",
        accent="#0ea5e9",
        capabilities=(
            "full spec sheet retrieval",
            "focused category retrieval",
            "NULL-aware fact reporting",
        ),
        protocols=("ACP/1.0", "PG-WIRE/3.0"),
        reads_database=True,
        uses_llm=False,
    )


    def activity(self, msg: Envelope, ctx: AgentContext) -> str:
        names = repo.display_names(msg.payload.get("phone_ids") or [])
        focus = msg.payload.get("focus")
        what = f"{focus} specifications" if focus else "the full specification sheet"
        return (f"Fetching {what} for {names} from PostgreSQL" if names
                else f"Fetching {what} from PostgreSQL")

    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        phone_ids: list[int] = msg.payload.get("phone_ids") or []
        focus: str | None = msg.payload.get("focus")
        categories = FOCUS_CATEGORIES.get(focus or "", None)

        sheets: list[dict[str, Any]] = []
        for pid in phone_ids:
            sheet = repo.spec_sheet(pid, categories, trace=ctx.trace, agent=self.name)
            if sheet:
                sheets.append(sheet)

        rendered = [self.render(s) for s in sheets]
        null_total = sum(self._count_nulls(s) for s in sheets)

        ctx.note("spec_sheets", sheets)
        ctx.trace.agent(
            "agent.finding",
            f"retrieved {len(sheets)} spec sheet(s)"
            + (f" scoped to {focus}" if focus else " (full)")
            + f"; {null_total} field(s) recorded as NULL in source",
            agent=self.name,
            detail={
                "phones": [s["phone"]["model_name"] for s in sheets],
                "focus": focus,
                "categories": categories,
                "null_fields": null_total,
            },
        )
        return msg.reply(
            "specs.result",
            {
                "sheets": sheets,
                "rendered": rendered,
                "focus": focus,
                "null_fields": null_total,
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _count_nulls(sheet: dict[str, Any]) -> int:
        return sum(
            1
            for rows in sheet["specs_by_category"].values()
            for r in rows
            if r["value"] is None
        )

    @staticmethod
    def render(sheet: dict[str, Any]) -> str:
        """Flatten one sheet into the grounded text block given to the LLM."""
        p = sheet["phone"]
        lines = [f"### {p['model_name']}"]

        def add(label: str, value: Any, unit: str = "") -> None:
            if value is None:
                lines.append(f"- {label}: NOT PUBLISHED (NULL in database)")
            elif isinstance(value, bool):
                # Spell booleans out. A bare "False" in the prompt gets read as
                # a value rather than a negation, and the answer comes back
                # inverted.
                lines.append(f"- {label}: {'Yes' if value else 'No'}")
            else:
                lines.append(f"- {label}: {value}{unit}")

        add("Series", p.get("series"))
        add("Tier", p.get("tier"))
        add("Announced", p.get("announced_text"))
        add("Release status", p.get("release_status"))
        add("Display", p.get("display_type"))
        add("Screen size", p.get("display_size_in"), " in")
        add("Resolution", p.get("display_resolution"))
        add("Refresh rate", p.get("display_refresh_hz"), " Hz")
        add("Peak brightness", p.get("peak_brightness_nits"), " nits")
        add("Chipset", p.get("chipset"))
        add("CPU", p.get("cpu"))
        add("GPU", p.get("gpu"))
        add("AnTuTu", p.get("antutu_score"))
        add("GeekBench", p.get("geekbench_score"))
        add("RAM options", p.get("ram_options_gb"), " GB")
        add("Storage options", p.get("storage_options_gb"), " GB")
        add("Card slot", p.get("card_slot"))
        add("Rear camera", p.get("main_camera_modules"))
        add("Rear camera setup", p.get("main_camera_setup"))
        add("Max rear resolution", p.get("main_camera_mp"), " MP")
        add("Rear video", p.get("main_camera_video"))
        add("Front camera", p.get("selfie_camera_modules"))
        add("Battery", p.get("battery_type"))
        add("Battery capacity", p.get("battery_capacity_mah"), " mAh")
        add("Wired charging", p.get("charging_wired_w"), " W")
        add("Wireless charging", p.get("charging_wireless_w"), " W")
        add("Measured endurance", p.get("battery_endurance_hours"), " h")
        add("Weight", p.get("weight_g"), " g")
        add("Thickness", p.get("thickness_mm"), " mm")
        add("Build", p.get("build_text"))
        add("IP rating", p.get("ip_rating"))
        add("Launch OS", p.get("os_launch"))
        add("Promised OS updates", p.get("os_updates_promised"))
        add("5G", p.get("has_5g"))
        add("NFC", p.get("has_nfc"))
        add("Headphone jack", p.get("has_headphone_jack"))
        add("Listed price", p.get("price_text"))
        add("Colours", p.get("colors_text"))

        for category, rows in sheet["specs_by_category"].items():
            shown = [r for r in rows if r["value"] is not None]
            absent = [r["key"] for r in rows if r["value"] is None]
            if shown:
                lines.append(f"  [{category}] " + " | ".join(
                    f"{r['key']}: {r['value']}" for r in shown
                ))
            # State absences explicitly. Without this the model sees only the
            # fields that exist and has to infer what is missing, which it does
            # badly -- it invents gaps that are not there and misses real ones.
            if absent:
                lines.append(
                    f"  [{category}] NOT PUBLISHED (NULL in database): "
                    + ", ".join(absent)
                )
        return "\n".join(lines)
