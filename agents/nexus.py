"""NEXUS -- Orchestrator.

Owns the run. Dispatches ACP envelopes to the specialist agents in an order
chosen by ATLAS's intent, synthesises the final answer for the intents that have
no dedicated writer, and always closes with a SENTINEL audit.

Every hand-off passes through `Agent.run`, so the console's flow diagram is
drawn from the messages that actually travelled, not from a fixed script.
"""
from __future__ import annotations

import time
from typing import Any

from agents.atlas import AtlasAgent
from agents.prompts import NEXUS_SYNTHESIS
from agents.base import Agent, AgentCard, AgentContext, Envelope
from agents.critic import CriticAgent
from agents.oracle import OracleAgent
from agents.ranker import RankerAgent
from agents.sentinel import SentinelAgent, gather_evidence
from agents.spectra import SpectraAgent
from agents.versus import VersusAgent
from backend.core.events import RunTrace
from backend.core.logging_setup import get_logger
from backend.core.protocols import ALL_PROTOCOLS
from database import engine
from backend.llm.ollama_client import LLMUnavailable, client

log = get_logger("agents.nexus")


# Which category subset SPECTRA should pull, by topic in the question.
FOCUS_KEYWORDS: list[tuple[str, str]] = [
    ("camera", "camera"), ("photo", "camera"), ("selfie", "camera"), ("video", "camera"),
    ("battery", "battery"), ("charg", "battery"), ("endurance", "battery"),
    ("screen", "display"), ("display", "display"), ("refresh", "display"),
    ("brightness", "display"), ("resolution", "display"),
    ("performance", "performance"), ("chipset", "performance"),
    ("processor", "performance"), ("cpu", "performance"), ("gpu", "performance"),
    ("benchmark", "performance"), ("ram", "performance"), ("storage", "performance"),
    ("weight", "design"), ("build", "design"), ("dimension", "design"),
    ("waterproof", "design"), ("ip6", "design"),
    ("price", "pricing"), ("cost", "pricing"),
    ("5g", "connectivity"), ("nfc", "connectivity"), ("wifi", "connectivity"),
    ("bluetooth", "connectivity"), ("jack", "connectivity"), ("speaker", "connectivity"),
]


