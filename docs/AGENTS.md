# The Agents

Eight named agents. Each one is a single file in [`agents/`](../agents/), and every
system prompt lives in [`agents/prompts.py`](../agents/prompts.py) — never buried
inside the code.

```bash
python -m agents.prompts            # print every prompt
python -m agents.prompts VERSUS     # print one
curl localhost:8000/api/agents      # the roster the console renders
```

---

## At a glance

| # | Agent | Role | File | Reads DB | Uses LLM | Prompt |
|---|-------|------|------|:--------:|:--------:|--------|
| 1 | **NEXUS** | Orchestrator | [`agents/nexus.py`](../agents/nexus.py) | – | yes | `NEXUS_SYNTHESIS` |
| 2 | **ATLAS** | Query Analyst | [`agents/atlas.py`](../agents/atlas.py) | yes | fallback only | `ATLAS_CLASSIFY` |
| 3 | **SPECTRA** | Specification Retrieval | [`agents/spectra.py`](../agents/spectra.py) | yes | – | none |
| 4 | **ORACLE** | Semantic Retrieval | [`agents/oracle.py`](../agents/oracle.py) | yes | – | none |
| 5 | **RANKER** | Comparative Analytics | [`agents/ranker.py`](../agents/ranker.py) | yes | – | none |
| 6 | **VERSUS** | Comparison Analyst | [`agents/versus.py`](../agents/versus.py) | yes | yes | `VERSUS_COMPARE` |
| 7 | **CRITIC** | Review Writer | [`agents/critic.py`](../agents/critic.py) | yes | yes | `CRITIC_REVIEW` |
| 8 | **SENTINEL** | Grounding Auditor | [`agents/sentinel.py`](../agents/sentinel.py) | – | – | none |

Four agents have **no prompt at all**. SPECTRA, ORACLE, RANKER and SENTINEL are
deterministic — they read the database and return rows. That is deliberate: the
facts entering any prompt are reproducible, and the audit that checks the answer
cannot itself hallucinate.

---

## How they talk to each other

Agents never call one another's methods. They exchange `Envelope` objects —
the **Agent Communication Protocol (ACP/1.0)** — routed by NEXUS.

