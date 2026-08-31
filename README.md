# Samsung Phone Query and Review System

Scrapes Samsung phone specs from GSMArena into PostgreSQL, indexes them for
RAG, and answers questions / generates reviews via a local LLM (Ollama)
through a lightweight multi-agent pipeline, served as one FastAPI app (API +
chat UI) on a single port.

## Architecture

```
config/       settings (env-driven)
database/     SQLAlchemy models, session, PhoneService (data-access layer)
scraper/      GSMArena scraper - full Samsung catalog, paginated (BeautifulSoup)
rag/          Chroma vector store (sentence-transformers embeddings)
agents/       SpecAgent (DB/vector retrieval), ReviewAgent (LLM review/compare),
              Orchestrator (intent routing + follow-up resolution)
chatbot/      Ollama LLM client
api/          FastAPI app - JSON routes under /api, serves frontend/static/ at /
frontend/     single-page HTML/JS chat + phone browser (no build step)
utils/        rotating file + console logger
scripts/      run_scraper.py (the only standalone script - see below)
data/         seed_data.json (18 pre-scraped phones: Galaxy S21-S26),
              chroma_store/ (generated)
app.py        single entry point - preflight checks, then runs the app
```

Multi-agent design is deliberately plain Python classes, not a framework
(CrewAI/LangChain/AutoGen): `SpecAgent` fetches specs, `ReviewAgent` calls
the LLM to write reviews/comparisons, `Orchestrator` routes each query to
one of them and remembers the last phone(s) discussed so follow-up
questions work without repeating the model name.

## Prerequisites

- Miniconda
- PostgreSQL server running locally (or reachable)
- [Ollama](https://ollama.com) installed (separate app, not a pip package)

## Setup (Anaconda Prompt)

```
conda create -n samsung_phone_system python=3.10 -y
conda activate samsung_phone_system
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` with your PostgreSQL credentials if they differ from the
defaults.

Create the database once (skip if it already exists):

```
psql -U postgres -c "CREATE DATABASE samsung_phones;"
```

Pull the local LLM:

```
ollama pull llama3.2:3b
ollama serve
```

## Run

```
python app.py
```

This checks the database (creates tables + loads seed data if empty), the
vector index (builds it if empty), and whether Ollama is reachable — then
starts the app at http://localhost:8000. Open that URL for the chat UI.
API docs: http://localhost:8000/docs.

## Follow-up questions

The assistant remembers the last phone(s) it discussed in the running
session. "What's the S26 Ultra's camera?" → "What about its battery?" works
without repeating the model name.

## Re-scraping (optional)

The bundled `data/seed_data.json` covers the Galaxy S21-S26 lineup so the
app works out of the box. To scrape GSMArena's full Samsung catalog (all
series, not just Galaxy S) and refresh it:

```
python -m scripts.run_scraper
```

Use `--max-phones N` to cap a run (GSMArena rate-limits/blocks bots, so a
full catalog scrape is slow and not guaranteed to complete). Re-run
`python app.py` afterward — it will reload the new `seed_data.json` into an
empty database, or delete the phones table first if you want a full refresh.

## API Endpoints

| Method | Path                       | Description                     |
|--------|-----------------------------|----------------------------------|
| GET    | /api/health                 | Health check                     |
| GET    | /api/phones                 | List all phones                  |
| GET    | /api/phones/{id}            | Get one phone + specs            |
| POST   | /api/phones/{id}/review     | Generate + persist a review      |
| POST   | /api/chat                   | Ask a question (spec/review/compare, routed automatically, follow-up aware) |

## Query routing

`Orchestrator.handle_query` classifies intent by keyword:

- **spec_lookup** (default) — RAG retrieval over phone specs → LLM answer
- **review** ("review", "should I buy", "recommend") → `ReviewAgent.generate_review`
- **compare** ("compare", "vs", "better than") → `ReviewAgent.compare` on the top-2 retrieved phones

## Notes on performance

Response time is dominated by local LLM inference (Ollama). Generation is
capped (`num_predict`) to keep answers fast; swap `OLLAMA_MODEL` in `.env`
for a smaller/faster model if needed, or a larger one if you have more
VRAM/RAM available.