class NexusAgent(Agent):
    card = AgentCard(
        name="NEXUS",
        role="Orchestrator",
        summary="Plans the run, routes ACP messages between specialists, and "
                "assembles the final grounded answer.",
        icon="hub",
        accent="#e11d48",
        capabilities=(
            "run planning",
            "agent dispatch and routing",
            "answer synthesis",
        ),
        protocols=("ACP/1.0", "OLLAMA-HTTP/1.1", "WS-TRACE/1.0"),
        reads_database=False,
        uses_llm=True,
    )

    def __init__(self) -> None:
        self.atlas = AtlasAgent()
        self.spectra = SpectraAgent()
        self.oracle = OracleAgent()
        self.ranker = RankerAgent()
        self.versus = VersusAgent()
        self.critic = CriticAgent()
        self.sentinel = SentinelAgent()

    def specialists(self) -> tuple[Agent, ...]:
        return (self.atlas, self.spectra, self.oracle, self.ranker,
                self.versus, self.critic, self.sentinel)

    def roster(self) -> list[dict[str, Any]]:
        """Agent cards for the console, orchestrator first."""
        return [self.card.to_dict()] + [a.card.to_dict() for a in self.specialists()]

    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        """NEXUS is the entry point, not a dispatch target -- use `answer`."""
        raise NotImplementedError("call NexusAgent.answer(question, trace)")

    # ------------------------------------------------------------------
    def answer(self, question: str, trace: RunTrace) -> dict[str, Any]:
        ctx = AgentContext(trace=trace, question=question)
        t0 = time.perf_counter()

        trace.system(
            "run.start",
            f"NEXUS accepted query: {question[:160]}",
            agent=self.name,
            detail={"question": question, "protocols": [p.id for p in ALL_PROTOCOLS]},
        )

        # ---- 1. analysis -------------------------------------------------
        analysis = self.atlas.run(
            Envelope(self.name, "ATLAS", "analyse.query", {"question": question}), ctx
        )
        intent = analysis.payload.get("intent", "general")
        phones = analysis.payload.get("phones", [])
        phone_ids = [p["phone_id"] for p in phones]
        metric = analysis.payload.get("metric")
        direction = analysis.payload.get("direction")
        unresolved = analysis.payload.get("unresolved_mentions", [])

        agents_used = ["NEXUS", "ATLAS"]
        answer, extras = "", {}

        # Every branch below needs devices that actually exist in the database.
        # When ATLAS resolved none, the run falls through to the general path --
        # so report the intent that *ran*, not the one that was hoped for.
        # Otherwise the response claims `spec_lookup` while listing the general
        # pipeline's agents.
        effective_intent = intent
        if (intent == "compare" and len(phone_ids) < 2) or (
            intent in ("review", "spec_lookup") and not phone_ids
        ):
            effective_intent = "general"
            trace.system(
                "run.downgrade",
                f"no device from the database matched, so the {intent} pipeline "
                "cannot run; answering from semantic search instead",
                agent=self.name,
                detail={"requested_intent": intent, "effective_intent": "general"},
            )

        plan = self._plan(effective_intent)
        trace.system(
            "run.plan",
            f"intent={effective_intent} -> pipeline: {' -> '.join(plan)}",
            agent=self.name,
            detail={"intent": effective_intent, "pipeline": plan,
                    "devices": [p["model_name"] for p in phones]},
        )

        # ---- 2. dispatch --------------------------------------------------
        if effective_intent == "compare" and len(phone_ids) >= 2:
            specs = self.spectra.run(
                Envelope(self.name, "SPECTRA", "fetch.specs",
                         {"phone_ids": phone_ids, "focus": self._focus(question)}), ctx
            )
            agents_used.append("SPECTRA")
            result = self.versus.run(
                Envelope("SPECTRA", "VERSUS", "compare.devices", {
                    "phone_ids": phone_ids,
                    "question": question,
                    "rendered": specs.payload.get("rendered", []),
                }), ctx
            )
            agents_used.append("VERSUS")
            answer = result.payload.get("answer", "")
            extras = {"deltas": result.payload.get("deltas", []),
                      "table": result.payload.get("table")}

        elif effective_intent == "review" and phone_ids:
            specs = self.spectra.run(
                Envelope(self.name, "SPECTRA", "fetch.specs",
                         {"phone_ids": phone_ids[:1]}), ctx
            )
            agents_used.append("SPECTRA")
            result = self.critic.run(
                Envelope("SPECTRA", "CRITIC", "write.review", {
                    "sheets": specs.payload.get("sheets", []),
                    "rendered": specs.payload.get("rendered", []),
                    "question": question,
                }), ctx
            )
            agents_used.append("CRITIC")
            answer = result.payload.get("answer", "")
            extras = {"standings": result.payload.get("standings", [])}

        elif effective_intent == "ranking":
            result = self.ranker.run(
                Envelope(self.name, "RANKER", "rank.devices", {
                    "metric": metric, "direction": direction, "limit": 8,
                }), ctx
            )
            agents_used.append("RANKER")
            ranking_text = result.payload.get("rendered", "")
            answer = self._synthesise(question, ranking_text, ctx)
            extras = {"ranking": result.payload.get("ranking")}

        elif effective_intent == "spec_lookup" and phone_ids:
            focus = self._focus(question)
            specs = self.spectra.run(
                Envelope(self.name, "SPECTRA", "fetch.specs",
                         {"phone_ids": phone_ids, "focus": focus}), ctx
            )
            agents_used.append("SPECTRA")
            rag = self.oracle.run(
                Envelope("SPECTRA", "ORACLE", "search.corpus",
                         {"query": question, "phone_ids": phone_ids, "top_k": 5}), ctx
            )
            agents_used.append("ORACLE")
            context = "\n\n".join(specs.payload.get("rendered", []))
            if rag.payload.get("context"):
                context += "\n\n=== RELATED PASSAGES ===\n" + rag.payload["context"]
            answer = self._synthesise(question, context, ctx)
            extras = {"citations": rag.payload.get("citations", []),
                      "focus": focus}

        else:  # general
            rag = self.oracle.run(
                Envelope(self.name, "ORACLE", "search.corpus",
                         {"query": question, "top_k": 8}), ctx
            )
            agents_used.append("ORACLE")
            context = rag.payload.get("context", "")
            if not context:
                answer = (
                    "The database does not hold anything matching that question. "
                    "It covers Samsung phone specifications only, for the "
                    f"{self._corpus_size()} devices currently loaded."
                )
            else:
                answer = self._synthesise(question, context, ctx)
            extras = {"citations": rag.payload.get("citations", [])}

        if unresolved:
            answer += (
                f"\n\nNote: {', '.join(unresolved)} is not in this database, so "
                "nothing above refers to it."
            )

        # ---- 3. audit -----------------------------------------------------
        audit = self.sentinel.run(
            Envelope(self.name, "SENTINEL", "audit.answer", {
                "answer": answer,
                "evidence": gather_evidence(ctx, [str(extras.get("table") or "")]),
            }), ctx
        )
        agents_used.append("SENTINEL")

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        trace.system(
            "run.end",
            f"answered in {elapsed:.0f} ms via {len(agents_used)} agents "
            f"({audit.payload.get('verdict')})",
            agent=self.name,
            duration_ms=elapsed,
            detail={"agents": agents_used, "intent": intent,
                    "grounding": audit.payload},
        )

        return {
            "answer": answer,
            "intent": effective_intent,
            "requested_intent": intent,
            "agents_used": agents_used,
            "pipeline": plan,
            "devices": [p["model_name"] for p in phones],
            "unresolved": unresolved,
            "grounding": audit.payload,
            "extras": extras,
            "latency_ms": elapsed,
            "run_id": trace.run_id,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _plan(intent: str) -> list[str]:
        return {
            "compare": ["ATLAS", "SPECTRA", "VERSUS", "SENTINEL"],
            "review": ["ATLAS", "SPECTRA", "CRITIC", "SENTINEL"],
            "ranking": ["ATLAS", "RANKER", "NEXUS", "SENTINEL"],
            "spec_lookup": ["ATLAS", "SPECTRA", "ORACLE", "NEXUS", "SENTINEL"],
        }.get(intent, ["ATLAS", "ORACLE", "NEXUS", "SENTINEL"])

    @staticmethod
    def _focus(question: str) -> str | None:
        low = question.lower()
        for keyword, focus in FOCUS_KEYWORDS:
            if keyword in low:
                return focus
        return None

    def _synthesise(self, question: str, context: str, ctx: AgentContext) -> str:
        if not context.strip():
            return "The database returned no rows for that question."

        # NEXUS composes in-line rather than through Agent.run, so it announces
        # its own step to keep the activity feed complete.
        ctx.trace.agent(
            "agent.start",
            "NEXUS (Orchestrator) composing the answer",
            agent=self.name,
            status="pending",
            activity="Preparing the final response",
            detail={"context_chars": len(context)},
        )
        prompt = (
            f"=== CONTEXT (retrieved from PostgreSQL) ===\n{context[:7000]}\n\n"
            f"=== QUESTION ===\n{question}\n\n"
            "Answer using only the context above."
        )
        try:
            comp = client().generate(
                prompt, system=NEXUS_SYNTHESIS.text, trace=ctx.trace, agent=self.name,
                purpose="answer synthesis", max_tokens=700,
            )
            ctx.trace.agent("agent.end", "NEXUS composed the answer",
                            agent=self.name, detail={"chars": len(comp.text)})
            return comp.text
        except LLMUnavailable as exc:
            ctx.trace.system(
                "llm.unavailable",
                f"local model unreachable; returning raw database rows ({exc})",
                agent=self.name, status="error",
            )
            ctx.trace.agent("agent.end", "NEXUS returned raw rows (no LLM)",
                            agent=self.name, status="error")
            return (
                "The local language model is unreachable, so here are the raw "
                f"database rows for your question:\n\n{context[:2500]}"
            )

    @staticmethod
    def _corpus_size() -> int:
        row = engine.fetch_one("SELECT count(*) AS n FROM phones", audit=False)
        return int(row["n"]) if row else 0


_ORCHESTRATOR: NexusAgent | None = None


def orchestrator() -> NexusAgent:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = NexusAgent()
    return _ORCHESTRATOR
