# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: configuration loaded from environment variables.
"""Central configuration.

Every setting can be overridden with an environment variable or a `.env`
file in the project root, so the same code runs against Ollama, any
OpenAI-compatible server, or the offline mock model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_DIR = PROJECT_ROOT / "logs"
SPECS_DIR = PROJECT_ROOT / "specs"


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without overriding real env vars."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(PROJECT_ROOT / ".env")


def _int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to a default."""
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    """Read a float environment variable, falling back to a default."""
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    """All runtime settings for the LLM, the pipeline and the UI."""

    # "ollama" (native API), "openai" (any OpenAI-compatible server, incl. Gemini) or "mock".
    provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "ollama").lower())
    model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "qwen2.5-coder:7b"))
    base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", ""))
    api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY")
                         or os.environ.get("GEMINI_API_KEY") or "not-needed")
    temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.2))
    num_ctx: int = field(default_factory=lambda: _int("LLM_NUM_CTX", 16384))
    request_timeout: int = field(default_factory=lambda: _int("LLM_TIMEOUT", 900))
    # Sent as `reasoning_effort` to OpenAI-compatible servers when set (e.g. "low" for Gemini).
    reasoning_effort: str = field(default_factory=lambda: os.environ.get("LLM_REASONING_EFFORT", ""))
    # Retries for rate limits (HTTP 429) and temporary server errors.
    max_retries: int = field(default_factory=lambda: _int("LLM_MAX_RETRIES", 4))

    # Pipeline limits that bound token usage.
    max_fix_rounds: int = field(default_factory=lambda: _int("MAX_FIX_ROUNDS", 2))
    test_timeout: int = field(default_factory=lambda: _int("TEST_TIMEOUT", 120))
    max_report_chars: int = field(default_factory=lambda: _int("MAX_REPORT_CHARS", 3500))

    # Header stamped on every generated Python file (course requirement).
    author_name: str = field(default_factory=lambda: os.environ.get("AUTHOR_NAME", "<YOUR NAME>"))
    student_id: str = field(default_factory=lambda: os.environ.get("STUDENT_ID", "<YOUR STUDENT ID>"))

    def resolved_base_url(self) -> str:
        """Return the base URL, choosing a sensible default per provider."""
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.provider == "openai":
            return "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
        return "http://localhost:11434"

    def public_dict(self) -> dict:
        """Settings that are safe to show in the UI (no API key)."""
        return {
            "provider": self.provider,
            "model": self.model if self.provider != "mock" else "mock (offline test double)",
            "base_url": self.resolved_base_url() if self.provider != "mock" else "-",
            "max_fix_rounds": self.max_fix_rounds,
        }


def get_settings() -> Settings:
    """Build a fresh Settings object from the current environment."""
    return Settings()
