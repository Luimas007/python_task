"""Agent Communication Protocol (ACP/1.0) and the agent base class.

Agents never call one another's methods directly. They exchange `Envelope`
objects through the orchestrator, and every hand-off is published on the trace
bus so the console can draw the actual message flow rather than a mock-up.
"""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.core.events import RunTrace
from backend.core.logging_setup import get_logger
from backend.core.protocols import ACP

log = get_logger("agents.base")


@dataclass(frozen=True)
class AgentCard:
    """Public identity of an agent, rendered as a node in the console graph."""

    name: str
    role: str
    summary: str
    icon: str
    accent: str
    capabilities: tuple[str, ...]
    protocols: tuple[str, ...]
    reads_database: bool
    uses_llm: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Envelope:
    """One ACP/1.0 message."""

    sender: str
    recipient: str
    intent: str
    payload: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    correlation_id: str | None = None
    status: str = "ok"
    error: str | None = None

    def reply(self, intent: str, payload: dict[str, Any], **kw: Any) -> "Envelope":
        return Envelope(
            sender=self.recipient,
            recipient=self.sender,
            intent=intent,
            payload=payload,
            correlation_id=self.message_id,
            **kw,
        )

    def summary(self) -> str:
        keys = ", ".join(list(self.payload)[:5])
        return f"{self.intent}({keys})" if keys else self.intent


@dataclass
class AgentContext:
    """Everything an agent may touch during one run."""

    trace: RunTrace
    question: str
    state: dict[str, Any] = field(default_factory=dict)

    def note(self, key: str, value: Any) -> None:
        self.state[key] = value


class Agent(ABC):
    card: AgentCard

    @property
    def name(self) -> str:
        return self.card.name

    @abstractmethod
    def handle(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        """Process one inbound envelope and return the reply."""

    def activity(self, msg: Envelope, ctx: AgentContext) -> str:
        """One plain sentence describing what this agent is about to do.

        Shown live in the chat while the agent works, so the multi-agent flow
        reads as "SPECTRA is fetching specifications for the Galaxy S25 Ultra"
        rather than as an opaque spinner. Agents override this to name the
        devices and metrics they were actually handed.
        """
        return f"{self.card.role} is working"

    # ------------------------------------------------------------------
    def run(self, msg: Envelope, ctx: AgentContext) -> Envelope:
        """Wrap `handle` with tracing, timing and error containment."""
        ctx.trace.agent(
            "acp.message",
            f"{msg.sender} -> {self.name}: {msg.summary()}",
            agent=msg.sender,
            target=self.name,
            protocol=ACP.id,
            detail={
                "message_id": msg.message_id,
                "intent": msg.intent,
                "payload": _preview(msg.payload),
            },
        )
        try:
            activity = self.activity(msg, ctx)
        except Exception:                       # never let a label break a run
            activity = f"{self.card.role} is working"

        ctx.trace.agent(
            "agent.start",
            f"{self.name} ({self.card.role}) working",
            agent=self.name,
            status="pending",
            activity=activity,
            detail={"role": self.card.role, "intent": msg.intent},
        )

        t0 = time.perf_counter()
        try:
            reply = self.handle(msg, ctx)
        except Exception as exc:
            dt = round((time.perf_counter() - t0) * 1000, 2)
            log.exception("%s failed", self.name)
            ctx.trace.agent(
                "agent.error",
                f"{self.name} failed: {exc}",
                agent=self.name,
                status="error",
                duration_ms=dt,
                detail={"error": str(exc), "type": type(exc).__name__},
            )
            return msg.reply(
                f"{msg.intent}.failed", {"error": str(exc)}, status="error", error=str(exc)
            )

        dt = round((time.perf_counter() - t0) * 1000, 2)
        ctx.trace.agent(
            "agent.end",
            f"{self.name} done in {dt:.0f} ms -> {reply.summary()}",
            agent=self.name,
            target=reply.recipient,
            status=reply.status,
            duration_ms=dt,
            detail={"result": _preview(reply.payload)},
        )
        return reply


def _preview(payload: dict[str, Any], limit: int = 700) -> dict[str, Any]:
    """Shrink a payload so the trace stream stays readable over the wire."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, str):
            out[k] = v if len(v) <= limit else v[:limit] + f"... (+{len(v) - limit} chars)"
        elif isinstance(v, list):
            out[k] = f"<{len(v)} items>" if len(v) > 8 else [_shrink(i) for i in v]
        elif isinstance(v, dict):
            out[k] = f"<{len(v)} keys: {', '.join(list(v)[:6])}>"
        else:
            out[k] = v
    return out


def _shrink(item: Any) -> Any:
    if isinstance(item, dict):
        keep = ("model_name", "name", "section", "score", "phone_id", "metric_value")
        small = {k: item[k] for k in keep if k in item}
        return small or f"<dict:{len(item)} keys>"
    if isinstance(item, str) and len(item) > 160:
        return item[:160] + "..."
    return item
