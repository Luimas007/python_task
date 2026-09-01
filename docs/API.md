# API Reference

Base URL `http://127.0.0.1:8000` · JSON everywhere · **no authentication**

The same reference renders in the browser at
**[/docs-ui](http://127.0.0.1:8000/docs-ui)**, linked from the assistant's header.
The interactive OpenAPI explorer is at [/docs](http://127.0.0.1:8000/docs).

For the click-by-click Postman walkthrough see **[POSTMAN.md](POSTMAN.md)**, or
import [`postman_collection.json`](postman_collection.json).

---

## Authentication

**None.** The service binds to `127.0.0.1` and is meant for local use, so there
are no keys, tokens or cookies. The only header you ever need is
`Content-Type: application/json`, and only on `POST`.

> If you expose this beyond localhost, put an authenticating reverse proxy in
> front of it. Nothing in this codebase does that for you.

---

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/ask` | Ask a question — the full multi-agent pipeline |
| `GET` | `/api/knowledge` | What the knowledge base holds |
| `POST` | `/api/knowledge/refresh` | Scrape and rebuild the knowledge base |
| `GET` | `/api/knowledge/refresh/status` | Poll refresh progress |
| `GET` | `/api/phones` | Catalogue overview |
| `GET` | `/api/phones/{id}` | Full spec sheet for one device |
| `GET` | `/api/rankings` | League table over one metric |
| `GET` | `/api/rankable` | Which metrics can be ranked |
| `GET` | `/api/agents` | The agent roster |
| `GET` | `/api/protocols` | The protocol registry |
| `GET` | `/api/health` | Database, LLM, embedding and corpus status |
| `GET` | `/api/history` | Stored conversation |
| `GET` | `/api/query-log` | Audit trail of every SQL statement |
| `WS` | `/ws/trace` | Live agent and protocol event stream |

---

## `POST /api/ask`

Runs the whole pipeline. This is what the chat interface calls.

**Request**

| Field | Type | Required | Description |
|---|---|:--:|---|
| `question` | string | yes | 2–2000 characters |
| `session_key` | string | no | Groups turns into one conversation. Default `anonymous` |

```bash
curl -X POST http://127.0.0.1:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Which Samsung phone has the best battery life?"}'
```

**Response**

```jsonc
{
  "run_id": "a1b2c3d4e5f6",
  "answer": "The Samsung Galaxy S26 Ultra has the best battery life, at 16.4 hours.",
  "intent": "ranking",
  "requested_intent": "ranking",
  "agents_used": ["NEXUS", "ATLAS", "RANKER", "SENTINEL"],
  "pipeline":    ["ATLAS", "RANKER", "NEXUS", "SENTINEL"],
  "devices": [],
  "unresolved": [],
  "grounding": {
    "verdict": "grounded",
    "numeric_claims": 1,
    "supported": 1,
    "unsupported": [],
    "support_ratio": 1.0
  },
  "extras": { "ranking": { "rows": [...], "excluded_null_count": 0 } },
  "latency_ms": 977.6,
  "trace": [ /* every agent message and SQL round-trip */ ]
}
```

| Field | Meaning |
|---|---|
| `intent` | The pipeline that **ran**: `spec_lookup`, `compare`, `ranking`, `review`, `general` |
| `requested_intent` | What ATLAS classified, before any downgrade |
| `devices` | Phones resolved against the `phones` table |
| `unresolved` | Mentioned but **not in the database** — reported, never invented |
| `grounding` | SENTINEL's audit of every numeric claim |
| `extras` | Intent-specific: `ranking`, `deltas`, `standings` or `citations` |
| `trace` | The evidence trail: agent hand-offs plus the SQL each one issued |

**How it interacts with the system.** `POST /api/ask` → `NexusAgent.answer()` →
ATLAS classifies and resolves names against `phones` → the intent's agents query
PostgreSQL → a writer agent drafts prose from those rows via Ollama on
localhost → SENTINEL checks every figure → the exchange is stored in
`conversations` / `messages`.

---

## `GET /api/knowledge`

```bash
curl http://127.0.0.1:8000/api/knowledge
```

```jsonc
{
  "ready": true,
  "running": false,
  "stats": { "phones": 10, "spec_rows": 613, "spec_nulls": 38,
             "chunks": 237, "embedded": 237 },
  "phones": [ { "phone_id": 1, "model_name": "Samsung Galaxy S26 Ultra",
                "series": "Galaxy S", "rank": 2 } ],
  "default_limit": 10
}
```

## `POST /api/knowledge/refresh`

Starts the scrape-and-rebuild pipeline. Returns immediately; progress streams
over `/ws/trace` as `kb.*` events. **409** if one is already running.

| Field | Type | Default | Description |
|---|---|---|---|
| `limit` | int 1–100 | 10 | How many phones to load |
| `replace` | bool | `true` | Replace the knowledge base rather than adding to it |
| `offline` | bool | `false` | Skip the network; rebuild from `data/pages/` |

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge/refresh \
  -H "Content-Type: application/json" -d '{"limit": 10}'

{ "started": true, "limit": 10, "stream": "/ws/trace" }
```

> If GSMArena denies access (429/403), the pipeline stops requesting
> **immediately** and finishes from the locally saved pages. The same parser runs
> either way, so the stored data is identical.

## `GET /api/knowledge/refresh/status`

Polling fallback for clients that cannot hold a WebSocket open. Returns
`{"running": bool, "events": [...]}` with every progress event so far.

---

## `GET /api/phones` · `GET /api/phones/{id}`

```bash
curl http://127.0.0.1:8000/api/phones
curl http://127.0.0.1:8000/api/phones/1
```

The detail response carries typed attributes plus every verbatim spec row,
grouped by category. Fields the source never published are `null` — never `0`,
never `""`.

```jsonc
{
  "phone": { "model_name": "Samsung Galaxy S26 Ultra",
             "battery_capacity_mah": 5400,
             "display_protection": null },
  "specs_by_category": {
    "Display": [ { "key": "Size", "value": "6.9 inches, …" },
                 { "key": "Protection", "value": null } ]
  }
}
```

**404** if no phone has that id.

---

## `GET /api/rankings`

| Query param | Type | Default | Description |
|---|---|---|---|
| `metric` | string | `battery_endurance_hours` | Must appear in `/api/rankable` |
| `limit` | int 1–30 | 10 | Rows returned |

```bash
curl "http://127.0.0.1:8000/api/rankings?metric=battery_capacity_mah&limit=5"
```

```jsonc
{
  "column": "battery_capacity_mah",
  "label": "battery capacity",
  "unit": "mAh",
  "direction": "DESC",
  "rows": [ { "model_name": "Samsung Galaxy S26 Ultra", "metric_value": 5400 } ],
  "excluded_null_count": 0
}
```

`metric` is checked against an 18-column whitelist; anything else returns
**400**. A user query can never reach an arbitrary column.

---

## `GET /api/agents` · `GET /api/protocols`

The agent roster (name, role, capabilities, protocols, whether each touches the
database or the LLM) and the five labelled transports.

---

## `GET /api/health`

```jsonc
{
  "status": "ok",
  "database":  { "ok": true, "endpoint": "localhost:5432/samsung_kb",
                 "protocol": "PG-WIRE/3.0" },
  "llm":       { "ok": true, "model": "llama3.2:3b" },
  "embedding": { "model": "…/all-MiniLM-L6-v2", "dim": 384, "device": "cpu" },
  "corpus":    { "phones": 10, "chunks": 237 }
}
```

`status` is `degraded` when the LLM is unreachable — the API still answers,
returning database rows instead of prose.

---

## `GET /api/history` · `GET /api/query-log`

`history` returns the stored conversation for a `session_key`.
`query-log` returns the audit trail: every SQL statement the agents ran, with the
issuing agent, row count and duration. It is the proof answers came from the
database.

---

## `WS /ws/trace`

| Event | When |
|---|---|
| `agent.start` / `agent.end` | An agent begins or finishes. Carries `activity`, the sentence the chat shows |
| `acp.message` | One agent hands off to another |
| `db.query` / `db.write` | A SQL round-trip, with statement and timing |
| `llm.request` / `llm.response` | A call to the local model |
| `kb.*` | Knowledge-base refresh progress |
| `trace.ping` | Keepalive every 25 s — ignore it |

```javascript
const ws = new WebSocket("ws://127.0.0.1:8000/ws/trace");
ws.onmessage = e => console.log(JSON.parse(e.data));
```

---

## Errors

| Status | Meaning | Body |
|---|---|---|
| `400` | Valid JSON, disallowed value — e.g. a `metric` outside the whitelist | `{"detail": "metric must be one of: …"}` |
| `404` | No phone with that id | `{"detail": "no phone with id 999"}` |
| `409` | A knowledge-base refresh is already running | `{"detail": "a knowledge base refresh is already running"}` |
| `422` | Body failed validation — missing or empty `question` | `{"detail": [{"loc": ["body","question"], …}]}` |

There is no `401` or `403`: the API is unauthenticated by design.
