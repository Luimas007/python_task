"""ATLAS -- Query Analyst.

First agent in every run. Works out what the user is asking for and which
devices in the database they mean. Classification is rule-first (fast and
deterministic) and falls back to the LLM only when the rules are ambiguous.

ATLAS resolves device names *against the phones table*, so a model the database
does not hold is reported as unknown rather than answered from memory.
"""
from __future__ import annotations

import re
from typing import Any

from agents.prompts import ATLAS_CLASSIFY
from agents.base import Agent, AgentCard, AgentContext, Envelope
from backend.core.logging_setup import get_logger
from database import repository as repo
from backend.llm.ollama_client import client

log = get_logger("agents.atlas")

INTENTS = ("spec_lookup", "compare", "ranking", "review", "general")

# --- rule signals -----------------------------------------------------------
COMPARE_RE = re.compile(
    r"\b(compare|comparison|versus|vs\.?|better than|difference between|"
    r"differ|which of|over the)\b", re.IGNORECASE)
RANK_RE = re.compile(
    r"\b(best|worst|top|highest|lowest|biggest|largest|smallest|longest|"
    r"shortest|most|least|fastest|slowest|lightest|heaviest|thinnest|"
    r"cheapest|rank|ranking)\b", re.IGNORECASE)
REVIEW_RE = re.compile(
    r"\b(review|verdict|worth (it|buying)|should i buy|opinion|pros and cons|"
    r"strengths and weaknesses|impressions)\b", re.IGNORECASE)

# Metric keywords -> whitelisted ranking column.
METRIC_HINTS: list[tuple[str, str]] = [
    (r"battery life|endurance|last(s)? longer|screen[- ]on time", "battery_endurance_hours"),
    (r"battery|mah|capacity", "battery_capacity_mah"),
    (r"charg\w*\s*(speed|power|watt|fast)|fast charg", "charging_wired_w"),
    (r"wireless charg", "charging_wireless_w"),
    (r"selfie|front camera", "selfie_camera_mp"),
    (r"camera|megapixel|\bmp\b|photo", "main_camera_mp"),
    (r"refresh rate|hz\b", "display_refresh_hz"),
    (r"bright(ness)?|nits", "peak_brightness_nits"),
    (r"sharp|pixel density|ppi", "display_ppi"),
    (r"screen|display|big(gest)? screen|size", "display_size_in"),
    (r"antutu", "antutu_score"),
    (r"geekbench", "geekbench_score"),
    (r"perform\w*|fast(est)?|powerful|speed|benchmark|chipset|processor", "antutu_score"),
    (r"ram|memory", "max_ram_gb"),
    (r"storage", "max_storage_gb"),
    (r"light(est)?|weigh\w*|heav\w*", "weight_g"),
    (r"thin(nest)?|thick\w*", "thickness_mm"),
    (r"cheap(est)?|afford\w*|price|cost|budget", "price_usd"),
]

# "worst / lowest / cheapest" flips the natural direction of a metric.
ASC_RE = re.compile(r"\b(worst|lowest|smallest|shortest|least|cheapest|"
                    r"lightest|thinnest|slowest)\b", re.IGNORECASE)
DESC_RE = re.compile(r"\b(best|highest|biggest|largest|longest|most|"
                     r"fastest|heaviest|thickest|expensive)\b", re.IGNORECASE)



class AtlasAgent(Agent):
    card = AgentCard(
        name="ATLAS",
        role="Query Analyst",
        summary="Reads the question, classifies intent, and resolves device names "
                "against the phones table.",
        icon="compass",
        accent="#6366f1",
        capabilities=(
            "intent classification",
            "device entity resolution",
            "metric selection",
        ),
        protocols=("ACP/1.0", "PG-WIRE/3.0", "OLLAMA-HTTP/1.1"),
        reads_database=True,
        uses_llm=True,
    )


    def activity(self, msg: Envelope, ctx: AgentContext) -> str:
        return "Reading your question and identifying which phones you mean"

    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        question = msg.payload.get("question", ctx.question)

        matched, unresolved = repo.resolve_phones(
            question, trace=ctx.trace, agent=self.name
        )
        intent, how = self._classify(question, matched, ctx)
        metric, direction = self._metric(question)

        payload: dict[str, Any] = {
            "intent": intent,
            "classified_by": how,
            "phones": [
                {
                    "phone_id": p["phone_id"],
                    "model_name": p["model_name"],
                    "series": p["series"],
                    "tier": p["tier"],
                }
                for p in matched
            ],
            "unresolved_mentions": unresolved,
            "metric": metric,
            "direction": direction,
        }
        ctx.note("intent", intent)
        ctx.note("phones", matched)
        ctx.note("metric", metric)

        ctx.trace.agent(
            "agent.finding",
            f"intent={intent} ({how}); resolved {len(matched)} device(s)"
            + (f"; metric={metric}" if metric else "")
            + (f"; UNKNOWN: {', '.join(unresolved)}" if unresolved else ""),
            agent=self.name,
            detail=payload,
        )
        return msg.reply("analysis.result", payload)

    # ------------------------------------------------------------------
    def _classify(
        self, q: str, matched: list[dict], ctx: AgentContext
    ) -> tuple[str, str]:
        if REVIEW_RE.search(q):
            return "review", "rule:review"
        # Two named devices plus comparative language is unambiguous.
        if COMPARE_RE.search(q) and len(matched) >= 2:
            return "compare", "rule:compare"
        if len(matched) >= 2 and RANK_RE.search(q):
            return "compare", "rule:compare(implicit)"
        if RANK_RE.search(q) and len(matched) <= 1:
            return "ranking", "rule:ranking"
        if COMPARE_RE.search(q):
            return "compare", "rule:compare(weak)"
        if matched:
            return "spec_lookup", "rule:spec_lookup"

        # No device named and no comparative or superlative cue: ask the model.
        try:
            data = client().generate_json(
                f'Question: "{q}"\nReply with JSON only.',
                system=ATLAS_CLASSIFY.text,
                trace=ctx.trace,
                agent=self.name,
                purpose="intent classification",
                fallback={},
            )
            intent = str(data.get("intent", "")).strip().lower()
            if intent in INTENTS:
                return intent, "llm"
        except Exception as exc:
            log.debug("LLM classification unavailable: %s", exc)
        return "general", "fallback"

    @staticmethod
    def _metric(q: str) -> tuple[str | None, str | None]:
        column = next(
            (col for pattern, col in METRIC_HINTS if re.search(pattern, q, re.IGNORECASE)),
            None,
        )
        if not column:
            return None, None
        natural = repo.RANKABLE[column]["dir"]
        if ASC_RE.search(q):
            direction = "asc"
        elif DESC_RE.search(q):
            direction = "desc"
        else:
            direction = natural
        # "cheapest"/"lightest" already mean ascending; keep the natural sense
        # when the wording is a superlative that matches the column's polarity.
        if natural == "asc" and DESC_RE.search(q) and not ASC_RE.search(q):
            direction = "desc"
        return column, direction