```
                        ┌──────────────────────────────┐
   User question ─────► │  NEXUS      the orchestrator │
                        └──────────────┬───────────────┘
                                       │ ACP/1.0 envelope
                                       ▼
                        ┌──────────────────────────────┐
                        │  ATLAS      what is asked?    │──► SELECT phones
                        └──────────────┬───────────────┘    (resolve names)
                                       │ intent + phone_ids
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
      ┌───────────────┐       ┌───────────────┐        ┌───────────────┐
      │   SPECTRA     │       │    ORACLE     │        │    RANKER     │
      │ spec sheets   │       │ vector search │        │ league tables │
      └───────┬───────┘       └───────┬───────┘        └───────┬───────┘
              │  PG-WIRE/3.0          │  VEC-SQL/1.0           │  PG-WIRE/3.0
              ▼                       ▼                        ▼
      ╔═══════════════════════════════════════════════════════════════╗
      ║                    PostgreSQL  samsung_kb                     ║
      ╚═══════════════════════════════════════════════════════════════╝
              │                                                │
              │ retrieved rows                                 │
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

The envelope is defined in [`agents/base.py`](../agents/base.py):

```python
Envelope(
    sender="SPECTRA",            # who is speaking
    recipient="VERSUS",          # who should act
    intent="compare.devices",    # what is being asked
    payload={...},               # the data itself
    correlation_id="a1b2c3d4",   # which message this replies to
)
```

Every hand-off is published on the trace bus, so the flow the console draws is
the flow that actually happened — not a fixed script.

---

## 1. NEXUS — Orchestrator

**File** [`agents/nexus.py`](../agents/nexus.py) · **Prompt** `NEXUS_SYNTHESIS`

| | |
|---|---|
| **Responsibility** | Owns the run. Picks the pipeline, routes every message, writes the answer for intents with no dedicated writer, and always finishes with a SENTINEL audit. |
| **Input** | The user's question. |
| **Output** | `{answer, intent, agents_used, pipeline, devices, unresolved, grounding, extras, latency_ms, run_id}` |
| **Talks to** | Every other agent. It is the only agent that does. |
| **Database** | None directly — it delegates. |

**Routing table** (`NexusAgent._plan`):

| Intent | Pipeline |
|---|---|
| `spec_lookup` | ATLAS → SPECTRA → ORACLE → NEXUS → SENTINEL |
| `compare` | ATLAS → SPECTRA → VERSUS → SENTINEL |
| `ranking` | ATLAS → RANKER → NEXUS → SENTINEL |
| `review` | ATLAS → SPECTRA → CRITIC → SENTINEL |
| `general` | ATLAS → ORACLE → NEXUS → SENTINEL |

If ATLAS resolves no device, a `compare`/`review`/`spec_lookup` run cannot
proceed, so NEXUS downgrades it to `general` and reports the intent that *ran*.

---

## 2. ATLAS — Query Analyst

**File** [`agents/atlas.py`](../agents/atlas.py) · **Prompt** `ATLAS_CLASSIFY` (fallback only)

| | |
|---|---|
| **Responsibility** | Work out what is being asked and which devices are meant. |
| **Input** | `{"question": str}` |
| **Output** | `{intent, phones, unresolved_mentions, metric, direction, classified_by}` |
| **Talks to** | Replies to NEXUS. |
| **Database** | `repository.resolve_phones()` → `SELECT … FROM phones` |

Classification is **rule-first**: regex signals (`COMPARE_RE`, `RANK_RE`,
`REVIEW_RE`) decide instantly and deterministically. The LLM is consulted only
when no device is named and no comparative cue is present — the open-ended tail.
`classified_by` in the response tells you which path was taken.

Name resolution is the important part. `repository.resolve_phone()` normalises a
mention to a comparison key (`"Galaxy S25 Ultra"` → `s25ultra`) and prefers an
exact match, which is what stops `"S25"` being swallowed by `"S25 Ultra"`. A
mention that matches nothing is returned in `unresolved_mentions` — it is never
guessed at.

---

## 3. SPECTRA — Specification Retrieval

**File** [`agents/spectra.py`](../agents/spectra.py) · **No prompt** (deterministic)

| | |
|---|---|
| **Responsibility** | The only agent that reads full device records. Fetches specs and hands them to whichever analyst asked. |
| **Input** | `{"phone_ids": [int], "focus": str | None}` |
| **Output** | `{sheets, rendered, focus, null_fields}` |
| **Talks to** | Receives from NEXUS; its output goes to VERSUS, CRITIC or back to NEXUS. |
| **Database** | `repository.spec_sheet()` → `SELECT p.*, a.* FROM phones p LEFT JOIN phone_attributes a` and `SELECT … FROM specifications` |

This is the "one agent fetches, another reasons" split the brief asks for.
SPECTRA writes no prose and never calls the LLM.

`focus` narrows the pull to the categories a question needs — `camera`,
`battery`, `display`, `performance`, `design`, `connectivity`, `pricing` — so a
question about cameras does not drag the whole sheet along.

`render()` marks absences explicitly:

```
- Screen protection: NOT PUBLISHED (NULL in database)
  [Display] NOT PUBLISHED (NULL in database): Protection
```

Booleans are spelled out as `Yes`/`No`. A bare `False` in a prompt reads as a
value rather than a negation, and the answer comes back inverted.

---

## 4. ORACLE — Semantic Retrieval

**File** [`agents/oracle.py`](../agents/oracle.py) · **No prompt** (deterministic)

| | |
|---|---|
| **Responsibility** | The RAG pass. Embeds the question and searches the chunked knowledge base. |
| **Input** | `{"query": str, "phone_ids": [int] | None, "top_k": int}` |
| **Output** | `{context, citations, hit_count}` |
| **Talks to** | Receives from NEXUS or SPECTRA; replies to the sender. |
| **Database** | `rag/retriever.search()` → hybrid SQL over `knowledge_chunks` |

Retrieval is **hybrid and runs inside PostgreSQL**:

```sql
SELECT k.content,
       cosine_similarity(k.embedding, %(qvec)s::real[]) AS dense_score,
       similarity(k.content, %(qtext)s)                 AS lexical_score
