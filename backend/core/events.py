"""Thread-safe trace bus.

Agents and the data layer run in worker threads; the API serves WebSockets on an
asyncio loop. `TraceBus` bridges the two: `emit()` is callable from any thread and
fans events out to every subscribed queue plus an in-memory ring buffer so a late
subscriber can replay recent history.
"""
from __future__ import annotations

import asyncio
import itertools
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

MAX_HISTORY = 600


def jsonsafe(value: Any) -> Any:
    """Coerce a value into something `json.dumps` accepts.

    Event details are built from database rows, which carry Decimal, date and
    datetime objects. An un-encodable payload would raise inside the WebSocket
    send and drop the subscriber, so everything is normalised on the way in.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonsafe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonsafe(v) for v in value]
    return str(value)


@dataclass
class TraceEvent:
    seq: int
    ts: float
    run_id: str
    channel: str            # agent | protocol | system
    type: str               # run.start, agent.start, acp.message, db.query, llm.call ...
    summary: str
    agent: str | None = None
    target: str | None = None
    protocol: str | None = None
    status: str = "ok"      # ok | error | pending
    duration_ms: float | None = None
    # Plain-language description of what an agent is doing, shown live in chat.
    activity: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.detail = jsonsafe(self.detail)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TraceBus:
    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        self._history: deque[TraceEvent] = deque(maxlen=MAX_HISTORY)
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- wiring ---------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once by the API on startup so worker threads can reach the loop."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def history(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items: Iterable[TraceEvent] = list(self._history)
        return [e.to_dict() for e in items if run_id is None or e.run_id == run_id]

    # -- emission -------------------------------------------------------
    def emit(
        self,
        run_id: str,
        channel: str,
        type: str,
        summary: str,
        **kwargs: Any,
    ) -> TraceEvent:
        event = TraceEvent(
            seq=next(self._counter),
            ts=time.time(),
            run_id=run_id,
            channel=channel,
            type=type,
            summary=summary,
            **kwargs,
        )
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)
        if subs and self._loop is not None:
            payload = event.to_dict()
            for q in subs:
                try:
                    self._loop.call_soon_threadsafe(self._offer, q, payload)
                except RuntimeError:
                    pass
        return event

    @staticmethod
    def _offer(q: asyncio.Queue, payload: dict[str, Any]) -> None:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


BUS = TraceBus()


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class RunTrace:
    """Convenience façade bound to a single request/run."""

    def __init__(self, run_id: str | None = None, bus: TraceBus = BUS) -> None:
        self.run_id = run_id or new_run_id()
        self.bus = bus
        self._t0 = time.perf_counter()

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 2)

    def system(self, type: str, summary: str, **kw: Any) -> TraceEvent:
        return self.bus.emit(self.run_id, "system", type, summary, **kw)

    def agent(self, type: str, summary: str, **kw: Any) -> TraceEvent:
        return self.bus.emit(self.run_id, "agent", type, summary, **kw)

    def protocol(self, type: str, summary: str, **kw: Any) -> TraceEvent:
        return self.bus.emit(self.run_id, "protocol", type, summary, **kw)

    def events(self) -> list[dict[str, Any]]:
        return self.bus.history(self.run_id)


NULL_TRACE_RUN = "offline"


class NullTrace(RunTrace):
    """Used by CLI scripts where nothing is listening."""

    def __init__(self) -> None:
        super().__init__(run_id=NULL_TRACE_RUN)
