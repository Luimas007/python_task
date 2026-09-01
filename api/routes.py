"""HTTP and WebSocket endpoints."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from agents.nexus import orchestrator
from api.schemas import QueryRequest, RefreshRequest
from backend.config.settings import settings
from backend.core.events import BUS, RunTrace
from backend.core.logging_setup import get_logger
from backend.core.protocols import ALL_PROTOCOLS
from database import engine
from database import repository as repo
from backend.llm.ollama_client import client

log = get_logger("api.routes")
router = APIRouter()


# ---------------------------------------------------------------- meta ----
@router.get("/api/health")
async def health() -> dict[str, Any]:
    db = await run_in_threadpool(engine.healthcheck)
    ok, msg = await run_in_threadpool(client().available)
    corpus = await run_in_threadpool(repo.corpus_stats)
    return {
        "status": "ok" if db["ok"] and ok else "degraded",
        "database": db,
        "llm": {
            "ok": ok,
            "detail": msg,
            "model": settings.llm.model,
            "endpoint": settings.llm.host,
            "protocol": "OLLAMA-HTTP/1.1",
        },
        "embedding": {
            "model": settings.embedding.model_name,
            "dim": settings.embedding.dim,
            "device": settings.embedding.device,
        },
        "corpus": {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in corpus.items()},
    }


@router.get("/api/agents")
async def agents() -> dict[str, Any]:
    """The agent roster the console renders as nodes."""
    return {"agents": orchestrator().roster()}


@router.get("/api/protocols")
async def protocols() -> dict[str, Any]:
    return {
        "protocols": [
            {
                "id": p.id,
                "label": p.label,
                "transport": p.transport,
                "description": p.description,
            }
            for p in ALL_PROTOCOLS
        ]
    }


# -------------------------------------------------------------- catalog ---
@router.get("/api/phones")
async def phones() -> dict[str, Any]:
    rows = await run_in_threadpool(repo.list_phones)
    series = await run_in_threadpool(repo.series_breakdown)
    return {"count": len(rows), "phones": _jsonsafe(rows), "series": _jsonsafe(series)}


@router.get("/api/phones/{phone_id}")
async def phone_detail(phone_id: int) -> dict[str, Any]:
    sheet = await run_in_threadpool(repo.spec_sheet, phone_id)
    if not sheet:
        raise HTTPException(404, f"no phone with id {phone_id}")
    return _jsonsafe(sheet)


@router.get("/api/rankings")
async def rankings(
    metric: str = Query("battery_endurance_hours"),
    limit: int = Query(10, ge=1, le=30),
) -> dict[str, Any]:
    if metric not in repo.RANKABLE:
        raise HTTPException(
            400, f"metric must be one of: {', '.join(sorted(repo.RANKABLE))}"
        )
    result = await run_in_threadpool(repo.rank_by, metric, limit=limit)
    return _jsonsafe(result)


@router.get("/api/rankable")
async def rankable() -> dict[str, Any]:
    return {"metrics": repo.RANKABLE}


@router.get("/api/query-log")
async def query_log(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    rows = await run_in_threadpool(repo.recent_queries, limit)
    return {"entries": _jsonsafe(rows)}


# ----------------------------------------------------------------- ask ----
@router.post("/api/ask")
async def ask(body: QueryRequest) -> dict[str, Any]:
    question = body.question.strip()
    if len(question) < 2:
        raise HTTPException(422, "question must be at least 2 characters")
    session_key = body.session_key

    trace = RunTrace()
    result = await run_in_threadpool(orchestrator().answer, question, trace)
    await run_in_threadpool(_persist, session_key, question, result)

    return {
        "run_id": trace.run_id,
        "question": question,
        **_jsonsafe(result),
        "trace": trace.events(),
    }


def _persist(session_key: str | None, question: str, result: dict[str, Any]) -> None:
    """Store the exchange. Failures here must not fail the request."""
    try:
        key = session_key or "anonymous"
        row = engine.execute(
            """INSERT INTO conversations (session_key) VALUES (%s)
               ON CONFLICT (session_key) DO UPDATE SET session_key = EXCLUDED.session_key
               RETURNING conversation_id""",
            (key,),
            returning=True,
        )
        cid = row["conversation_id"]
        engine.execute(
            """INSERT INTO messages (conversation_id, run_id, role, content)
               VALUES (%s,%s,'user',%s)""",
            (cid, result.get("run_id"), question),
        )
        engine.execute(
            """INSERT INTO messages (conversation_id, run_id, role, content, intent,
                                     agents_used, grounding, latency_ms)
               VALUES (%s,%s,'assistant',%s,%s,%s,%s,%s)""",
            (
                cid,
                result.get("run_id"),
                result.get("answer", ""),
                result.get("intent"),
                result.get("agents_used", []),
                json.dumps(result.get("grounding") or {}),
                result.get("latency_ms"),
            ),
        )
    except Exception as exc:
        log.warning("conversation not persisted: %s", exc)


@router.get("/api/history")
async def history(session_key: str = Query("anonymous"), limit: int = 40) -> dict[str, Any]:
    rows = await run_in_threadpool(
        engine.fetch_all,
        """SELECT m.role, m.content, m.intent, m.agents_used, m.latency_ms, m.created_at
             FROM messages m JOIN conversations c USING (conversation_id)
            WHERE c.session_key = %s ORDER BY m.message_id DESC LIMIT %s""",
        (session_key, limit),
    )
    return {"messages": _jsonsafe(list(reversed(rows)))}


# ------------------------------------------------- knowledge base refresh --
# One refresh at a time. A second request while one runs returns 409 rather
# than letting two writers fight over the same tables.
_refresh_lock = threading.Lock()
_refresh_state: dict[str, Any] = {"running": False, "events": [], "started_at": None}


@router.get("/api/knowledge")
async def knowledge() -> dict[str, Any]:
    """What the knowledge base currently holds."""
    corpus = await run_in_threadpool(repo.corpus_stats)
    phones = await run_in_threadpool(repo.list_phones)
    return {
        "ready": bool(corpus.get("phones")),
        "running": _refresh_state["running"],
        "stats": _jsonsafe(corpus),
        "phones": [
            {"phone_id": p["phone_id"], "model_name": p["model_name"],
             "series": p["series"], "rank": p["popularity_rank"]}
            for p in phones
        ],
        "default_limit": settings.scraper.demo_count,
    }


@router.post("/api/knowledge/refresh")
async def knowledge_refresh(body: RefreshRequest | None = None) -> dict[str, Any]:
    """Start a refresh. Progress streams over /ws/trace as `kb.*` events."""
    body = body or RefreshRequest()
    if not _refresh_lock.acquire(blocking=False):
        raise HTTPException(409, "a knowledge base refresh is already running")

    limit = body.limit or settings.scraper.demo_count
    _refresh_state.update(running=True, events=[], started_at=time.time())

    def worker() -> None:
        from scraper import pipeline

        trace = RunTrace()
        try:
            for event in pipeline.refresh(limit=limit, offline=body.offline,
                                             replace=body.replace):
                _refresh_state["events"].append(event)
                trace.system(
                    f"kb.{event['phase']}",
                    event.get("message", event["phase"]),
                    status="error" if event["phase"] == "error" else "ok",
                    detail=event,
                )
        except Exception as exc:
            log.exception("refresh failed")
            trace.system("kb.error", f"refresh failed: {exc}", status="error",
                         detail={"phase": "error", "message": str(exc)})
        finally:
            _refresh_state["running"] = False
            _refresh_lock.release()

    threading.Thread(target=worker, daemon=True, name="kb-refresh").start()
    return {"started": True, "limit": limit, "stream": "/ws/trace"}


@router.get("/api/knowledge/refresh/status")
async def knowledge_refresh_status() -> dict[str, Any]:
    """Poll fallback for clients that cannot hold a WebSocket open."""
    return {
        "running": _refresh_state["running"],
        "events": _jsonsafe(_refresh_state["events"]),
    }


# ----------------------------------------------------------- trace feed ---
@router.websocket("/ws/trace")
async def ws_trace(ws: WebSocket) -> None:
    await ws.accept()
    queue = BUS.subscribe()
    log.info("trace subscriber connected (%s)", ws.client)
    try:
        await ws.send_json({
            "type": "trace.hello",
            "channel": "system",
            "summary": "connected to live trace stream",
            "history": BUS.history()[-60:],
        })
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "trace.ping", "channel": "system"})
                continue
            try:
                await ws.send_json(event)
            except (TypeError, ValueError) as exc:
                # A single unencodable frame must not cost the subscriber its
                # connection; drop the frame and keep streaming.
                log.warning("skipped untransmittable trace frame: %s", exc)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("trace socket closed: %s", exc)
    finally:
        BUS.unsubscribe(queue)
        log.info("trace subscriber disconnected")


# --------------------------------------------------------------- helpers --
def _jsonsafe(value: Any) -> Any:
    """Decimal / date / datetime -> JSON-friendly primitives."""
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, dict):
        return {k: _jsonsafe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonsafe(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
