"""Open the PostgreSQL tables in a browser.

    python -m scripts.db_browser

Starts a small read-only viewer on http://127.0.0.1:8010 and opens it. Every
table in the knowledge base is listed in the sidebar with its row count; click
one to page through its rows. NULLs are rendered as NULL rather than as blanks
-- the point being that absent facts are stored, not invented.

Read-only by construction: the only statement it ever issues is SELECT.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from backend.config.settings import settings
from database import engine

PAGE_SIZE = 50
MAX_CELL = 180


# ---------------------------------------------------------------- queries ---
def tables() -> list[dict]:
    """Every public table, with its live row count."""
    names = engine.fetch_all(
        """SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name""",
        audit=False,
    )
    out = []
    for t in names:
        name = t["table_name"]
        n = engine.fetch_one(f'SELECT count(*) AS n FROM "{name}"', audit=False)
        out.append({"name": name, "rows": int(n["n"])})
    return out


def columns(table: str) -> list[dict]:
    return engine.fetch_all(
        """SELECT column_name, data_type, is_nullable
             FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position""",
        (table,),
        audit=False,
    )


def rows(table: str, offset: int) -> list[dict]:
    return engine.fetch_all(
        f'SELECT * FROM "{table}" ORDER BY 1 LIMIT %s OFFSET %s',
        (PAGE_SIZE, offset),
        audit=False,
    )


# ------------------------------------------------------------------ render ---
def cell(value) -> str:
    if value is None:
        return '<span class="null">NULL</span>'
    if isinstance(value, bool):
        return "true" if value else "false"
    # An embedding is 384 floats -- summarise it. Short JSONB arrays and objects
    # are worth reading, so render those as JSON.
    if isinstance(value, (list, tuple)) and len(value) > 16 and all(
        isinstance(v, (int, float)) for v in value
    ):
        return f'<span class="dim">[{len(value)} floats]</span>'
    if isinstance(value, (list, tuple, dict)):
        text = json.dumps(value, default=str, ensure_ascii=False)
    else:
        text = str(value)
    if len(text) > MAX_CELL:
        return (f'<span title="{html.escape(text[:2000], quote=True)}">'
                f'{html.escape(text[:MAX_CELL])}'
                f'<span class="dim"> ... +{len(text) - MAX_CELL} chars</span></span>')
    return html.escape(text)


CSS = """
*{box-sizing:border-box}
body{margin:0;font:13px/1.5 ui-sans-serif,system-ui,'Segoe UI',sans-serif;
     color:#1c1917;background:#faf9f7;display:flex;height:100vh;overflow:hidden}
aside{width:250px;flex:none;background:#fff;border-right:1px solid #e7e5e4;
      overflow-y:auto;padding:18px 0}
aside h1{font-size:13px;margin:0 18px 4px;letter-spacing:.02em}
aside .sub{margin:0 18px 16px;font-size:11px;color:#78716c}
aside a{display:flex;justify-content:space-between;gap:8px;padding:7px 18px;
        text-decoration:none;color:#44403c;border-left:2px solid transparent}
aside a:hover{background:#f5f5f4}
aside a.on{background:#f5f5f4;border-left-color:#c2410c;color:#0c0a09;font-weight:600}
aside a .n{color:#a8a29e;font-variant-numeric:tabular-nums;font-weight:400}
main{flex:1;overflow:auto;padding:24px 28px 60px}
h2{margin:0 0 2px;font-size:19px}
.meta{color:#78716c;font-size:12px;margin-bottom:16px}
table{border-collapse:collapse;font-size:12px;background:#fff;
      border:1px solid #e7e5e4;border-radius:6px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid #f0efed;
      white-space:nowrap;max-width:520px;overflow:hidden;text-overflow:ellipsis}
th{background:#f5f5f4;font-weight:600;position:sticky;top:0}
th small{display:block;font-weight:400;color:#a8a29e;font-size:10px}
tr:hover td{background:#fdfcfb}
.null{color:#a8a29e;font-style:italic}
.dim{color:#a8a29e}
.pager{margin-top:14px;display:flex;gap:8px;align-items:center}
.pager a{padding:5px 12px;border:1px solid #d6d3d1;border-radius:5px;
         text-decoration:none;color:#44403c;background:#fff}
.pager a:hover{background:#f5f5f4}
.pager span{color:#78716c}
.empty{color:#78716c;padding:24px 0}
pre{background:#fff;border:1px solid #e7e5e4;border-radius:6px;padding:14px;
    white-space:pre-wrap}
"""


def page(body: str, table_list: list[dict], current: str) -> bytes:
    links = "".join(
        f'<a class="{"on" if t["name"] == current else ""}" '
        f'href="/?table={t["name"]}"><span>{t["name"]}</span>'
        f'<span class="n">{t["rows"]:,}</span></a>'
        for t in table_list
    )
    total = sum(t["rows"] for t in table_list)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(settings.pg.endpoint)} - tables</title>
<style>{CSS}</style></head><body>
<aside>
  <h1>{html.escape(settings.pg.endpoint)}</h1>
  <p class="sub">{len(table_list)} tables &middot; {total:,} rows</p>
  {links}
</aside>
<main>{body}</main>
</body></html>""".encode()


def table_view(name: str, offset: int) -> str:
    cols = columns(name)
    if not cols:
        return f"<h2>{html.escape(name)}</h2><p class='empty'>No such table.</p>"

    data = rows(name, offset)
    total = int(engine.fetch_one(f'SELECT count(*) AS n FROM "{name}"', audit=False)["n"])

    head = "".join(
        f'<th>{html.escape(c["column_name"])}'
        f'<small>{html.escape(c["data_type"])}'
        f'{"" if c["is_nullable"] == "YES" else " &middot; not null"}</small></th>'
        for c in cols
    )
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(r.get(c['column_name']))}</td>" for c in cols) + "</tr>"
        for r in data
    )
    grid = (f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
            if data else "<p class='empty'>This table is empty.</p>")

    first, last = (offset + 1, offset + len(data)) if data else (0, 0)
    nav = []
    if offset:
        nav.append(f'<a href="/?table={name}&amp;offset={max(0, offset - PAGE_SIZE)}">'
                   "&larr; Previous</a>")
    if offset + PAGE_SIZE < total:
        nav.append(f'<a href="/?table={name}&amp;offset={offset + PAGE_SIZE}">'
                   "Next &rarr;</a>")
    pager = (f'<div class="pager">{"".join(nav)}'
             f"<span>Showing {first:,}-{last:,} of {total:,}</span></div>") if total else ""

    return (f"<h2>{html.escape(name)}</h2>"
            f'<p class="meta">{len(cols)} columns &middot; {total:,} rows</p>'
            f"{grid}{pager}")


# ------------------------------------------------------------------ server ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:                                   # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        try:
            table_list = tables()
            default = table_list[0]["name"] if table_list else ""
            name = query.get("table", [default])[0]
            offset = max(0, int(query.get("offset", ["0"])[0] or 0))
            body = table_view(name, offset) if name else "<p class='empty'>No tables.</p>"
            payload = page(body, table_list, name)
        except Exception as exc:                                # keep the tab alive
            payload = page(f"<h2>Database error</h2><pre>{html.escape(str(exc))}</pre>",
                           [], "")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Browse the PostgreSQL tables.")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    health = engine.healthcheck()
    if not health["ok"]:
        print(f"PostgreSQL unavailable at {health['endpoint']}: {health['error']}")
        print("Start PostgreSQL, then:  python -m scripts.setup_db")
        return 1

    url = f"http://127.0.0.1:{args.port}/"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    names = tables()
    print(f"  {health['database']} @ {health['endpoint']}")
    print(f"  {len(names)} tables, {sum(t['rows'] for t in names):,} rows")
    print(f"\n  {url}\n\n  Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        server.server_close()
        engine.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
