"""PostgreSQL connectivity.

Two entry points:
  * `pool()`      -- a threaded psycopg2 pool used by the request path
  * `traced()`    -- a context manager that wraps one SQL round-trip, emits a
                     PG-WIRE/3.0 frame on the trace bus and writes an audit row

Nothing in this module reaches outside the database.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool

from backend.config.settings import settings
from backend.core.events import RunTrace
from backend.core.logging_setup import get_logger
from backend.core.protocols import PG_WIRE

log = get_logger("db.engine")

_POOL: pg_pool.ThreadedConnectionPool | None = None
_POOL_LOCK = threading.Lock()

# Audit writes must never recurse into tracing.
_AUDIT_SQL = (
    "INSERT INTO query_log (run_id, agent, protocol, operation, statement, "
    "params, row_count, duration_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
)


def pool() -> pg_pool.ThreadedConnectionPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = pg_pool.ThreadedConnectionPool(
                minconn=1, maxconn=10, dsn=settings.pg.dsn
            )
            log.info("PostgreSQL pool opened -> %s", settings.pg.endpoint)
        return _POOL


def close_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.closeall()
            _POOL = None


@contextmanager
def connection() -> Iterator[psycopg2.extensions.connection]:
    p = pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


@contextmanager
def cursor(dict_rows: bool = True) -> Iterator[psycopg2.extensions.cursor]:
    with connection() as conn:
        factory = psycopg2.extras.RealDictCursor if dict_rows else None
        cur = conn.cursor(cursor_factory=factory)
        try:
            yield cur
        finally:
            cur.close()


# --------------------------------------------------------------------------
#  Traced execution
# --------------------------------------------------------------------------
def _compact(sql: str) -> str:
    return " ".join(sql.split())


def _audit(
    trace: RunTrace | None,
    agent: str | None,
    operation: str,
    sql: str,
    params: Any,
    row_count: int,
    duration_ms: float,
) -> None:
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _AUDIT_SQL,
                    (
                        trace.run_id if trace else None,
                        agent,
                        PG_WIRE.id,
                        operation,
                        _compact(sql)[:4000],
                        json.dumps(_jsonable(params)),
                        row_count,
                        duration_ms,
                    ),
                )
    except Exception as exc:  # auditing must never break a query
        log.debug("audit write skipped: %s", exc)


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            if isinstance(v, (list, tuple)) and len(v) > 24:
                out.append(f"<{len(v)} floats>")
            else:
                out.append(_jsonable(v))
        return out
    return str(value)


def fetch_all(
    sql: str,
    params: Sequence[Any] | dict[str, Any] | None = None,
    *,
    trace: RunTrace | None = None,
    agent: str | None = None,
    operation: str = "SELECT",
    audit: bool = True,
) -> list[dict[str, Any]]:
    """Run a read query, emitting a PG-WIRE frame describing the round-trip."""
    t0 = time.perf_counter()
    with cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    dt = round((time.perf_counter() - t0) * 1000, 2)

    if trace is not None:
        trace.protocol(
            "db.query",
            f"{operation} -> {len(rows)} row(s) in {dt} ms",
            agent=agent,
            protocol=PG_WIRE.id,
            duration_ms=dt,
            detail={
                "endpoint": settings.pg.endpoint,
                "operation": operation,
                "sql": _compact(sql),
                "params": _jsonable(params),
                "row_count": len(rows),
            },
        )
    if audit:
        _audit(trace, agent, operation, sql, params, len(rows), dt)
    return rows


def fetch_one(
    sql: str,
    params: Sequence[Any] | dict[str, Any] | None = None,
    **kw: Any,
) -> dict[str, Any] | None:
    rows = fetch_all(sql, params, **kw)
    return rows[0] if rows else None


def execute(
    sql: str,
    params: Sequence[Any] | dict[str, Any] | None = None,
    *,
    trace: RunTrace | None = None,
    agent: str | None = None,
    operation: str = "WRITE",
    returning: bool = False,
) -> Any:
    t0 = time.perf_counter()
    result = None
    with cursor() as cur:
        cur.execute(sql, params)
        rowcount = cur.rowcount
        if returning:
            row = cur.fetchone()
            result = dict(row) if row else None
    dt = round((time.perf_counter() - t0) * 1000, 2)
    if trace is not None:
        trace.protocol(
            "db.write",
            f"{operation} -> {rowcount} row(s) in {dt} ms",
            agent=agent,
            protocol=PG_WIRE.id,
            duration_ms=dt,
            detail={
                "endpoint": settings.pg.endpoint,
                "operation": operation,
                "sql": _compact(sql),
                "params": _jsonable(params),
                "row_count": rowcount,
            },
        )
    return result if returning else rowcount


def execute_many(sql: str, rows: Sequence[Sequence[Any]], page_size: int = 200) -> int:
    if not rows:
        return 0
    with cursor(dict_rows=False) as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=page_size)
        return len(rows)


# --------------------------------------------------------------------------
#  Bootstrap
# --------------------------------------------------------------------------
def database_exists() -> bool:
    conn = psycopg2.connect(settings.pg.maintenance_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (settings.pg.database,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def create_database() -> bool:
    """Create the target database if absent. Returns True when created."""
    if database_exists():
        return False
    conn = psycopg2.connect(settings.pg.maintenance_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{settings.pg.database}"')
        log.info("created database %s", settings.pg.database)
        return True
    finally:
        conn.close()


def apply_schema(path: Path | None = None) -> None:
    sql_path = path or (Path(__file__).parent / "schema.sql")
    ddl = sql_path.read_text(encoding="utf-8")
    conn = psycopg2.connect(settings.pg.dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
        log.info("schema applied from %s", sql_path.name)
    finally:
        conn.close()


def healthcheck() -> dict[str, Any]:
    try:
        with cursor() as cur:
            cur.execute("SELECT version() AS v, current_database() AS db, now() AS ts")
            row = dict(cur.fetchone())
        return {
            "ok": True,
            "endpoint": settings.pg.endpoint,
            "database": row["db"],
            "server": row["v"].split(",")[0],
            "protocol": PG_WIRE.id,
        }
    except Exception as exc:
        return {"ok": False, "endpoint": settings.pg.endpoint, "error": str(exc)}
