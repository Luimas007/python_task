"""VERSUS -- Comparison Analyst.

Consumes the spec sheets SPECTRA fetched and writes the comparison. Every
numeric comparison is decided in Python -- a 3B model is not trustworthy
arithmetic -- and the LLM is left to narrate a result it cannot get wrong.
"""
from __future__ import annotations

from typing import Any

from agents.base import Agent, AgentCard, AgentContext, Envelope
from backend.core.logging_setup import get_logger
from database import repository as repo
from backend.llm.ollama_client import LLMUnavailable, client

log = get_logger("agents.versus")

SYSTEM = (
    "You are VERSUS, a comparison analyst in a Samsung phone advisory system. "
    "You are given a verified comparison drawn from a PostgreSQL database: each "
    "metric lists one value per device and states which device leads. Write the "
    "comparison using ONLY those figures.\n"
    "Rules:\n"
    "- Every number you write must be copied from a device's own value line.\n"
    "- Never state a number that is not in the source, and never do arithmetic "
    "of your own -- which device leads is already decided for you.\n"
    "- If a field says NOT PUBLISHED, say the source does not publish it. Do not estimate.\n"
    "- Be specific and concise. No marketing language."
)

# Fields worth calling out, with the direction that counts as an advantage.
DELTA_FIELDS: list[tuple[str, str, str, str]] = [
    # (column, label, unit, better)
    ("antutu_score", "AnTuTu score", "points", "higher"),
    ("geekbench_score", "GeekBench score", "points", "higher"),
    ("battery_capacity_mah", "battery capacity", "mAh", "higher"),
    ("battery_endurance_hours", "measured endurance", "h", "higher"),
    ("charging_wired_w", "wired charging", "W", "higher"),
    ("charging_wireless_w", "wireless charging", "W", "higher"),
    ("main_camera_mp", "main camera", "MP", "higher"),
    ("selfie_camera_mp", "front camera", "MP", "higher"),
    ("display_size_in", "screen size", "in", "higher"),
    ("display_refresh_hz", "refresh rate", "Hz", "higher"),
    ("display_ppi", "pixel density", "ppi", "higher"),
    ("peak_brightness_nits", "peak brightness", "nits", "higher"),
    ("max_ram_gb", "maximum RAM", "GB", "higher"),
    ("max_storage_gb", "maximum storage", "GB", "higher"),
    ("weight_g", "weight", "g", "lower"),
    ("thickness_mm", "thickness", "mm", "lower"),
    ("price_usd", "price", "USD", "lower"),
]


