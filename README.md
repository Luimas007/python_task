<div align="center">

# Samsung Phone Query & Review System

**A multi-agent chatbot that answers questions about Samsung phones —
using nothing but a local PostgreSQL database it built itself.**

Scrapes GSMArena · stores in PostgreSQL · retrieves with RAG ·
reasons with eight named agents · checks its own answers

```bash
python app.py
```

</div>

---

## Contents

| | |
|---|---|
| **[Overview](#overview)** · [Features](#main-features) · [Quick start](#quick-start) | Get going in five minutes |
| **[Setup on a new PC](#setup-on-a-new-pc)** | Plain Python, Windows & Linux — copy, paste, run |
| **[System architecture](#system-architecture)** · [Multi-agent](#multi-agent-architecture) · [Agents](#agent-responsibilities) | How it is built |
| **[Scraping](#scraping-workflow)** · [Knowledge base](#knowledge-base-workflow) · [PostgreSQL](#postgresql-architecture) | Where the data comes from |
| **[API](#api-architecture)** · [Frontend](#frontend-architecture) | The surfaces |
| **[Example query](#example-query-workflow)** · [Agent communication](#agent-communication-flow) | Walkthroughs |
| **[Folder structure](#project-folder-structure)** · [Install notes](#installation-notes) · [Usage](#using-the-chat-interface) | Reference |
| **[Postman](#testing-the-api-with-postman)** · [Troubleshooting](#troubleshooting) | Demo and repair |

---

## Overview

Ask *"How does the Galaxy S25 Ultra compare to the S24 Ultra?"* and eight agents
cooperate to answer it: one works out what you asked, one pulls the
specifications from PostgreSQL, one computes the differences, one writes the
comparison, and one checks that every number in the reply actually came from the
database.

The rule the whole design serves:

> **The PostgreSQL database is the only knowledge the system may use.**
> The language model runs locally, receives database rows as context, and has no
> tools, no browsing and no retrieval of its own.

That is not just policy — the last agent in every run extracts each figure from
the answer and matches it against the rows that were actually retrieved. An
invented number has no matching row and gets flagged.

### The demonstration in one picture

```
   Start the app                 python app.py
        │
        ▼
   Refresh the knowledge base    click "Knowledge base" → "Refresh"
        │
        ▼
   Scrape the top 10 phones      GSMArena popularity ranking
        │                        (falls back to local pages if blocked)
        ▼
   Extract the information       verbatim specs + 70 typed attributes
        │                        absent facts stored as NULL
        ▼
   Store in PostgreSQL           phones · specifications · phone_attributes
        │
        ▼
   Knowledge base ready          + embeddings in knowledge_chunks
        │
        ▼
   Ask a question                "compare the S25 Ultra and S24 Ultra"
        │
        ▼
   Agents communicate            ATLAS → SPECTRA → VERSUS → SENTINEL
        │
        ▼
   Agents query PostgreSQL       every fact comes from a row
        │
        ▼
   LLM writes the answer         llama3.2:3b, local, no internet
        │
        ▼
   Final answer                  + "17/17 figures verified"
```

---

## Main features

| | |
|---|---|
| **Chat interface** | Clean, ChatGPT-style. Shows each agent working, in plain English |
| **One-click knowledge base** | Scrape the top N Samsung phones live, with per-phone progress |
| **Automatic fallback** | The first time GSMArena denies access, it switches to local pages and stops asking |
| **Eight named agents** | Each in its own file, each with a documented job |
| **Prompts in one place** | Every system prompt in `agents/prompts.py`, never buried in code |
| **Grounding audit** | Every number in every answer is checked against retrieved rows |
| **Honest NULLs** | Missing facts are `NULL` — never `0`, `""` or a guess |
| **In-database vector search** | Cosine similarity as a SQL function; no external vector store |
| **Full audit trail** | Every SQL statement logged with agent, row count and duration |
| **API + docs + Postman** | REST API, a docs page in the app, and an importable collection |
| **92 tests** | Parser, database, agents, API, scraper fallback and startup |

---

## Quick start

*The Anaconda version. Using plain Python instead? Go straight to
**[Setup on a new PC](#setup-on-a-new-pc)** below — full copy-paste commands for
Windows and Linux.*

### Requirements

| | |
|---|---|
| Python | 3.10+ |
| PostgreSQL | 14+ running locally (pgvector **not** required) |
| [Ollama](https://ollama.com) | for the local language model |
| RAM / VRAM | 8 GB RAM; a 4 GB GPU is plenty |

### Install

```bash
git clone https://github.com/Luimas007/python_task.git && cd python_task

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
SCRAPE_DEMO_COUNT=10
```

The database itself is created for you — the role just needs `CREATEDB`.

### Run

```bash
python app.py
```

```
[1/4] PostgreSQL       PostgreSQL 18.6, connected to localhost:5432/samsung_kb
[2/4] local LLM        llama3.2:3b ready
[3/4] knowledge base   10 phones, 237 chunks, 237 embedded
[4/4] embedding model  all-MiniLM-L6-v2 (384-dim, cpu)

ready in 11.0s
  console   http://127.0.0.1:8000
  API docs  http://127.0.0.1:8000/docs-ui
```

A browser opens on the chat. If the knowledge base is empty it offers to fill it.

| Flag | Effect |
|---|---|
| `--scrape` | Refresh the knowledge base before starting |
| `--limit N` | How many phones to load (default 10) |
| `--rebuild` | Rebuild from `data/pages/` — never touches the network |
| `--reset` | Drop every row, then rebuild |
| `--port N` / `--host H` | Serve elsewhere |
| `--no-browser` | Do not open a window |
| `--check` | Run preflight checks and exit |

---

## Setup on a new PC

**Plain Python — no Anaconda.** Copy each block, paste, press Enter. Three
blocks, and one small file to edit in the middle.

First install these four (skip any you already have):
[Python 3.10+](https://www.python.org/downloads/) ·
[PostgreSQL 14+](https://www.postgresql.org/download/) ·
[Ollama](https://ollama.com/download) ·
[Git](https://git-scm.com/downloads)

> **Windows:** tick **"Add Python to PATH"** in the Python installer, and
> remember the password you set for the `postgres` user — you need it in step 2.

---

### Windows (PowerShell)

**1 · Clone and install** — takes 5–10 minutes.

```powershell
git clone https://github.com/Luimas007/python_task.git
cd python_task
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
notepad .env
```

**2 · Edit `.env`** — Notepad just opened it. See
[what to change](#3-what-to-change-in-env) below, save, close.

**3 · Build and run**

```powershell
python -m scripts.setup_db
ollama pull llama3.2:3b
python app.py
```

<details>
<summary>PowerShell says <code>running scripts is disabled on this system</code></summary>

Run this once, then start block 1 again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
</details>

---

### Linux / macOS

**1 · Clone and install** — takes 5–10 minutes. The `torch` line pulls the CPU
build; without it pip downloads 2.5 GB of CUDA libraries this project never uses.

```bash
git clone https://github.com/Luimas007/python_task.git
cd python_task
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
cp .env.example .env
nano .env
```

**2 · Edit `.env`** — nano just opened it. See
[what to change](#3-what-to-change-in-env) below, then `Ctrl+O`, `Enter`,
`Ctrl+X`.

**3 · Build and run**

```bash
python -m scripts.setup_db
ollama pull llama3.2:3b
python app.py
```

<details>
<summary>PostgreSQL not installed or password unknown (Ubuntu / Debian)</summary>

```bash
sudo apt install -y postgresql
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
```

Then put `postgres` as the password in `.env`.
</details>

---

### 3 · What to change in `.env`

You copied `.env.example` to `.env`. It has ~25 settings, all with working
defaults. **Only one usually needs changing:**

```ini
PG_PASSWORD=your_password_here
```

Replace `your_password_here` with your actual PostgreSQL password — the one you
chose when installing PostgreSQL. Save the file.

Change these too **only if they are not the defaults on your machine**:

| Variable | Default | Change it if… |
|---|---|---|
| `PG_PASSWORD` | `postgres` | **Always** — set your real password |
| `PG_USER` | `postgres` | You use a different PostgreSQL user |
| `PG_PORT` | `5432` | PostgreSQL runs on another port |
| `PG_HOST` | `localhost` | The database is on another machine |
| `PG_DATABASE` | `samsung_kb` | You want a different database name |
| `OLLAMA_MODEL` | `llama3.2:3b` | You pulled a different model |
| `API_PORT` | `8000` | Port 8000 is already taken |

Leave everything else alone. `.env` is gitignored, so your password stays on
your machine.

---

### That's it

`python app.py` builds the knowledge base from the 30 saved pages in
`data/pages/` and opens <http://127.0.0.1:8000>. First run takes about 30
seconds.

```
[1/4] PostgreSQL       PostgreSQL 18.6, connected to localhost:5432/samsung_kb
[2/4] local LLM        llama3.2:3b ready
[3/4] knowledge base   10 phones, 237 chunks, 237 embedded
[4/4] embedding model  all-MiniLM-L6-v2 (384-dim, cpu)

ready in 27.0s
  console   http://127.0.0.1:8000
```

Ask it *"Which Samsung phone has the best battery life?"*

**Every time after this**, you only need to activate the environment and run:

```bash
# Windows:  .\.venv\Scripts\Activate.ps1
# Linux:    source .venv/bin/activate
python app.py
```

---

### If something goes wrong

Run this first — it names the broken piece instead of making you guess:

```bash
python app.py --check
```

| What you see | Fix |
|---|---|
| `'python' is not recognized` | Reinstall Python with **"Add Python to PATH"** ticked |
| `running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` |
| `cannot reach PostgreSQL` | Start the PostgreSQL service, then check `PG_PASSWORD` in `.env` |
| `password authentication failed` | Wrong `PG_PASSWORD` in `.env` |
| `permission denied to create database` | `sudo -u postgres psql -c "ALTER USER postgres CREATEDB;"` |
| `LLM unavailable` | `ollama serve` in another terminal. Optional — the app still works, answering with tables instead of prose |
| `port already in use` | `python app.py --port 8001` |
| `knowledge base is empty` | Check `data/pages/` has ~30 `.html` files, then `python app.py --rebuild` |

---

## System architecture

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                          BUILD TIME (once)                          │
 │                                                                     │
 │   GSMArena ──► fetcher ──► parser ──► normalizer ──► PostgreSQL     │
 │   popularity     │          verbatim    70 typed                    │
 │   ranking        │          spec rows   attributes                  │
 │                  ▼                                                  │
 │            data/pages/*.html  ◄── fallback source when blocked      │
 └─────────────────────────────────────────────────────────────────────┘
                                    │
 ┌──────────────────────────────────┼──────────────────────────────────┐
 │                          QUERY TIME                                 │
 │                                  ▼                                  │
 │  ╔═══════════════════════════════════════════════════════════════╗  │
 │  ║                  PostgreSQL   samsung_kb                      ║  │
 │  ║   phones · specifications · phone_attributes                  ║  │
 │  ║   knowledge_chunks (+ 384-dim embeddings)                     ║  │
 │  ║   cosine_similarity()  ← vector search runs INSIDE the DB     ║  │
 │  ╚═══════════════════════════════════════════════════════════════╝  │
 │                    ▲                          ▲                     │
 │                    │ PG-WIRE/3.0              │ VEC-SQL/1.0         │
 │  ┌─────────────────┴──────────────────────────┴──────────────────┐  │
 │  │  agents/    NEXUS orchestrates 7 specialists over ACP/1.0     │  │
 │  └────────────┬───────────────────────────────┬─────────────────┘  │
 │               │ OLLAMA-HTTP/1.1               │ WS-TRACE/1.0        │
 │      ┌────────▼─────────┐          ┌──────────▼──────────┐          │
 │      │  Ollama          │          │  FastAPI + chat UI  │          │
 │      │  llama3.2:3b     │          │  live agent view    │          │
 │      │  127.0.0.1 only  │          └─────────────────────┘          │
 │      └──────────────────┘                                           │
 └─────────────────────────────────────────────────────────────────────┘
```

Scraping is a **build step**, not part of answering. Once the database is
populated the only outbound connection at query time is loopback to Ollama.

### The five protocols

Every frame in the trace is labelled with the transport it travelled over.

| Protocol | Transport | Carries |
|---|---|---|
| `PG-WIRE/3.0` | TCP 5432, psycopg2 | Every knowledge lookup. Parameterised SQL only |
| `VEC-SQL/1.0` | PL/pgSQL over TCP 5432 | `cosine_similarity(real[], real[])` inside Postgres |
| `ACP/1.0` | in-process envelopes | Typed messages between agents |
| `OLLAMA-HTTP/1.1` | HTTP 127.0.0.1:11434 | Local inference. Loopback only |
| `WS-TRACE/1.0` | WebSocket `/ws/trace` | Live events to the browser |

---

## Multi-agent architecture

```
                        ┌──────────────────────────────┐
   User question ─────► │  NEXUS      the orchestrator │
                        └──────────────┬───────────────┘
                                       │ ACP/1.0
                                       ▼
                        ┌──────────────────────────────┐
                        │  ATLAS      what is asked?    │──► SELECT phones
                        └──────────────┬───────────────┘
                                       │ intent + phone_ids
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
      ┌───────────────┐       ┌───────────────┐        ┌───────────────┐
      │   SPECTRA     │       │    ORACLE     │        │    RANKER     │
      │ spec sheets   │       │ vector search │        │ league tables │
      └───────┬───────┘       └───────┬───────┘        └───────┬───────┘
              │                       │                        │
              ▼                       ▼                        ▼
      ╔═══════════════════════════════════════════════════════════════╗
      ║                    PostgreSQL  samsung_kb                     ║
      ╚═══════════════════════════════════════════════════════════════╝
              │                                                │
              ▼                                                ▼
      ┌───────────────┐  ┌───────────────┐          ┌───────────────┐
      │    VERSUS     │  │    CRITIC     │          │     NEXUS     │
      │  comparison   │  │    review     │          │  synthesis    │
      └───────┬───────┘  └───────┬───────┘          └───────┬───────┘
              └──────────────────┴──────────────────────────┘
                                       │  draft answer
                                       ▼
                        ┌──────────────────────────────┐
                        │  SENTINEL   every number ok?  │
                        └──────────────┬───────────────┘
                                       ▼
                                 Final answer
```

### Agent responsibilities

| Agent | Role | File | Reads DB | Uses LLM | Prompt |
|---|---|---|:--:|:--:|---|
| **NEXUS** | Orchestrator — plans the run, routes messages, writes the answer | [`agents/nexus.py`](agents/nexus.py) | – | yes | `NEXUS_SYNTHESIS` |
| **ATLAS** | Query Analyst — classifies intent, resolves phone names | [`agents/atlas.py`](agents/atlas.py) | yes | fallback | `ATLAS_CLASSIFY` |
| **SPECTRA** | Specification Retrieval — pulls spec sheets | [`agents/spectra.py`](agents/spectra.py) | yes | – | none |
| **ORACLE** | Semantic Retrieval — hybrid vector search | [`agents/oracle.py`](agents/oracle.py) | yes | – | none |
| **RANKER** | Comparative Analytics — superlatives via SQL | [`agents/ranker.py`](agents/ranker.py) | yes | – | none |
| **VERSUS** | Comparison Analyst — computes deltas, narrates | [`agents/versus.py`](agents/versus.py) | yes | yes | `VERSUS_COMPARE` |
| **CRITIC** | Review Writer — positions a device against the catalogue | [`agents/critic.py`](agents/critic.py) | yes | yes | `CRITIC_REVIEW` |
| **SENTINEL** | Grounding Auditor — verifies every figure | [`agents/sentinel.py`](agents/sentinel.py) | – | – | none |

> **Full detail per agent — input, output, SQL, prompt — in
> [`docs/AGENTS.md`](docs/AGENTS.md).**

Two deliberate splits:

- **Retrieval agents never call the LLM.** SPECTRA, ORACLE, RANKER and SENTINEL
  are fully deterministic, so the facts entering a prompt are reproducible — and
  the audit cannot itself hallucinate.
- **Arithmetic never goes through the model.** VERSUS computes every difference
  in Python. A wrong number in a spec comparison is the worst error this system
  could make.

### Where the prompts live

All four, in [`agents/prompts.py`](agents/prompts.py):

```bash
python -m agents.prompts            # print them all
python -m agents.prompts VERSUS     # print one
python -m agents.prompts SPECTRA    # → "deterministic, it has no prompt"
```

---

## Scraping workflow

```
  1. DISCOVER      GET samsung-phones-f-9-0-r1-p{1,2,3}.php
                   (`r1` = GSMArena's own popularity sort — 150 products)
                        │
                        ▼
  2. FILTER        drop tablets, watches, laptops, earbuds
                        │
                        ▼
  3. CLASSIFY      series · generation · variant · tier
                   "Galaxy S25 Ultra" → Galaxy S, gen 25, Ultra, flagship
                        │
                        ▼
  4. SELECT        flagship quotas first, then raw popularity
                        │
                        ▼
  5. FETCH         save each page to data/pages/<slug>.html
                        │
                   ┌────┴────────────────────────────┐
                   │  HTTP 429 / 403 ?               │
                   │       └─► YES: stop asking.     │
                   │           Use data/pages/.      │
                   └────┬────────────────────────────┘
                        ▼
  6. PARSE         GSMArena tags every cell with `data-spec`
                   → verbatim rows; absent codes recorded as NULL
                        │
                        ▼
  7. NORMALIZE     70 typed columns; every extractor returns None
                   when the text does not state the fact
                        │
                        ▼
  8. STORE         UPSERT phones / specifications / phone_attributes
```

### Phone selection

The brief asks for 10–15 phones; the selector handles any N and the demo loads
**10**.

1. Crawl GSMArena's popularity ranking — 150 products.
2. Drop everything that is not a phone.
3. Classify each into series / generation / variant / tier.
4. **Fill flagship quotas first** so high-volume budget models cannot crowd out
   the flagships: 6 × Galaxy S base, 6 × S Ultra, 2 × S+, 2 × S FE, 1 × S Edge,
   3 × Z Fold, 2 × Z Flip, 2 × Note.
5. Fill remaining slots by raw popularity.

At `--limit 30` this covers **every Galaxy S generation from S21 to S26**, both
foldable lines, the Note line, and the six most-viewed A-series devices. At
`--limit 10` the quotas spend every slot on flagships — which is what you want
in a demo.

### When GSMArena says no

GSMArena rate-limits by IP and, once it starts refusing, keeps refusing for a
long while regardless of TLS fingerprint. **Retrying only extends the block.**

So the first access denial (`429`, `403`, `401`, `503`) flips the fetcher into
offline mode for the rest of the run:

```
  live fetch ──► HTTP 429 ──► ┌─────────────────────────────┐
                              │  offline = True             │
                              │  block_reason = "HTTP 429"  │
                              │  no further requests sent   │
                              └──────────┬──────────────────┘
                                         ▼
                              data/pages/<slug>.html
                                         │
                                         ▼
                              same parser, same normalizer,
                              same rows in PostgreSQL
```

The console says so plainly: *"GSMArena denied access, so the locally saved
pages are being used instead. Same parser, same result."*

This is covered by tests — including one asserting that a blocked origin is
contacted exactly **once**.

### The NULL policy

If GSMArena does not publish a fact, it is stored as SQL `NULL`. Never `0`,
never `""`, never `"N/A"`, never a value inferred from a sibling device.

Enforced at four layers:

- `parser.clean()` maps `""`, `-`, `N/A` to `None`.
- `_fill_absent_specs()` writes an **explicit NULL row** for every canonical spec
  the page omitted — absence is a recorded fact, not a missing row.
- Every extractor in `normalizer.py` returns `None` when its pattern fails.
- Tests assert no placeholder ever reaches the database.

Downstream, NULL is handled rather than hidden:

| Agent | Behaviour |
|---|---|
| RANKER | Excludes NULLs from rankings **and reports how many** |
| VERSUS | Marks a metric "not comparable" when either side is NULL |
| CRITIC | Is handed a computed list of missing fields, so it cannot misreport them |
| SPECTRA | Renders absences as `NOT PUBLISHED (NULL in database)` |

---

## Knowledge base workflow

```
   ┌──────────────┐   POST /api/knowledge/refresh {"limit": 10}
   │   Browser    │ ─────────────────────────────────────────────►
   └──────┬───────┘                                          FastAPI
          │                                                     │
          │  WS /ws/trace                          background thread
          │  ◄───── kb.discover ──────────────────────────────  │
          │  ◄───── kb.catalogue  (top 10 selected) ──────────  │
          │  ◄───── kb.cleared ───────────────────────────────  │
          │  ◄───── kb.phone  S26 Ultra  scraping ────────────  │
          │  ◄───── kb.phone  S26 Ultra  added ───────────────  │
          │  ◄───── kb.phone  S26        scraping ────────────  │
          │              ⋮                                      │
          │  ◄───── kb.indexing ──────────────────────────────  │
          │  ◄───── kb.done   "10 phones loaded" ─────────────  │
          ▼
   ✓ Samsung Galaxy S26 Ultra — added · 64 specs · 3 NULL
   ✓ Samsung Galaxy S26      — added · 63 specs · 3 NULL
   ⏳ Samsung Galaxy S25 Ultra — Scraping…
```

From the CLI:

```bash
python -m scripts.refresh                # top 10
python -m scripts.refresh --limit 30     # the full catalogue
python -m scripts.refresh --offline      # never touch the network
python -m scripts.refresh --add          # add rather than replace
```

---

## PostgreSQL architecture

```
                    ┌──────────────────────┐
                    │       phones         │  identity, series, tier,
                    │  ──────────────────  │  popularity, provenance
                    │  phone_id  (PK)      │  (source URL, local path, SHA-256)
                    └──────────┬───────────┘
                               │ 1
             ┌─────────────────┼─────────────────┐
             │ N               │ 1               │ N
  ┌──────────▼─────────┐  ┌────▼──────────┐  ┌───▼────────────────┐
  │  specifications    │  │phone_attributes│  │ knowledge_chunks   │
  │ ────────────────── │  │ ────────────── │  │ ────────────────── │
  │ category           │  │ 70 typed cols  │  │ section, content   │
  │ spec_key           │  │ every one      │  │ embedding REAL[384]│
  │ spec_value  (NULL  │  │ nullable       │  │ ← generated FROM   │
  │   = not published) │  │                │  │   the tables left  │
  └────────────────────┘  └────────────────┘  └────────────────────┘

  ┌────────────────┐  ┌──────────────┐  ┌──────────────────────────┐
  │   query_log    │  │ conversations│  │      scrape_runs         │
  │ every SQL stmt │  │  + messages  │  │  provenance per ingest   │
  └────────────────┘  └──────────────┘  └──────────────────────────┘

  views:  v_phone_overview (flattened)   v_coverage (fill rate per device)
```

### Vector search without pgvector

pgvector is not installed on the target server, so similarity is a set-based SQL
function:

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

The knowledge base stays self-contained — no Chroma, no FAISS, no second store
to fall out of sync. At this corpus size a full scan returns in ~200 ms. It is
O(n) and would need pgvector with an HNSW index beyond roughly 10⁵ chunks.

Retrieval fuses that dense score with a `pg_trgm` lexical score (0.75 / 0.25).
The lexical pass rescues exact model numbers, which a 384-dimension embedding
blurs together.

### How the "only PostgreSQL" rule is enforced

Four mechanisms, only the last of which is a check rather than a policy:

1. **The RAG corpus is generated from the database.** `rag/chunker.py` renders
   chunks out of `phones` / `phone_attributes` / `specifications`. Retrieval
   physically cannot surface a fact that is not stored.
2. **The LLM has no tools.** `backend/llm/ollama_client.py` exposes `generate`
   only. No function calling, no browsing, loopback network only.
3. **Every prompt is closed** — answer from the context, say so when it is silent.
4. **SENTINEL audits the output.** Every number in the answer is extracted and
   matched against the evidence actually retrieved. Unmatched figures are
   reported in the response and shown in the chat.

---

## API architecture

```
   Browser ──── POST /api/ask ────► FastAPI ──► run_in_threadpool
                                       │              │
                                       │              ▼
                                       │      NexusAgent.answer()
                                       │              │
                                       │       agents ⇄ PostgreSQL
                                       │       agents ⇄ Ollama
                                       │              │
                                       │◄─── result + trace
                                       ▼
   Browser ◄──── JSON  {answer, agents_used, grounding, trace}
      ▲
      └───── WS /ws/trace ◄── live agent + protocol events (during the run)
```

The agents run in a worker thread; the trace bus bridges them back to the
asyncio loop so the browser sees each step as it happens.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/ask` | Ask a question |
| `GET` | `/api/knowledge` | What the knowledge base holds |
| `POST` | `/api/knowledge/refresh` | Scrape and rebuild |
| `GET` | `/api/phones` · `/api/phones/{id}` | Catalogue and spec sheets |
| `GET` | `/api/rankings` · `/api/rankable` | League tables |
| `GET` | `/api/agents` · `/api/protocols` | Roster and transports |
| `GET` | `/api/health` | Status of every dependency |
| `GET` | `/api/history` · `/api/query-log` | Conversation and audit trail |
| `WS` | `/ws/trace` | Live event stream |

**Authentication: none.** The service binds to `127.0.0.1`. The only header you
ever need is `Content-Type: application/json`, on `POST` only.

> **Full reference: [`docs/API.md`](docs/API.md), or
> [/docs-ui](http://127.0.0.1:8000/docs-ui) in the running app.**
> Interactive OpenAPI at [/docs](http://127.0.0.1:8000/docs).

---

## Frontend architecture

One page, no build step, no framework — `frontend/static/index.html`.

```
 ┌──────────────────────────────────────────────────────────────┐
 │  S  Samsung Phone Assistant       ● 10 phones  [KB]  [Docs]  │
 ├──────────────────────────────────────────────────────────────┤
 │                                                              │
 │                    ┌────────────────────────────────────┐    │
 │                    │ How does the S25 Ultra compare…?   │    │
 │                    └────────────────────────────────────┘    │
 │   ┌──┐  ┌──────────────────────────────────────────────┐     │
 │   │ S│  │ ⟳ Agents are working…                    ▾  │     │
 │   └──┘  │  ✓ ATLAS    Reading your question…          │     │
 │         │  ✓ SPECTRA  Fetching the full spec sheet     │     │
 │         │             for the S25 Ultra and S24 Ultra  │     │
 │         │             from PostgreSQL                  │     │
 │         │  ⟳ VERSUS   Comparing S25 Ultra and S24 Ultra│     │
 │         └──────────────────────────────────────────────┘     │
 │                                                              │
 │         The Samsung Galaxy S25 Ultra outperforms…            │
 │                                                              │
 │         [compare] [ATLAS → SPECTRA → VERSUS → SENTINEL]      │
 │         [17/17 figures verified against PostgreSQL]          │
 ├──────────────────────────────────────────────────────────────┤
 │  Ask about a Samsung phone…                            [↑]   │
 └──────────────────────────────────────────────────────────────┘
```

- **Agent activity** streams over the WebSocket. Each agent reports one plain
  sentence — its `activity()` method — as it starts, and ticks green when done.
  The panel collapses to a one-line summary once the answer arrives.
- **Grounding chip** shows how many figures were verified against the database.
- **Knowledge base panel** runs the refresh and shows each phone as it lands.
- **Light and dark** follow the system theme.

---

## Example query workflow

*"How does the Galaxy S25 Ultra compare to the S24 Ultra?"*

```
 1  ATLAS      rule match on "compare"; resolves two names
                 SQL  SELECT phone_id, model_name … FROM phones
                 →    intent=compare, phone_ids=[4, 6]

 2  SPECTRA    pulls both spec sheets
                 SQL  SELECT p.*, a.* FROM phones p
                      LEFT JOIN phone_attributes a USING (phone_id)
                      WHERE p.phone_id = %s                      ×2
                 SQL  SELECT category, spec_key, spec_value
                      FROM specifications WHERE phone_id = %s    ×2
                 →    2 sheets, 6 fields recorded as NULL

 3  VERSUS     builds the matrix and computes every delta in Python
                 SQL  SELECT … FROM phones LEFT JOIN phone_attributes
                      WHERE phone_id = ANY(%s)
                 →    17 comparable metrics; S25 Ultra leads on 9
                 LLM  narrates the differences it was handed
                      (it is told which side wins, never the gap)

 4  SENTINEL   extracts every number from the draft and matches it
                 →    17/17 traced to retrieved rows → "grounded"

 5  NEXUS      returns  {answer, agents_used, pipeline, grounding, trace}
```

Total: about 6 seconds, of which ~5 is the language model.

### Other intents

| Question | Intent | Pipeline |
|---|---|---|
| "What are the camera specs of the S25 Ultra?" | `spec_lookup` | ATLAS → SPECTRA → ORACLE → NEXUS → SENTINEL |
| "How does the S25 Ultra compare to the S24 Ultra?" | `compare` | ATLAS → SPECTRA → VERSUS → SENTINEL |
| "Which phone has the best battery life?" | `ranking` | ATLAS → RANKER → NEXUS → SENTINEL |
| "Write a review of the S25 Ultra" | `review` | ATLAS → SPECTRA → CRITIC → SENTINEL |
| anything else | `general` | ATLAS → ORACLE → NEXUS → SENTINEL |

---

## Agent communication flow

Agents never call each other's methods. They exchange `Envelope` objects over
the **Agent Communication Protocol (ACP/1.0)**, routed by NEXUS:

```
User
 │
 ▼
ATLAS  ─── SELECT phones ──────────────► PostgreSQL
 │     ◄── matched devices ─────────────┘
 │  Envelope(sender=NEXUS, recipient=SPECTRA,
 │           intent="fetch.specs", payload={phone_ids:[4,6]})
 ▼
SPECTRA ── SELECT phone_attributes ────► PostgreSQL
 │      ◄─ specification rows ──────────┘
 │  Envelope(sender=SPECTRA, recipient=VERSUS,
 │           intent="compare.devices", payload={rendered:[…]})
 ▼
VERSUS ─── deltas computed in Python
 │     ─── OLLAMA-HTTP/1.1 ────────────► llama3.2:3b (localhost)
 │     ◄── prose ───────────────────────┘
 ▼
SENTINEL ─ every figure checked against the retrieved rows
 │
 ▼
Final response
```

Every hand-off is published on the trace bus, so the flow the console draws is
the flow that actually happened. Watch it live at `/ws/trace`, or read it from
the `trace` array in any `/api/ask` response.

---

## Project folder structure

```
py_task/
│
├── app.py                    ← single entry point: preflight, bring-up, serve
├── README.md
├── requirements.txt
├── .env.example
├── pytest.ini
│
├── agents/                   ← the eight agents, one file each
│   ├── prompts.py            ← EVERY system prompt, in one place
│   ├── base.py               ← Envelope, AgentCard, ACP plumbing
│   ├── nexus.py              ← 1. Orchestrator
│   ├── atlas.py              ← 2. Query Analyst
│   ├── spectra.py            ← 3. Specification Retrieval
│   ├── oracle.py             ← 4. Semantic Retrieval
│   ├── ranker.py             ← 5. Comparative Analytics
│   ├── versus.py             ← 6. Comparison Analyst
│   ├── critic.py             ← 7. Review Writer
│   └── sentinel.py           ← 8. Grounding Auditor
│
├── api/                      ← FastAPI surface
│   ├── main.py               ← app, lifespan, static + docs routes
│   ├── routes.py             ← every endpoint and the trace WebSocket
│   └── schemas.py            ← request/response models
│
├── backend/                  ← shared infrastructure
│   ├── config/settings.py    ← every tunable, sourced from .env
│   ├── core/
│   │   ├── events.py         ← thread-safe trace bus (workers → WebSocket)
│   │   ├── protocols.py      ← the five named protocols
│   │   └── logging_setup.py
│   ├── llm/ollama_client.py  ← local inference; no tools, loopback only
│   └── rag/
│       ├── chunker.py        ← builds the corpus FROM database rows
│       ├── embedder.py       ← MiniLM on CPU
│       ├── indexer.py        ← writes chunks + embeddings back to Postgres
│       └── retriever.py      ← hybrid dense + trigram search, in SQL
│
├── database/                 ← everything PostgreSQL
│   ├── schema.sql            ← tables, views, cosine_similarity()
│   ├── engine.py             ← pooled psycopg2; traces + audits every query
│   ├── repository.py         ← EVERY read an agent may perform
│   └── loader.py             ← ingest-side upserts
│
├── scraper/                  ← GSMArena → structured rows
│   ├── catalog.py            ← popularity crawl + flagship-aware selection
│   ├── fetcher.py            ← fetch, save, and fall back when blocked
│   ├── parser.py             ← HTML → verbatim spec rows
│   ├── normalizer.py         ← verbatim text → 70 typed, NULL-honest columns
│   └── pipeline.py           ← the refresh generator, with progress events
│
├── frontend/static/
│   ├── index.html            ← the chat interface
│   └── docs.html             ← the API reference page
│
├── docs/
│   ├── AGENTS.md             ← every agent: input, output, SQL, prompt
│   ├── API.md                ← full endpoint reference
│   ├── POSTMAN.md            ← step-by-step Postman guide
│   └── postman_collection.json
│
├── scripts/
│   ├── setup_db.py           ← create the database and apply the schema
│   ├── refresh.py            ← CLI knowledge-base refresh
│   └── report.py             ← what the database actually holds
│
├── tests/                    ← 92 tests
│   ├── test_parsing.py       ← parser, normalizer, catalogue selection
│   ├── test_database.py      ← schema, NULL policy, resolution, retrieval
│   ├── test_agents.py        ← agent behaviour and the grounding guarantee
│   ├── test_api.py           ← HTTP + WebSocket surface
│   ├── test_scraper.py       ← fetcher fallback and the refresh pipeline
│   └── test_app.py           ← startup and bring-up logic
│
└── data/
    ├── pages/                ← saved source HTML (the fallback corpus)
    └── catalog.json          ← the current selection
```

---

## Installation notes

Step-by-step commands are in **[Setup on a new PC](#setup-on-a-new-pc)**, near
the top of this file.

**PostgreSQL.** Any 14+ server works. pgvector is *not* needed — similarity is a
plain SQL function. The role needs `CREATEDB` the first time.

**Model sizing.** `llama3.2:3b` at q4 fits a 4 GB GPU with context to spare.
Embeddings run on **CPU** (MiniLM does the whole corpus in ~11 s), leaving the
GPU to the language model. For better prose on the same hardware, set
`OLLAMA_MODEL=gemma3:4b` in `.env`.

---


## Refreshing the knowledge base

**From the chat** — click **Knowledge base** in the header, choose how many
phones, click **Refresh knowledge base**. Each phone appears as it is stored.

**From the CLI**

```bash
python -m scripts.refresh --limit 10
python -m scripts.refresh --offline      # from data/pages/ only
```

**From the API**

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge/refresh \
  -H "Content-Type: application/json" -d '{"limit": 10}'
```

A refresh **replaces** the knowledge base by default, so what the console lists
afterwards is exactly what was just loaded. Pass `--add` / `"replace": false` to
append instead.

To see what landed:

```bash
python -m scripts.report        # a coverage summary in the terminal
python -m scripts.db_browser    # the raw tables, in a browser
```

`db_browser` opens a read-only viewer on <http://127.0.0.1:8010> listing every
table with its row count. Click through to page the rows — useful for pointing
at `specifications` and showing that unpublished facts really are stored as
`NULL`.

---

## Using the chat interface

Open <http://127.0.0.1:8000>. Four starter questions are on the welcome screen;
otherwise just type. `Enter` sends, `Shift+Enter` makes a new line.

While the agents work you see each one by name with a plain sentence describing
what it is doing. When the answer arrives the panel collapses to
`✓ 4 agents · 6273 ms` — click it to reopen the steps.

Under each answer:

| Chip | Meaning |
|---|---|
| `compare` | The intent that ran |
| `ATLAS → SPECTRA → VERSUS → SENTINEL` | The pipeline |
| `Samsung Galaxy S25 Ultra` | Devices resolved from the database |
| `17/17 figures verified against PostgreSQL` | SENTINEL's audit |
| `not in database: …` | A phone you named that is not loaded |

Ask about a phone that is not loaded and the system says so rather than
inventing an answer. That is the point.

---

## API documentation

| Where | What |
|---|---|
| [/docs-ui](http://127.0.0.1:8000/docs-ui) | Human-readable reference — the **API docs** button in the header |
| [/docs](http://127.0.0.1:8000/docs) | Interactive OpenAPI explorer |
| [`docs/API.md`](docs/API.md) | The same reference, in the repo |
| [`docs/postman_collection.json`](docs/postman_collection.json) | Importable collection |

---

## Testing the API with Postman

Full guide: **[`docs/POSTMAN.md`](docs/POSTMAN.md)**. The short version:

1. **Import** `docs/postman_collection.json` — every endpoint arrives pre-filled.
2. Or build one by hand: **New → HTTP**, method **POST**, URL
   `http://127.0.0.1:8000/api/ask`, header
   `Content-Type: application/json`, **Body → raw → JSON**:

```json
{ "question": "How does the Galaxy S25 Ultra compare to the S24 Ultra?" }
```

3. **Send**. In the response look at `agents_used` for the multi-agent flow,
   `grounding.verdict` for the audit, and expand `trace` to show the actual SQL
   each agent ran — that is the proof the answer came from PostgreSQL.

Use `127.0.0.1`, not `localhost`: on some Windows setups `localhost` resolves to
IPv6 first and the connection is refused.

---

## Running the tests

```bash
python -m pytest                  # all 92
python -m pytest -m parsing       # scraper; no services needed
python -m pytest -m database      # schema, NULL policy, retrieval
python -m pytest -m agents        # agent behaviour
python -m pytest -m api           # HTTP + WebSocket
python -m pytest -m scraper       # fallback and refresh pipeline
python -m pytest -m "not llm"     # skip anything needing Ollama
```

Tests skip cleanly when PostgreSQL, Ollama or the saved pages are absent, and
none of them destroy the knowledge base you just loaded.

Beyond happy paths, they assert that: absent facts are NULL and never a
placeholder; every numeric attribute is physically plausible; `"S25"` resolves to
the base model and not the Ultra; an unknown model is declared rather than
answered; `rank_by` rejects any column outside the whitelist; SENTINEL catches
invented figures including unit-glued ones like `240W`; a blocked origin is
contacted exactly once; and every trace event is JSON-serialisable.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `STARTUP FAILED: cannot reach PostgreSQL` | Server down, or wrong credentials | Start the service; check `PG_USER` / `PG_PASSWORD` in `.env` |
| `LLM unavailable` warning at startup | Ollama not running | `ollama serve`, then `ollama pull llama3.2:3b`. The app still starts and returns database rows instead of prose |
| Answers say a phone "is not in this database" | It genuinely is not loaded | Check the header chip; refresh with a bigger `--limit` |
| Refresh shows *"GSMArena denied access"* | Rate-limited by IP | Nothing to do — it finished from `data/pages/`. Same data |
| Refresh returns **409** | One is already running | Wait, or poll `/api/knowledge/refresh/status` |
| First question is slow | The model is loading into VRAM | Normal. Later questions are fast |
| Postman: `ECONNREFUSED` | App not running, or `localhost` resolved to IPv6 | Start `python app.py`; use `127.0.0.1` |
| Chat shows no agent steps | WebSocket blocked | Check the browser console; the header chip shows the connection state |
| `port already in use` | Another instance is running | `python app.py --port 8001` |

Check every dependency at once:

```bash
python app.py --check
curl http://127.0.0.1:8000/api/health
```

---

## Known limitations

- **Vector search is a full scan.** Fine at a few hundred chunks (~200 ms); would
  need pgvector with HNSW past roughly 10⁵.
- **SENTINEL audits numbers, not prose.** A wrong chipset *name* would pass; a
  wrong chipset *score* would not.
- **Small-model prose.** `llama3.2:3b` was chosen to fit 4 GB of VRAM. The facts
  are grounded and checked, but the writing is plainer than a larger model's.
- **Prices are GSMArena's snapshot** at scrape time, in whatever currencies that
  page listed. Missing currencies are NULL, not converted.
