"""CRITIC -- Review Writer.

Turns a spec sheet into a product review. This is the "generate a detailed
review by combining the specifications" half of the brief: SPECTRA supplies the
facts, CRITIC supplies the judgement.

Judgement is still bounded by the database -- CRITIC positions a device by
comparing it against the corpus percentile for each metric, computed in SQL,
rather than against outside knowledge of the market.
"""
from __future__ import annotations

from typing import Any

from agents.base import Agent, AgentCard, AgentContext, Envelope
from backend.core.logging_setup import get_logger
from database import engine
from backend.llm.ollama_client import LLMUnavailable, client

log = get_logger("agents.critic")

SYSTEM = (
    "You are CRITIC, a reviewer in a Samsung phone advisory system. You are given "
    "a device's specification sheet from a PostgreSQL database and its standing "
    "against the rest of the catalogue.\n"
    "Rules:\n"
    "- Use ONLY the supplied figures. Never invent a specification, price, or score.\n"
    "- Where a field says NOT PUBLISHED, say the data is unavailable. Do not guess.\n"
    "- Base every judgement on the supplied catalogue standings, not outside knowledge.\n"
    "- Do not mention competitors from other brands; the database holds Samsung only.\n"
    "- The 'Gaps in the data' section must list ONLY the fields named in the "
    "FIELDS WITH NO DATA block. Never claim a field is missing if it is not in "
    "that block, and never claim one is present if it is.\n"
    "- Write plainly. No marketing copy, no invented user quotes."
)

# Fields worth reporting as a data gap when they are NULL, with readable names.
GAP_FIELDS: list[tuple[str, str]] = [
    ("ip_rating", "ingress protection (IP) rating"),
    ("battery_endurance_hours", "measured battery endurance"),
    ("antutu_score", "AnTuTu benchmark score"),
    ("geekbench_score", "GeekBench score"),
    ("peak_brightness_nits", "peak display brightness"),
    ("display_protection", "screen protection glass"),
    ("charging_wired_w", "wired charging power"),
    ("charging_wireless_w", "wireless charging power"),
    ("price_usd", "USD price"),
    ("price_eur", "EUR price"),
    ("main_camera_video", "rear video recording modes"),
    ("selfie_camera_mp", "front camera resolution"),
    ("os_updates_promised", "promised OS update count"),
    ("display_ppi", "pixel density"),
    ("sensors_text", "sensor list"),
]

PERCENTILE_METRICS = [
    ("antutu_score", "AnTuTu score", "points", "higher"),
    ("battery_capacity_mah", "battery capacity", "mAh", "higher"),
    ("battery_endurance_hours", "measured endurance", "hours", "higher"),
    ("charging_wired_w", "wired charging", "W", "higher"),
    ("main_camera_mp", "main camera resolution", "MP", "higher"),
    ("display_size_in", "screen size", "inches", "higher"),
    ("display_refresh_hz", "refresh rate", "Hz", "higher"),
    ("peak_brightness_nits", "peak brightness", "nits", "higher"),
    ("weight_g", "weight", "g", "lower"),
    ("price_usd", "price", "USD", "lower"),
]


