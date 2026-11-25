# Author: Xin Zhou (Student ID: 42742822)
# SWE 270P Final Project - AI Coder: LLM usage tracking (API calls and tokens).
"""Track and report LLM usage.

Each agent server records one `CallRecord` per LLM request and returns the
records with its tool result. The orchestrator merges them into a
`UsageTracker`, which produces the per-agent report shown in the UI and
saved as `usage_report.json`.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def estimate_tokens(text: str) -> int:
    """Rough token estimate (about 4 characters per token) used when the
    provider does not report token counts."""
    return max(1, len(text) // 4) if text else 0


@dataclass
class CallRecord:
    """One LLM API call."""

    agent: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    estimated: bool = False
    timestamp: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        """Prompt plus completion tokens."""
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict:
        """Serialise the record, including the derived total."""
        data = asdict(self)
        data["total_tokens"] = self.total_tokens
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "CallRecord":
        """Rebuild a record from `to_dict` output (extra keys are ignored)."""
        return cls(
            agent=data["agent"],
            model=data.get("model", "unknown"),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            latency_s=float(data.get("latency_s", 0.0)),
            estimated=bool(data.get("estimated", False)),
            timestamp=float(data.get("timestamp", time.time())),
        )


class UsageTracker:
    """Aggregates call records for one generation run."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.calls: list[CallRecord] = []

    def add(self, record: CallRecord) -> None:
        """Add a single call record."""
        self.calls.append(record)

    def extend(self, records: list[dict]) -> None:
        """Add call records received (as dicts) from an agent server."""
        for item in records or []:
            self.calls.append(CallRecord.from_dict(item))

    @property
    def total_calls(self) -> int:
        """Number of LLM API calls."""
        return len(self.calls)

    @property
    def total_tokens(self) -> int:
        """Total tokens across all calls."""
        return sum(c.total_tokens for c in self.calls)

    def by_agent(self) -> dict[str, dict]:
        """Per-agent totals: calls, prompt/completion/total tokens, latency."""
        result: dict[str, dict] = {}
        for c in self.calls:
            row = result.setdefault(
                c.agent,
                {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                 "total_tokens": 0, "latency_s": 0.0},
            )
            row["calls"] += 1
            row["prompt_tokens"] += c.prompt_tokens
            row["completion_tokens"] += c.completion_tokens
            row["total_tokens"] += c.total_tokens
            row["latency_s"] = round(row["latency_s"] + c.latency_s, 2)
        return result

    def report(self) -> dict:
        """Full usage report as a JSON-serialisable dict."""
        return {
            "total_api_calls": self.total_calls,
            "total_prompt_tokens": sum(c.prompt_tokens for c in self.calls),
            "total_completion_tokens": sum(c.completion_tokens for c in self.calls),
            "total_tokens": self.total_tokens,
            "any_estimated": any(c.estimated for c in self.calls),
            "by_agent": self.by_agent(),
            "calls": [c.to_dict() for c in self.calls],
        }

    def save(self, path: Path) -> None:
        """Write the report to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.report(), indent=2), encoding="utf-8")

    def append_history(self, path: Path, job_id: str, extra: dict | None = None) -> None:
        """Append a one-line summary of this run to a JSONL history log."""
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {"job_id": job_id, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "api_calls": self.total_calls, "total_tokens": self.total_tokens}
        line.update(extra or {})
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
