"""FastAPI application: REST endpoints, the trace WebSocket, and the console."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from api.routes import router
from backend.config.settings import settings
from backend.core.events import BUS
from backend.core.logging_setup import get_logger, setup_logging
from database import engine

setup_logging()
log = get_logger("api.main")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Worker threads publish trace events; bind the loop so they can reach it.
    BUS.bind_loop(asyncio.get_running_loop())

    health = engine.healthcheck()
    if health["ok"]:
        log.info("database ready: %s", health["endpoint"])
    else:
        log.error("DATABASE UNAVAILABLE: %s", health.get("error"))

    from backend.llm.ollama_client import client
    ok, msg = client().available()
    log.info("llm: %s", msg) if ok else log.warning("llm degraded: %s", msg)

    log.info("console -> http://%s:%d/", settings.api.host, settings.api.port)
    yield
    engine.close_pool()
    log.info("shutdown complete")


app = FastAPI(
    title="Samsung Phone Query and Review System",
    description=(
        "Multi-agent Samsung phone advisory service. All knowledge is served from "
        "a local PostgreSQL database populated by scraping GSMArena; the language "
        "model performs no retrieval of its own."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/", include_in_schema=False)
async def console() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="7" fill="#0b0f17"/>'
        '<circle cx="16" cy="16" r="6" fill="none" stroke="#38bdf8" stroke-width="2.5"/>'
        '<circle cx="16" cy="16" r="2.5" fill="#38bdf8"/></svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")


def run() -> None:
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run()
