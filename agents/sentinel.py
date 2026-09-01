"""SENTINEL -- Grounding Auditor.

Last agent in every run. Extracts each number the answer asserts and checks it
appears somewhere in the evidence the other agents pulled from PostgreSQL. This
is the mechanical enforcement of "the database is the only knowledge base": a
figure the model produced from its own weights has no matching source row and is
reported as unsupported.

SENTINEL reports; it does not rewrite. The verdict is shown in the console so
the grounding of any answer can be inspected.
"""
from __future__ import annotations

import re
from typing import Any

from agents.base import Agent, AgentCard, AgentContext, Envelope
from backend.core.logging_setup import get_logger

log = get_logger("agents.sentinel")

# Numbers embedded in prose: 5000, 6.8, 1,241,531, and unit-glued forms such as
# 45W or 5000mAh. The trailing guard rejects only a continuation of the number
# itself -- rejecting any word character would skip every figure with a unit
# attached, which is most of them. The leading guard keeps us out of
# identifiers like SM-S918B and version tags like v9.
NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?!\.?\d)")

# Years, list markers and small ordinals are narrative, not factual claims.
IGNORE_VALUES = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}


class SentinelAgent(Agent):
    card = AgentCard(
        name="SENTINEL",
        role="Grounding Auditor",
        summary="Cross-checks every figure in the drafted answer against the "
                "evidence retrieved from PostgreSQL and flags anything unsupported.",
        icon="shield",
        accent="#22c55e",
        capabilities=(
            "numeric claim extraction",
            "evidence cross-checking",
            "hallucination flagging",
        ),
        protocols=("ACP/1.0",),
        reads_database=False,
        uses_llm=False,
    )


    def activity(self, msg: Envelope, ctx: AgentContext) -> str:
        return "Checking every figure in the answer against the database"

    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        answer: str = msg.payload.get("answer") or ""
        evidence: str = msg.payload.get("evidence") or ""

        claims = self._claims(answer)
        haystack = self._normalise(evidence)

        supported, unsupported = [], []
        for c in claims:
            (supported if self._is_present(c, haystack) else unsupported).append(c)

        total = len(claims)
        ratio = round(len(supported) / total, 3) if total else 1.0
        verdict = (
            "grounded" if not unsupported
            else "partially-grounded" if ratio >= 0.8
            else "weakly-grounded"
        )

        result = {
            "verdict": verdict,
            "numeric_claims": total,
            "supported": len(supported),
            "unsupported": unsupported,
            "support_ratio": ratio,
            "evidence_chars": len(evidence),
        }

        ctx.trace.agent(
            "agent.finding",
            f"grounding {verdict}: {len(supported)}/{total} numeric claim(s) "
            f"traced to database evidence"
            + (f"; unverified: {', '.join(unsupported[:6])}" if unsupported else ""),
            agent=self.name,
            status="ok" if verdict == "grounded" else "error",
            detail=result,
        )
        return msg.reply("audit.result", result)

    # ------------------------------------------------------------------
    @staticmethod
    def _claims(text: str) -> list[str]:
        seen, out = set(), []
        for m in NUMBER_RE.finditer(text):
            raw = m.group(1)
            norm = raw.replace(",", "")
            if norm in IGNORE_VALUES or norm in seen:
                continue
            # Four-digit values in the 19xx/20xx range read as years.
            if re.fullmatch(r"(19|20)\d{2}", norm):
                continue
            seen.add(norm)
            out.append(raw)
        return out

    @staticmethod
    def _normalise(text: str) -> str:
        return re.sub(r"[,\s]", "", text.lower())

    @staticmethod
    def _is_present(claim: str, haystack: str) -> bool:
        norm = claim.replace(",", "")
        if norm in haystack:
            return True
        # "6.8" should still match a stored "6.80"; "12" should match "12.0".
        try:
            f = float(norm)
        except ValueError:
            return False
        for variant in (f"{f:g}", f"{f:.1f}", f"{f:.2f}", str(int(f)) if f.is_integer() else ""):
            if variant and variant in haystack:
                return True
        return False


def gather_evidence(ctx: AgentContext, extra: list[str] | None = None) -> str:
    """Concatenate everything the retrieval agents produced this run."""
    parts: list[str] = list(extra or [])
    # Figures an agent computed from database values (deltas, percentages,
    # percentile standings) are as grounded as the rows they came from.
    if derived := ctx.state.get("derived_evidence"):
        parts.append(str(derived))
    for key in ("spec_sheets", "rag_hits", "ranking"):
        value = ctx.state.get(key)
        if not value:
            continue
        if key == "spec_sheets":
            for sheet in value:
                parts.append(_flatten(sheet["phone"]))
                for rows in sheet["specs_by_category"].values():
                    parts.extend(str(r["value"]) for r in rows if r["value"])
        elif key == "rag_hits":
            parts.extend(h.content for h in value)
        elif key == "ranking":
            parts.extend(
                f"{r['model_name']} {r['metric_value']}" for r in value["rows"]
            )
    return "\n".join(parts)


def _flatten(record: dict[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in record.items() if v is not None)
