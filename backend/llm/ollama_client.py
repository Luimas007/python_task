"""Local LLM inference via Ollama over loopback HTTP.

Ollama runs llama3.2:3b on the host RTX 3050 (4 GB), which the 3B q4 weights fit
comfortably. The client is deliberately dumb: it takes a prompt that has already
been grounded in database rows and returns text. It has no tools, no retrieval,
and no internet access of its own.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from backend.config.settings import settings
from backend.core.events import RunTrace
from backend.core.logging_setup import get_logger
from backend.core.protocols import OLLAMA

log = get_logger("llm.ollama")


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: float

    def __str__(self) -> str:
        return self.text


class OllamaClient:
    def __init__(self, model: str | None = None) -> None:
        self.host = settings.llm.host.rstrip("/")
        self.model = model or settings.llm.model

    # ------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=5)
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            if self.model not in names:
                return False, f"model {self.model} not pulled (have: {', '.join(names) or 'none'})"
            return True, f"{self.model} ready"
        except Exception as exc:
            return False, f"cannot reach Ollama at {self.host}: {exc}"

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=5)
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        trace: RunTrace | None = None,
        agent: str | None = None,
        purpose: str = "generation",
    ) -> Completion:
        options: dict[str, Any] = {
            "temperature": settings.llm.temperature if temperature is None else temperature,
            "num_ctx": settings.llm.num_ctx,
        }
        if max_tokens:
            options["num_predict"] = max_tokens

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            payload["system"] = system

        if trace is not None:
            trace.protocol(
                "llm.request",
                f"{agent or 'system'} -> {self.model} ({purpose}), "
                f"{len(prompt)} char prompt",
                agent=agent,
                protocol=OLLAMA.id,
                status="pending",
                detail={
                    "endpoint": f"{self.host}/api/generate",
                    "model": self.model,
                    "purpose": purpose,
                    "options": options,
                    "system_preview": (system or "")[:400],
                    "prompt_preview": prompt[:1200],
                    "prompt_chars": len(prompt),
                },
            )

        t0 = time.perf_counter()
        try:
            r = httpx.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=settings.llm.timeout,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            dt = round((time.perf_counter() - t0) * 1000, 2)
            if trace is not None:
                trace.protocol(
                    "llm.error", f"inference failed after {dt} ms: {exc}",
                    agent=agent, protocol=OLLAMA.id, status="error",
                    duration_ms=dt, detail={"error": str(exc)},
                )
            raise LLMUnavailable(str(exc)) from exc

        dt = round((time.perf_counter() - t0) * 1000, 2)
        text = (data.get("response") or "").strip()
        comp = Completion(
            text=text,
            model=self.model,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            duration_ms=dt,
        )
        if trace is not None:
            trace.protocol(
                "llm.response",
                f"{self.model} returned {len(text)} chars in {dt:.0f} ms "
                f"({comp.completion_tokens or '?'} tokens)",
                agent=agent,
                protocol=OLLAMA.id,
                duration_ms=dt,
                detail={
                    "model": self.model,
                    "purpose": purpose,
                    "prompt_tokens": comp.prompt_tokens,
                    "completion_tokens": comp.completion_tokens,
                    "response_preview": text[:1200],
                },
            )
        return comp

    # ------------------------------------------------------------------
    def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        trace: RunTrace | None = None,
        agent: str | None = None,
        purpose: str = "structured",
        fallback: dict | None = None,
    ) -> dict:
        """Ask for JSON. Small models drift, so parsing is forgiving."""
        comp = self.generate(
            prompt, system=system, temperature=0.0, trace=trace,
            agent=agent, purpose=purpose,
        )
        return _loads_loose(comp.text, fallback or {})


def _loads_loose(text: str, fallback: dict) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Salvage the outermost {...} block.
    start, depth = text.find("{"), 0
    if start >= 0:
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    log.debug("could not parse JSON from model output: %r", text[:200])
    return fallback


_CLIENT: OllamaClient | None = None


def client() -> OllamaClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OllamaClient()
    return _CLIENT