class CriticAgent(Agent):
    card = AgentCard(
        name="CRITIC",
        role="Review Writer",
        summary="Combines a device's specifications with its rank against the "
                "catalogue to produce a grounded product review.",
        icon="pen",
        accent="#ef4444",
        capabilities=(
            "catalogue percentile positioning",
            "strengths and weaknesses synthesis",
            "grounded review generation",
        ),
        protocols=("ACP/1.0", "PG-WIRE/3.0", "OLLAMA-HTTP/1.1"),
        reads_database=True,
        uses_llm=True,
    )

    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        sheets: list[dict[str, Any]] = msg.payload.get("sheets") or []
        rendered: list[str] = msg.payload.get("rendered") or []
        question: str = msg.payload.get("question", ctx.question)

        if not sheets:
            return msg.reply(
                "review.result",
                {"answer": "No device from the database was identified to review.",
                 "standings": []},
                status="error",
            )

        phone = sheets[0]["phone"]
        standings = self._standings(phone, ctx)
        gaps = self._gaps(phone)

        ctx.trace.agent(
            "agent.finding",
            f"positioned {phone['model_name']} on {len(standings)} metric(s) "
            f"against the {standings[0]['population'] if standings else 0}-device "
            f"catalogue; {len(gaps)} reportable data gap(s)",
            agent=self.name,
            detail={"standings": standings, "gaps": gaps},
        )

        gap_block = (
            "\n".join(f"- {g}" for g in gaps)
            if gaps
            else "- None. Every tracked field has a value for this device."
        )
        prompt = (
            f"User request: {question}\n\n"
            f"=== SPECIFICATION SHEET (from PostgreSQL) ===\n{rendered[0][:5000]}\n\n"
            f"=== STANDING WITHIN THIS DATABASE ===\n{self._render_standings(standings)}\n\n"
            f"=== FIELDS WITH NO DATA (authoritative) ===\n{gap_block}\n\n"
            "Write a review of this device with these sections:\n"
            "Verdict (2 sentences) / Strengths / Weaknesses / "
            "Gaps in the data / Who it suits.\n"
            "Under 400 words. Quote concrete figures from the sheet. The 'Gaps in "
            "the data' section must reproduce the block above and nothing else."
        )
        try:
            answer = client().generate(
                prompt, system=SYSTEM, trace=ctx.trace, agent=self.name,
                purpose="review generation", max_tokens=850,
            ).text
        except LLMUnavailable as exc:
            # Without the model there is no prose, but the catalogue standings
            # are already computed and are the substance of the review.
            ctx.trace.agent(
                "agent.degraded",
                f"local model unreachable ({exc}); returning the standings "
                "without narration",
                agent=self.name, status="error",
            )
            answer = (
                "The local language model is unreachable, so here is how "
                f"{phone['model_name']} stands in the database:\n\n"
                + self._render_standings(standings)
            )

        # Ranks and catalogue averages are computed from database rows, so they
        # count as evidence when SENTINEL audits the review.
        ctx.note("derived_evidence", self._render_standings(standings))

        return msg.reply(
            "review.result",
            {
                "answer": answer,
                "phone": phone["model_name"],
                "standings": standings,
                "gaps": gaps,
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _gaps(phone: dict[str, Any]) -> list[str]:
        """Which tracked fields are genuinely NULL for this device.

        Computed here rather than left to the model, which reads a long sheet
        and cannot reliably tell 'absent' from 'present but not noticed'.
        """
        return [label for col, label in GAP_FIELDS if phone.get(col) is None]

    def _standings(self, phone: dict[str, Any], ctx: AgentContext) -> list[dict[str, Any]]:
        """Where this device sits in the catalogue, per metric, computed in SQL."""
        out = []
        for col, label, unit, better in PERCENTILE_METRICS:
            value = phone.get(col)
            if value is None:
                out.append({
                    "metric": label, "unit": unit, "value": None,
                    "note": "NULL in database - not published by the source",
                })
                continue
            row = engine.fetch_one(
                f"""
                SELECT count(*)                                        AS population,
                       count(*) FILTER (WHERE {col} < %(v)s)           AS below,
                       count(*) FILTER (WHERE {col} > %(v)s)           AS above,
                       round(avg({col})::numeric, 1)                   AS catalogue_avg,
                       max({col})                                      AS catalogue_max,
                       min({col})                                      AS catalogue_min
                FROM phone_attributes WHERE {col} IS NOT NULL
                """,
                {"v": value},
                trace=ctx.trace,
                agent=self.name,
                operation=f"PERCENTILE {col}",
            )
            if not row or not row["population"]:
                continue
            pop = int(row["population"])
            better_count = int(row["above"]) if better == "higher" else int(row["below"])
            rank = better_count + 1
            out.append({
                "metric": label,
                "unit": unit,
                "value": float(value),
                "rank": rank,
                "population": pop,
                "catalogue_avg": float(row["catalogue_avg"]) if row["catalogue_avg"] else None,
                "catalogue_max": float(row["catalogue_max"]) if row["catalogue_max"] else None,
                "catalogue_min": float(row["catalogue_min"]) if row["catalogue_min"] else None,
                "better": better,
            })
        return out

    @staticmethod
    def _render_standings(standings: list[dict[str, Any]]) -> str:
        lines = []
        for s in standings:
            if s.get("value") is None:
                lines.append(f"- {s['metric']}: {s['note']}")
                continue
            lines.append(
                f"- {s['metric']}: {_n(s['value'])} {s['unit']}; "
                f"ranks {s['rank']} of {s['population']} devices in the database "
                f"({'higher' if s['better'] == 'higher' else 'lower'} is better); "
                f"catalogue average {_n(s['catalogue_avg'])}, "
                f"best {_n(s['catalogue_max'] if s['better'] == 'higher' else s['catalogue_min'])}"
            )
        return "\n".join(lines)


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
