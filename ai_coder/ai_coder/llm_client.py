# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: LLM client for open-source models.
"""LLM clients.

* `OllamaClient`  - Ollama's native /api/chat (lets us set num_ctx).
* `OpenAICompatClient` - any OpenAI-compatible /chat/completions server
  (Ollama /v1, LM Studio, vLLM, llama.cpp server, Groq free tier...).
* `MockClient` - offline test double returning canned answers, used only to
  test the pipeline without a model.

Every client returns `(text, CallRecord)` so usage is always tracked.
"""

from __future__ import annotations

import time

import httpx

from .config import Settings
from .usage import CallRecord, estimate_tokens


class LLMError(RuntimeError):
    """Raised when the model server cannot be reached or returns an error."""


class BaseClient:
    """Common interface for all LLM clients."""

    def __init__(self, settings: Settings) -> None:
        """Keep the settings used for every request."""
        self.settings = settings

    def chat(self, agent: str, system: str, user: str, max_tokens: int = 4096,
             json_mode: bool = False) -> tuple[str, CallRecord]:
        """Send one system+user exchange and return (reply_text, usage_record).

        `json_mode` asks the server to constrain output to valid JSON where
        supported (Ollama's `format: json`); other servers ignore it."""
        raise NotImplementedError

    def _record(self, agent: str, prompt: str, reply: str, started: float,
                prompt_tokens: int | None, completion_tokens: int | None) -> CallRecord:
        """Build a usage record, estimating token counts if the server omitted them."""
        estimated = prompt_tokens is None or completion_tokens is None
        return CallRecord(
            agent=agent,
            model=self.settings.model,
            prompt_tokens=prompt_tokens if prompt_tokens is not None else estimate_tokens(prompt),
            completion_tokens=completion_tokens if completion_tokens is not None else estimate_tokens(reply),
            latency_s=round(time.time() - started, 2),
            estimated=estimated,
        )


class OllamaClient(BaseClient):
    """Client for Ollama's native chat API."""

    def chat(self, agent, system, user, max_tokens=4096, json_mode=False):
        """Call POST /api/chat with streaming off and return text plus usage."""
        url = f"{self.settings.resolved_base_url()}/api/chat"
        payload = {
            "model": self.settings.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {
                "temperature": self.settings.temperature,
                "num_ctx": self.settings.num_ctx,
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"
        started = time.time()
        try:
            resp = httpx.post(url, json=payload, timeout=self.settings.request_timeout)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request to {url} failed: {exc}") from exc
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        record = self._record(agent, system + user, text, started,
                              data.get("prompt_eval_count"), data.get("eval_count"))
        return text, record


class OpenAICompatClient(BaseClient):
    """Client for OpenAI-compatible chat completion endpoints.

    Also used for hosted APIs with an OpenAI-compatible endpoint, such as
    Google Gemini (base URL https://generativelanguage.googleapis.com/v1beta/openai).
    """

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def chat(self, agent, system, user, max_tokens=4096, json_mode=False):
        """Call POST /chat/completions and return text plus usage.

        Rate-limit (429) and temporary server errors are retried with
        back-off, which free tiers need. Reasoning ("thinking") tokens that
        some models report only inside `total_tokens` are counted as
        completion tokens so the usage report is complete.
        """
        url = f"{self.settings.resolved_base_url()}/chat/completions"
        payload = {
            "model": self.settings.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.settings.temperature,
            "max_tokens": max_tokens,
        }
        if self.settings.reasoning_effort:
            payload["reasoning_effort"] = self.settings.reasoning_effort
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        started = time.time()
        resp = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                resp = httpx.post(url, json=payload, headers=headers, timeout=self.settings.request_timeout)
            except httpx.HTTPError as exc:
                if attempt == self.settings.max_retries:
                    raise LLMError(f"Request to {url} failed: {exc}") from exc
                time.sleep(2 ** attempt * 2)
                continue
            if resp.status_code in self.RETRY_STATUS and attempt < self.settings.max_retries:
                wait = resp.headers.get("retry-after")
                time.sleep(float(wait) if wait and wait.replace(".", "", 1).isdigit() else 2 ** attempt * 5)
                continue
            break
        if resp.status_code >= 400:
            raise LLMError(f"Request to {url} failed: HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        if not text and choice.get("finish_reason") == "length":
            raise LLMError("The model used its whole output budget (often on reasoning) and returned no text; "
                           "set LLM_REASONING_EFFORT=low or use a model without thinking.")
        usage = data.get("usage") or {}
        prompt_t, completion_t = usage.get("prompt_tokens"), usage.get("completion_tokens")
        total_t = usage.get("total_tokens")
        if prompt_t is not None and total_t is not None:
            completion_t = max(completion_t or 0, total_t - prompt_t)
        record = self._record(agent, system + user, text, started, prompt_t, completion_t)
        return text, record


class MockClient(BaseClient):
    """Offline test double: returns canned answers per agent role.

    Token counts are estimated from text length and flagged as estimated.
    This exists so the pipeline and its tests can run without a model; the
    real demo should use Ollama or another open-source model.
    """

    def chat(self, agent, system, user, max_tokens=4096, json_mode=False):
        """Return the canned reply for `agent`."""
        from . import mock_responses  # local import: large module, only needed offline

        started = time.time()
        text = mock_responses.reply_for(agent, user)
        return text, self._record(agent, system + user, text, started, None, None)


def make_client(settings: Settings) -> BaseClient:
    """Factory that picks the client class from `settings.provider`."""
    if settings.provider == "mock":
        return MockClient(settings)
    if settings.provider == "openai":
        return OpenAICompatClient(settings)
    return OllamaClient(settings)
