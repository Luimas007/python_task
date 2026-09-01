# Samsung Phone Query and Review System

A multi-agent advisory service for Samsung smartphones. Specifications are
scraped from GSMArena into PostgreSQL, and **that database is the only knowledge
the system may use at query time** — the language model runs locally, receives
database rows as context, and performs no retrieval of its own.

Eight named agents cooperate over a typed message protocol. A live operator
console shows which agents are working, the messages they exchange, and every
database round-trip as it happens.

```bash
python app.py          # scrapes and builds on first run, then serves on :8000
```

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [The agents](#the-agents)
- [Protocols](#protocols)
- [Data model](#data-model)
- [How "no external knowledge" is enforced](#how-no-external-knowledge-is-enforced)
- [The NULL policy](#the-null-policy)
- [Phone selection](#phone-selection)
- [Setup](#setup)
- [Running it](#running-it)
- [API](#api)
- [Tests](#tests)
- [Design notes and trade-offs](#design-notes-and-trade-offs)
- [Known limitations](#known-limitations)

---

## What it does

Ask a question in the console and the system routes it to the right pipeline:

| Question | Intent | Agents |
|---|---|---|
| "What are the camera specs of the Galaxy S23?" | `spec_lookup` | ATLAS → SPECTRA → ORACLE → NEXUS → SENTINEL |
| "How does the Galaxy S23 compare to the S22?" | `compare` | ATLAS → SPECTRA → VERSUS → SENTINEL |
| "Which Samsung phone has the best battery life?" | `ranking` | ATLAS → RANKER → NEXUS → SENTINEL |
| "Write a review of the Galaxy Z Fold8" | `review` | ATLAS → SPECTRA → CRITIC → SENTINEL |
| anything else | `general` | ATLAS → ORACLE → NEXUS → SENTINEL |

Every answer carries a grounding verdict: how many of the figures it states were
traced back to database rows.

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
   GSMArena ──────► │  scraper/   fetch → parse → normalise     │   offline,
   (one-time)       │  data/pages/*.html saved verbatim         │   run once
                    └───────────────────┬──────────────────────┘
                                        │
                    ┌───────────────────▼──────────────────────┐
                    │            PostgreSQL 18                  │
                    │  phones · specifications ·                │
                    │  phone_attributes · knowledge_chunks      │
                    │  cosine_similarity()  ← vector search     │
                    └───────────────────┬──────────────────────┘
                                        │ PG-WIRE/3.0
                    ┌───────────────────▼──────────────────────┐
                    │  agents/  NEXUS orchestrates 7 specialists│
                    │           exchanging ACP/1.0 envelopes    │
                    └──────┬────────────────────────┬──────────┘
                           │ OLLAMA-HTTP/1.1        │ WS-TRACE/1.0
                    ┌──────▼───────┐        ┌───────▼──────────┐
                    │ Ollama       │        │ FastAPI + console │
                    │ llama3.2:3b  │        │ live agent view   │
                    │ (local GPU)  │        └───────────────────┘
                    └──────────────┘
```

Scraping is a **build step**, not part of the request path. Once the database is
populated the system never touches the network again — the only outbound
connection at query time is loopback to Ollama.

### Layout

```
app.py                  single entry point: preflight, bring-up, serve
config/settings.py      all tunables, sourced from .env
core/
  events.py             thread-safe trace bus (worker threads → WebSocket)
  protocols.py          the named protocols surfaced in the console
  logging_setup.py
db/
  schema.sql            tables, views, cosine_similarity()
  engine.py             pooled psycopg2 access; traces + audits every query
  repository.py         every read the agents may perform
  loader.py             ingest-side upserts
scraper/
  catalog.py            popularity crawl + flagship-aware selection
  fetcher.py            curl_cffi fetch, saves each page to data/pages/
  parser.py             HTML → verbatim spec rows
  normalizer.py         verbatim text → typed, NULL-honest attributes
rag/
  chunker.py            builds the corpus *from database rows*
  embedder.py           MiniLM on CPU
  indexer.py            writes chunks + embeddings back into PostgreSQL
  retriever.py          hybrid dense + trigram search, executed in SQL
agents/                 base.py + the eight agents
llm/ollama_client.py    local inference, no tools
api/                    FastAPI routes, schemas, WebSocket
frontend/static/        the operator console (single page, no build step)
scripts/                setup_db · scrape_pages · fill_missing · ingest ·
                        build_index · run_pipeline · report
tests/                  parsing · database · agents · api · app
```

---

## The agents

| Agent | Role | Reads DB | Uses LLM | What it contributes |
|---|---|:--:|:--:|---|
| **NEXUS** | Orchestrator | – | yes | Plans the run, routes messages, synthesises the answer |
| **ATLAS** | Query Analyst | yes | fallback | Classifies intent; resolves device names against `phones` |
| **SPECTRA** | Specification Retrieval | yes | – | Pulls typed attributes + the verbatim spec sheet |
| **ORACLE** | Semantic Retrieval | yes | – | Embeds the question, runs hybrid search in SQL |
| **RANKER** | Comparative Analytics | yes | – | `ORDER BY` over whitelisted metrics for superlatives |
| **VERSUS** | Comparison Analyst | yes | yes | Builds the matrix, computes deltas in code, narrates |
| **CRITIC** | Review Writer | yes | yes | Positions a device against the catalogue, writes the review |
| **SENTINEL** | Grounding Auditor | – | – | Checks every figure in the answer against retrieved evidence |

Two deliberate splits:

- **Retrieval agents never call the LLM.** SPECTRA, ORACLE, RANKER and SENTINEL
  are fully deterministic, so the facts entering a prompt are reproducible.
- **Arithmetic never goes through the model.** VERSUS computes every delta in
  Python and hands the model finished numbers to narrate. A 3B model is not
  reliable at subtraction, and a wrong number in a spec comparison is the most
  damaging error the system could make.

---

## Protocols

The console labels every frame with the transport it actually travelled over.

| Protocol | Transport | Carries |
|---|---|---|
| `PG-WIRE/3.0` | TCP 5432, psycopg2 | Every knowledge lookup. Parameterised SQL only |
| `VEC-SQL/1.0` | PL/pgSQL over TCP 5432 | `cosine_similarity(real[], real[])` — vector search runs *inside* Postgres |
| `ACP/1.0` | in-process envelopes | Typed request/response messages between agents |
| `OLLAMA-HTTP/1.1` | HTTP 127.0.0.1:11434 | Local inference. Loopback only |
| `WS-TRACE/1.0` | WebSocket `/ws/trace` | Live agent + protocol frames to the console |

Every SQL statement is also written to a `query_log` table with its agent,
row count and duration, so the traffic is auditable after the fact.

---

## Data model

| Table | Purpose |
|---|---|
| `phones` | One row per device: identity, series, tier, popularity, provenance (source URL, local page path, SHA-256) |
| `specifications` | Verbatim key/value capture of every spec row, plus an explicit NULL row for each canonical spec the page omitted |
| `phone_attributes` | 70 typed columns projected from the verbatim text — every one nullable |
| `knowledge_chunks` | RAG corpus generated *from* the tables above, with its 384-dim embedding stored alongside |
| `query_log` | Audit trail of every database round-trip |
| `conversations`, `messages` | Chat history with intent, agents used and grounding verdict |
| `scrape_runs` | Provenance for each ingest |

Views: `v_phone_overview` (flattened catalogue), `v_coverage` (per-device fill rate).

### Vector search without pgvector

pgvector is not available on this PostgreSQL install, so similarity is a
set-based SQL function:

```sql
CREATE FUNCTION cosine_similarity(a REAL[], b REAL[]) RETURNS DOUBLE PRECISION
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $fn$
    SELECT CASE WHEN s.na = 0 OR s.nb = 0 THEN 0::double precision
                ELSE s.dot / (sqrt(s.na) * sqrt(s.nb)) END
    FROM (SELECT sum(x.v::float8 * y.v::float8) AS dot,
                 sum(x.v::float8 * x.v::float8) AS na,
                 sum(y.v::float8 * y.v::float8) AS nb
          FROM unnest(a) WITH ORDINALITY AS x(v, i)
          JOIN unnest(b) WITH ORDINALITY AS y(v, i) USING (i)) s;
$fn$;
```

This keeps the knowledge base self-contained — no Chroma, no FAISS, no second
store to fall out of sync. At this corpus size a full scan returns in ~200 ms.
It is O(n) and would need pgvector with an HNSW index beyond roughly 10⁵ chunks.

Retrieval fuses that dense score with a `pg_trgm` lexical score (0.75 / 0.25).
The lexical pass is what rescues exact model names and part numbers, which a
384-dim embedding blurs together — "S22" and "S23" are near-identical vectors.

---

## How "no external knowledge" is enforced

Four independent mechanisms, not one:

1. **The corpus is generated from the database.** `rag/chunker.py` renders chunks
   out of `phones` / `phone_attributes` / `specifications`. Retrieval physically
   cannot surface a fact that is not already stored.
2. **The LLM has no tools.** `llm/ollama_client.py` exposes `generate` only. No
   function calling, no browsing, no network beyond loopback.
3. **Every prompt is closed.** System prompts instruct the model to answer only
   from the supplied context and to say so when the context is silent.
4. **SENTINEL audits the output.** Every number in the answer is extracted and
   matched against the evidence the retrieval agents actually pulled. Unmatched
   figures are reported in the response and shown in the console.

Point 4 is the one that matters: the first three are policy, this one is a check.
A figure produced from the model's own weights has no matching source row and
gets flagged.

---

## The NULL policy

If GSMArena does not publish a fact, it is stored as SQL `NULL`. Never `0`,
never `""`, never `"N/A"`, and never a value inferred from a sibling device.

This is enforced at four layers:

- `parser.clean()` maps `""`, `-`, `N/A` and similar to `None`.
- `_fill_absent_specs()` writes an **explicit NULL row** for every canonical spec
  the page omitted, so absence is a recorded fact rather than a missing row.
- Every extractor in `normalizer.py` returns `None` when its pattern does not match.
- Tests assert no placeholder ever reaches the database
  (`test_absent_facts_are_null_never_placeholder`).

Downstream, NULL is handled rather than hidden:

- `RANKER` excludes NULL rows from rankings **and reports how many it excluded**.
- `VERSUS` marks a metric "not comparable" when either side is NULL.
- `CRITIC` is handed a computed list of missing fields, so it cannot claim data
  is absent when it is present, or vice versa.
- `SPECTRA` renders absences as `NOT PUBLISHED (NULL in database)` in the prompt.

---

## Phone selection

The brief asked for 10–15 phones; this build stores **30**, chosen by algorithm
rather than by hand.

1. Crawl GSMArena's own popularity ranking (`samsung-phones-f-9-0-r1-p{1..3}`,
   where `r1` is the popularity sort) — 150 products.
2. Drop everything that is not a phone (tablets, watches, laptops, earbuds).
3. Classify each device into series / generation / variant / tier.
4. **Fill flagship quotas first**, so high-volume budget models cannot crowd out
   the flagships: 6 × Galaxy S base, 6 × S Ultra, 2 × S Plus, 2 × S FE,
   1 × S Edge, 3 × Z Fold, 2 × Z Flip, 2 × Note.
5. Fill the remaining slots by raw popularity.

The result covers **every Galaxy S generation from S21 to S26**, both foldable
lines, the Note line, and the six most-viewed A-series devices. Because step 1
reads the live ranking, re-running it tracks whatever is popular that week while
the quotas keep flagship coverage stable.

---

## Setup

### Verified environment

Built and tested against this machine:

| | |
|---|---|
| OS | Windows 11 (26200) |
| CPU / RAM | Intel i7-12650H, 20 threads / 16 GB |
| GPU | RTX 3050 Laptop, 4 GB VRAM |
| PostgreSQL | 18.6 on `localhost:5432` (no pgvector) |
| Ollama | 0.33.2, `llama3.2:3b` |
| Python | 3.10 (conda env `samsung_phone_system`) |

Those specs drove three decisions: the LLM is a 3B model (q4 weights fit 4 GB
VRAM with context to spare), embeddings run on **CPU** (the installed torch is a
CPU build, and MiniLM embeds the whole corpus in ~11 s anyway), and vector search
lives in SQL because pgvector is not installed.

### Install

```bash
conda create -n samsung_phone_system python=3.10 -y
conda activate samsung_phone_system
pip install -r requirements.txt

ollama pull llama3.2:3b
```

Copy `.env.example` to `.env` and set your PostgreSQL password:

```ini
PG_HOST=localhost
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=your_password_here
PG_DATABASE=samsung_kb
OLLAMA_MODEL=llama3.2:3b
```

The database is created for you; the role just needs `CREATEDB`.

---

## Running it

```bash
python app.py
```

That is the whole thing. `app.py` runs preflight checks, brings the database up
to a usable state if it is not already, starts the API, and opens the console at
**http://127.0.0.1:8000**.

It is self-bootstrapping and safe to re-run. On a first run against an empty
database it creates the schema, scrapes GSMArena, loads the pages and builds the
vector index (~6 minutes, most of it polite crawl delay). On every run after
that it detects the work is already done and starts in a couple of seconds.

```
[1/4] PostgreSQL       PostgreSQL 18.6, connected to localhost:5432/samsung_kb
[2/4] local LLM        llama3.2:3b ready
[3/4] knowledge base   30 phones, 700 chunks, 700 embedded
[4/4] embedding model  all-MiniLM-L6-v2 (384-dim, cpu)

ready in 2.4s
  console   http://127.0.0.1:8000
  API docs  http://127.0.0.1:8000/docs
  trace     ws://127.0.0.1:8000/ws/trace
```

### Options

| Flag | Effect |
|---|---|
| `--port N` / `--host H` | serve somewhere else |
| `--scrape` | force a fresh crawl before starting |
| `--rebuild` | re-ingest the saved pages and rebuild the index (never touches the network) |
| `--reset` | drop every row and rebuild from the saved pages |
| `--no-browser` | do not open a browser window |
| `--check` | run the preflight checks and exit |

### If a dependency is down

`app.py` distinguishes fatal from degraded:

- **PostgreSQL unreachable** → startup fails with the endpoint it tried and what
  to check. There is no knowledge base without it.
- **Ollama unreachable** → warns and starts anyway. Retrieval, ranking and
  comparison still run; answers come back as the computed database rows instead
  of prose, and the console shows a `running without the LLM` note.

### Running the steps individually

`app.py` calls these; you can also run them yourself.

```bash
python -m scripts.setup_db       # create database, apply schema
python -m scripts.scrape_pages   # select 30 phones, save pages to data/pages/
python -m scripts.ingest         # parse saved pages → PostgreSQL
python -m scripts.build_index    # generate chunks + embeddings
python -m scripts.run_pipeline   # all four, with a verification pass
python -m api.main               # serve without the bring-up logic
```

To see exactly what landed in the database — per-device spec coverage, the
attribute fill rate across the catalogue, and a sample of the fields the source
did not publish:

```bash
python -m scripts.report
```

Re-running is safe: pages are cached on disk, and all writes are upserts.
`scripts.ingest` and `scripts.build_index` never touch the network, so you can
rebuild the database from the saved pages at any time:

```bash
python -m scripts.run_pipeline --skip-scrape
```

### If GSMArena rate-limits you

GSMArena throttles by IP and returns `429` for a while once triggered — no TLS
fingerprint gets around it. The fetcher backs off automatically, and any pages it
could not get can be collected later:

```bash
python -m scripts.fill_missing --wait 900 --spacing 75 --rounds 6
python -m scripts.ingest && python -m scripts.build_index
```

Anything already saved is skipped, so this only fetches the gaps.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/ask` | Ask a question. Returns the answer, intent, agents used, grounding verdict and the full trace |
| `GET` | `/api/health` | Database, LLM, embedding and corpus status |
| `GET` | `/api/agents` | Agent roster with roles, capabilities and protocols |
| `GET` | `/api/protocols` | The protocol registry the console renders |
| `GET` | `/api/phones` | Catalogue overview |
| `GET` | `/api/phones/{id}` | Full spec sheet for one device |
| `GET` | `/api/rankings?metric=&limit=` | Ranking over a whitelisted metric |
| `GET` | `/api/rankable` | Which metrics can be ranked |
| `GET` | `/api/history?session_key=` | Stored conversation |
| `GET` | `/api/query-log` | Recent database traffic |
| `WS` | `/ws/trace` | Live agent + protocol event stream |

Interactive docs at `/docs`.

```bash
curl -X POST http://127.0.0.1:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Which Samsung phone has the best battery life?"}'
```

```jsonc
{
  "intent": "ranking",
  "agents_used": ["NEXUS", "ATLAS", "RANKER", "SENTINEL"],
  "answer": "The Samsung Galaxy S26+ and the Samsung Galaxy S26 Ultra have the best battery life, tied at 16.4 hours.",
  "grounding": { "verdict": "grounded", "numeric_claims": 1, "supported": 1, "unsupported": [] },
  "latency_ms": 977
}
```

---

## Tests

```bash
python -m pytest                     # everything
python -m pytest -m parsing          # scraper, no services needed
python -m pytest -m database         # schema, integrity, retrieval
python -m pytest -m agents           # agent behaviour
python -m pytest -m api              # HTTP + WebSocket
python -m pytest -m app              # app.py bring-up logic
python -m pytest -m "not llm"        # skip anything needing Ollama
```

Tests skip cleanly rather than failing when PostgreSQL, Ollama or the scraped
pages are absent.

What they actually check, beyond happy paths:

- Absent facts are NULL and never a placeholder, and NULLs genuinely exist.
- Every numeric attribute is within a physically plausible range.
- `"Galaxy S23"` resolves to the base model, **not** the Ultra.
- An unknown model (`Galaxy S99 Omega`) resolves to nothing and is declared as
  unknown in the answer rather than answered from model memory.
- `rank_by` rejects any column outside the whitelist, including SQL injection.
- SENTINEL flags invented figures and ignores years and list markers.
- Every trace event is JSON-serialisable — a `Decimal` leaking into an event
  detail would silently drop every WebSocket subscriber.

Defects these tests caught during development, all since fixed:

1. `str.title()` rendered the `FE` variant as `Fe`, so the Galaxy S FE flagship
   quota silently matched nothing.
2. `Decimal` and `date` values from database rows leaked into trace events and
   crashed `send_json`, disconnecting the console's live feed after the first
   frame.
3. SENTINEL's number regex rejected any figure followed by a word character, so
   unit-glued claims like `240W` and `5000mAh` — most real claims — were never
   audited.

---

## Design notes and trade-offs

**Rule-first intent classification.** ATLAS uses regex rules and only falls back
to the LLM when no device is named and no comparative cue is present. Rules are
instant and deterministic; a 3B model classifying "which has the best battery"
is neither. The LLM fallback covers the open-ended tail.

**Superlatives are SQL, not RAG.** Asked "which phone has the best battery life",
embeddings return every battery chunk with near-identical scores — the question
is an aggregation, not a similarity search. RANKER answers it with `ORDER BY`,
which is both correct and explainable, and reports the NULL exclusions.

**Hybrid retrieval.** Pure dense search confuses adjacent model numbers. The
trigram pass restores exact-token matching at a 25 % weight.

**Deltas computed in code.** See [The agents](#the-agents).

**Absence stated, not implied.** Early on, CRITIC claimed the Galaxy Z Fold8 had
no IP rating when the database held `IP48`. The model was being asked to notice
what was *missing* from a long sheet, which it does badly. The fix was to compute
the missing-field list in Python and hand it over explicitly. The general
principle: never make the model infer a negative.

**Page HTML is kept.** Every source page is saved under `data/pages/` with its
SHA-256 recorded in `phones`. Parsing can be re-run and re-verified offline
without re-crawling, which also makes the parser tests run against real input.

---

## Known limitations

- **Corpus size.** The catalogue targets 30 devices. If GSMArena rate-limits the
  crawl, fewer will be loaded; `scripts.fill_missing` collects the rest later.
  `GET /api/health` always reports the true count.
- **Vector search is a full scan.** Fine at ~600 chunks (~200 ms); would need
  pgvector + HNSW past roughly 10⁵.
- **SENTINEL audits numbers, not prose.** A wrong chipset *name* would pass the
  audit; a wrong chipset *score* would not. Extending it to entity claims is the
  obvious next step.
- **Small-model prose.** `llama3.2:3b` was chosen to fit 4 GB of VRAM. The facts
  are grounded and checked, but the writing is plainer than a larger model's.
  `OLLAMA_MODEL=gemma3:4b` in `.env` trades some speed for better prose on the
  same hardware.
- **Prices are GSMArena's snapshot** at scrape time, in whatever currencies that
  page listed. Missing currencies are NULL rather than converted.