FROM knowledge_chunks k
ORDER BY 0.75 * dense_score + 0.25 * lexical_score DESC
```

The lexical pass rescues exact model numbers, which a 384-dimension embedding
blurs together — `"S24"` and `"S25"` are near-identical vectors.

---

## 5. RANKER — Comparative Analytics

**File** [`agents/ranker.py`](../agents/ranker.py) · **No prompt** (deterministic)

| | |
|---|---|
| **Responsibility** | Superlatives and league tables. |
| **Input** | `{"metric": str, "direction": "asc"|"desc", "limit": int}` |
| **Output** | `{ranking: {column, label, unit, rows, excluded_null_count}, rendered}` |
| **Talks to** | Receives from NEXUS; result feeds NEXUS's synthesis. |
| **Database** | `repository.rank_by()` → `ORDER BY <col> … LIMIT` plus a `COUNT(*) WHERE <col> IS NULL` |

"Which phone has the best battery life?" is an **aggregation**, not a similarity
search — embeddings return every battery chunk with near-identical scores.
RANKER answers it with `ORDER BY`, which is correct and explainable.

`repository.RANKABLE` is a whitelist of 18 numeric columns. Anything else raises
`ValueError`, so a user query can never reach an arbitrary column.

NULL rows are excluded **and counted**, and the count is reported to the user.

---

## 6. VERSUS — Comparison Analyst

**File** [`agents/versus.py`](../agents/versus.py) · **Prompt** `VERSUS_COMPARE`

| | |
|---|---|
| **Responsibility** | Build the side-by-side matrix, decide every numeric comparison in Python, then narrate. |
| **Input** | `{"phone_ids": [int], "question": str, "rendered": [str]}` |
| **Output** | `{answer, matrix, deltas, table}` |
| **Talks to** | Receives from SPECTRA. |
| **Database** | `repository.compare_matrix()` → one `SELECT` across both devices |

**Arithmetic never goes through the model.** `_deltas()` computes every
difference and decides which device leads for all 17 tracked metrics. A wrong
number in a spec comparison is the most damaging error this system could make.

The prompt is handed the two values and which side wins — but **not the gap
between them**. Shown both, a 3B model reliably quotes the gap as if it were one
device's value ("a 150 MP sensor" when the figures were 50 MP and 200 MP). The
gap is still computed and returned in `extras.deltas` for API callers.

Metrics where either side is NULL are reported as "not comparable", never
silently dropped.

---

## 7. CRITIC — Review Writer

**File** [`agents/critic.py`](../agents/critic.py) · **Prompt** `CRITIC_REVIEW`

| | |
|---|---|
| **Responsibility** | Turn a spec sheet into a review with an actual opinion. |
| **Input** | `{"sheets": [...], "rendered": [str], "question": str}` |
| **Output** | `{answer, phone, standings, gaps}` |
| **Talks to** | Receives from SPECTRA. |
| **Database** | Per-metric percentile queries: `count(*) FILTER (WHERE col > %s)` over `phone_attributes` |

Judgement is still bounded by the database. CRITIC positions the device against
**the corpus**, computed in SQL — "ranks 3 of 10 on battery capacity; catalogue
average 4,900 mAh" — not against outside knowledge of the market.

`_gaps()` computes the list of genuinely-NULL fields in Python and hands it over
as authoritative. Left to notice absences itself across a long sheet, the model
claimed the Z Fold8 had no IP rating when the database held `IP48`. **Never make
a model infer a negative.**

---

## 8. SENTINEL — Grounding Auditor

**File** [`agents/sentinel.py`](../agents/sentinel.py) · **No prompt** (deterministic)

| | |
|---|---|
| **Responsibility** | Check every figure in the drafted answer against the evidence actually retrieved. |
| **Input** | `{"answer": str, "evidence": str}` |
| **Output** | `{verdict, numeric_claims, supported, unsupported, support_ratio}` |
| **Talks to** | Receives from NEXUS. Reports; never rewrites. |
| **Database** | None — it audits what the other agents already pulled. |

This is the **mechanical enforcement** of "the database is the only knowledge
base". Everything else is policy; this is a check. A figure produced from the
model's own weights has no matching source row and is flagged.

| Verdict | Meaning |
|---|---|
| `grounded` | Every number traced to a retrieved row. |
| `partially-grounded` | ≥ 80 % traced. |
| `weakly-grounded` | < 80 % traced. |

The extractor ignores years, list markers and identifiers like `SM-S918B`, and
**does** catch unit-glued figures such as `240W` and `5000mAh` — which is most
real claims.

Figures an agent computed from database values (VERSUS's deltas, CRITIC's
percentile ranks) count as evidence via `ctx.note("derived_evidence", …)`.

---

## Answering "how does agent N work?"

1. Open its file in [`agents/`](../agents/) — one agent per file, no exceptions.
2. Its `AgentCard` at the top states name, role, capabilities and protocols.
3. `activity()` is the sentence the chat shows while it works.
4. `handle()` is the whole behaviour — input envelope in, output envelope out.
5. Its prompt, if it has one, is in [`agents/prompts.py`](../agents/prompts.py).
6. Its database access goes through [`database/repository.py`](../database/repository.py),
   which is the complete set of reads any agent may perform.

Then run the thing and watch it:

```bash
curl -s -X POST localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"How does the Galaxy S25 Ultra compare to the S24 Ultra?"}' \
  | python -m json.tool | less
```

`trace` holds every message and every SQL statement, in order.
