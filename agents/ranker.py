"""RANKER -- Comparative Analytics.

Superlatives ("which has the best battery life?") are an aggregation problem,
not a similarity problem: embeddings rank all battery chunks as roughly equally
relevant. RANKER therefore answers them with an ORDER BY over a whitelisted
column, and reports how many devices were excluded because the value is NULL.
"""
from __future__ import annotations

from agents.base import Agent, AgentCard, AgentContext, Envelope
from backend.core.logging_setup import get_logger
from database import repository as repo

log = get_logger("agents.ranker")

DEFAULT_METRIC = "antutu_score"


class RankerAgent(Agent):
    card = AgentCard(
        name="RANKER",
        role="Comparative Analytics",
        summary="Answers superlative and league-table questions with SQL "
                "aggregation over whitelisted numeric attributes.",
        icon="chart",
        accent="#f59e0b",
        capabilities=(
            "ORDER BY over whitelisted metrics",
            "NULL exclusion accounting",
            "series and flagship filtering",
        ),
        protocols=("ACP/1.0", "PG-WIRE/3.0"),
        reads_database=True,
        uses_llm=False,
    )

    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        column = msg.payload.get("metric") or DEFAULT_METRIC
        if column not in repo.RANKABLE:
            column = DEFAULT_METRIC
        direction = msg.payload.get("direction")
        limit = int(msg.payload.get("limit", 8))
        flagship_only = bool(msg.payload.get("flagship_only", False))
        series = msg.payload.get("series")

        result = repo.rank_by(
            column,
            direction=direction,
            limit=limit,
            flagship_only=flagship_only,
            series=series,
            trace=ctx.trace,
            agent=self.name,
        )
        rendered = self.render(result)
        ctx.note("ranking", result)

        top = result["rows"][0] if result["rows"] else None
        ctx.trace.agent(
            "agent.finding",
            f"ranked {len(result['rows'])} device(s) by {result['label']} "
            f"({result['direction']})"
            + (f"; leader {top['model_name']} at {top['metric_value']} {result['unit']}"
               if top else "; no device has this value")
            + f"; {result['excluded_null_count']} excluded (NULL)",
            agent=self.name,
            detail=result,
        )
        return msg.reply("ranking.result", {"ranking": result, "rendered": rendered})

    @staticmethod
    def render(result: dict) -> str:
        lines = [
            f"Ranking by {result['label']} ({result['direction']}), "
            f"unit = {result['unit']}.",
            f"{result['excluded_null_count']} device(s) omitted because the value "
            f"is NULL in the database (the source never published it).",
        ]
        for i, r in enumerate(result["rows"], 1):
            lines.append(
                f"{i}. {r['model_name']} - {r['metric_value']} {result['unit']}"
            )
        if not result["rows"]:
            lines.append("No device in the database has a value for this metric.")
        return "\n".join(lines)
