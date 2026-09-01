"""Central configuration. Every tunable lives here and is sourced from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

def _project_root() -> Path:
    """Walk up from this file until the directory holding app.py is found.

    Anchoring on a marker rather than a fixed number of `.parent` hops means
    moving this module between packages cannot silently repoint data/ and logs/.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "app.py").exists():
            return candidate
    return here.parents[2]


ROOT = _project_root()
load_dotenv(ROOT / ".env")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PostgresSettings:
    host: str = os.getenv("PG_HOST", "localhost")
    port: int = _int("PG_PORT", 5432)
    user: str = os.getenv("PG_USER", "postgres")
    password: str = os.getenv("PG_PASSWORD", "postgres")
    database: str = os.getenv("PG_DATABASE", "samsung_kb")

    @property
    def dsn(self) -> str:
        return (
            f"host={self.host} port={self.port} user={self.user} "
            f"password={self.password} dbname={self.database}"
        )

    @property
    def maintenance_dsn(self) -> str:
        """Connection to the `postgres` catalog DB, used to CREATE DATABASE."""
        return (
            f"host={self.host} port={self.port} user={self.user} "
            f"password={self.password} dbname=postgres"
        )

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class ScraperSettings:
    target_count: int = _int("SCRAPE_TARGET_COUNT", 30)
    # Phones loaded by the one-click refresh in the console.
    demo_count: int = _int("SCRAPE_DEMO_COUNT", 10)
    delay_seconds: float = _float("SCRAPE_DELAY_SECONDS", 2.5)
    impersonate: str = os.getenv("SCRAPE_IMPERSONATE", "chrome")
    timeout: int = _int("SCRAPE_TIMEOUT", 40)
    max_retries: int = _int("SCRAPE_MAX_RETRIES", 3)
    base_url: str = "https://www.gsmarena.com"
    # `-f-9-0-r1-` == brand 9 (Samsung), r1 == order by popularity
    popularity_url: str = "https://www.gsmarena.com/samsung-phones-f-9-0-r1-p{page}.php"
    popularity_pages: int = 3


@dataclass(frozen=True)
class EmbeddingSettings:
    model_name: str = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    dim: int = _int("EMBED_DIM", 384)
    batch_size: int = _int("EMBED_BATCH", 32)
    device: str = os.getenv("EMBED_DEVICE", "cpu")


@dataclass(frozen=True)
class LLMSettings:
    host: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    model: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    temperature: float = _float("OLLAMA_TEMPERATURE", 0.2)
    num_ctx: int = _int("OLLAMA_NUM_CTX", 4096)
    timeout: int = _int("OLLAMA_TIMEOUT", 180)


@dataclass(frozen=True)
class RagSettings:
    top_k: int = _int("RAG_TOP_K", 8)
    min_score: float = _float("RAG_MIN_SCORE", 0.15)


@dataclass(frozen=True)
class ApiSettings:
    host: str = os.getenv("API_HOST", "127.0.0.1")
    port: int = _int("API_PORT", 8000)


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    data: Path = ROOT / "data"
    pages: Path = ROOT / "data" / "pages"
    logs: Path = ROOT / "logs"
    catalog: Path = ROOT / "data" / "catalog.json"

    def ensure(self) -> None:
        for p in (self.data, self.pages, self.logs):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    pg: PostgresSettings = field(default_factory=PostgresSettings)
    scraper: ScraperSettings = field(default_factory=ScraperSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    rag: RagSettings = field(default_factory=RagSettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    paths: Paths = field(default_factory=Paths)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
settings.paths.ensure()