class VersusAgent(Agent):
    card = AgentCard(
        name="VERSUS",
        role="Comparison Analyst",
        summary="Builds the side-by-side matrix, computes numeric deltas in code, "
                "and narrates the differences.",
        icon="scale",
        accent="#a855f7",
        capabilities=(
            "side-by-side matrix assembly",
            "deterministic delta computation",
            "grounded comparative narration",
        ),
        protocols=("ACP/1.0", "PG-WIRE/3.0", "OLLAMA-HTTP/1.1"),
        reads_database=True,
        uses_llm=True,
    )

    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        phone_ids: list[int] = msg.payload.get("phone_ids") or []
        question: str = msg.payload.get("question", ctx.question)

        if len(phone_ids) < 2:
            return msg.reply(
                "comparison.result",
                {
                    "answer": "A comparison needs at least two devices that exist "
                              "in the database.",
                    "deltas": [],
                },
                status="error",
            )

        matrix = repo.compare_matrix(
            phone_ids, trace=ctx.trace, agent=self.name
        )
        deltas = self._deltas(matrix)
        table = self._render_table(matrix)

        ctx.trace.agent(
            "agent.finding",
            f"computed {len(deltas)} comparable metric delta(s) across "
            f"{len(matrix['phones'])} device(s)",
            agent=self.name,
            detail={"deltas": deltas[:12]},
        )

        # Lead with what actually differs. Handing a small model the full table
        # plus every spec sheet buries the three lines that matter and it starts
        # misquoting figures that are right there in the prompt.
        differing = [d for d in deltas if d["comparable"] and d["advantage"]]
        identical = [d for d in deltas if d["comparable"] and not d["advantage"]]
        incomparable = [d for d in deltas if not d["comparable"]]

        prompt = (
            f"User question: {question}\n\n"
            "=== WHERE THEY DIFFER (authoritative, already computed) ===\n"
            f"{self._render_deltas(differing, show_difference=False) or 'No measured metric differs.'}\n\n"
            "=== IDENTICAL ON ===\n"
            f"{self._render_deltas(identical, show_difference=False) or 'Nothing.'}\n\n"
            "=== NOT COMPARABLE (value missing for at least one device) ===\n"
            f"{self._render_deltas(incomparable, show_difference=False) or 'Nothing.'}\n\n"
            f"=== FULL TABLE (for non-numeric fields such as chipset and OS) ===\n"
            f"{table}\n\n"
            "Write the comparison as flowing prose in short paragraphs. Do NOT "
            "reproduce the bullet lists above or their wording; they are source "
            "data, not the answer. Open with a one-line verdict, then discuss "
            "only the areas under WHERE THEY DIFFER, citing the figures. Note "
            "anything under NOT COMPARABLE as missing data. Close with who each "
            "device suits. Under 320 words."
        )
        try:
            answer = client().generate(
                prompt, system=SYSTEM, trace=ctx.trace, agent=self.name,
                purpose="comparison narrative", max_tokens=700,
            ).text
        except LLMUnavailable as exc:
            # The comparison itself is already computed; only the prose needs
            # the model. Hand back the figures rather than nothing.
            ctx.trace.agent(
                "agent.degraded",
                f"local model unreachable ({exc}); returning the computed "
                "comparison without narration",
                agent=self.name, status="error",
            )
            answer = (
                "The local language model is unreachable, so here is the "
                "computed comparison straight from the database:\n\n"
                + self._render_deltas(deltas)
            )

        # The gap and percentage figures are derived from database values, so
        # they are legitimate evidence. Without this SENTINEL sees them for the
        # first time in the answer and reports them as unsupported.
        ctx.note("derived_evidence", self._render_deltas(deltas) + "\n" + table)

        return msg.reply(
            "comparison.result",
            {
                "answer": answer,
                "matrix": matrix,
                "deltas": deltas,
                "table": table,
            },
        )

    # ------------------------------------------------------------------
    def _deltas(self, matrix: dict[str, Any]) -> list[dict[str, Any]]:
        """Numeric differences between the first two devices, computed in code."""
        phones = matrix["phones"]
        if len(phones) < 2:
            return []
        a, b = phones[0], phones[1]
        out = []
        for col, label, unit, better in DELTA_FIELDS:
            va, vb = a.get(col), b.get(col)
            if va is None or vb is None:
                out.append({
                    "metric": label, "unit": unit,
                    "a": a["model_name"], "b": b["model_name"],
                    "a_value": va, "b_value": vb,
                    "comparable": False,
                    "note": "not comparable - value is NULL for at least one device",
                })
                continue
            va, vb = float(va), float(vb)
            diff = round(va - vb, 2)
            if diff == 0:
                winner = None
            elif better == "higher":
                winner = a["model_name"] if diff > 0 else b["model_name"]
            else:
                winner = a["model_name"] if diff < 0 else b["model_name"]
            pct = round(abs(diff) / vb * 100, 1) if vb else None
            out.append({
                "metric": label, "unit": unit,
                "a": a["model_name"], "b": b["model_name"],
                "a_value": va, "b_value": vb,
                "difference": abs(diff), "percent": pct,
                "advantage": winner, "comparable": True,
            })
        return out

    @staticmethod
    def _render_deltas(deltas: list[dict[str, Any]], show_difference: bool = True) -> str:
        """One block per metric, each figure on its own labelled line.

        `show_difference` is False for the prompt. A 3B model shown both the two
        values and their gap reliably quotes the gap as if it were one device's
        value -- "a 150 MP sensor" when the figures were 50 MP and 200 MP. The
        two values carry the comparison on their own, so the model is only ever
        told which side leads, never by how much. The gap is still computed and
        returned in the API payload for callers that want it.
        """
        blocks = []
        for d in deltas:
            if not d["comparable"]:
                blocks.append(
                    f"- {d['metric']}: {d['note']}\n"
                    f"    {d['a']} = {_n(d['a_value'])}\n"
                    f"    {d['b']} = {_n(d['b_value'])}"
                )
            elif d["advantage"] is None:
                blocks.append(
                    f"- {d['metric']}: both devices are identical at "
                    f"{_n(d['a_value'])} {d['unit']}"
                )
            else:
                block = (
                    f"- {d['metric']}:\n"
                    f"    {d['a']} = {_n(d['a_value'])} {d['unit']}\n"
                    f"    {d['b']} = {_n(d['b_value'])} {d['unit']}\n"
                    f"    -> {d['advantage']} leads on this metric"
                )
                if show_difference:
                    block += (
                        f" (difference between the two figures: "
                        f"{_n(d['difference'])} {d['unit']})"
                    )
                blocks.append(block)
        return "\n".join(blocks)

    @staticmethod
    def _render_table(matrix: dict[str, Any]) -> str:
        phones = matrix["phones"]
        names = [p["model_name"] for p in phones]
        width = max((len(n) for n in names), default=10)
        lines = ["Attribute".ljust(26) + " | " + " | ".join(n.ljust(width) for n in names)]
        lines.append("-" * len(lines[0]))
        for col in matrix["columns"]:
            cells = []
            for p in phones:
                v = p.get(col)
                cells.append(("NOT PUBLISHED" if v is None else str(v))[:width].ljust(width))
            lines.append(col.ljust(26) + " | " + " | ".join(cells))
        return "\n".join(lines)


def _n(v: Any) -> str:
    if v is None:
        return "NOT PUBLISHED"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
