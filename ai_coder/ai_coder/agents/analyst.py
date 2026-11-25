# Author: Yuxiao Xiao (Student ID: 62185048)
# Written with AI assistance (Claude Code); see README section 9.
# SWE 270P Final Project - AI Coder: Requirements Analyst agent.
"""Requirements Analyst: description + requirements -> JSON specification.

Uses no workspace tools. If the model's JSON cannot be used even after one
retry, a simple spec is built from the requirement lines without the LLM, so
the pipeline can continue instead of failing.
"""

from __future__ import annotations

import re

from mcp.server.fastmcp import FastMCP

from .. import prompts
from .base import BaseAgent


class AnalystAgent(BaseAgent):
    """Turns free-text requirements into a testable spec."""

    role = "analyst"
    max_tokens = 2048

    async def analyze(self, description: str, requirements: str) -> dict:
        """Return {"spec": {...}} with app_name, summary, features and acceptance criteria."""
        user = f"DESCRIPTION:\n{description.strip()}\n\nREQUIREMENTS:\n{requirements.strip()}"
        try:
            spec = await self.ask_json(prompts.ANALYST, user, self.max_tokens)
        except ValueError:
            spec = None
        if not is_usable_spec(spec):
            spec = fallback_spec(description, requirements)
        return {"spec": spec}

    def register(self, mcp: FastMCP) -> None:
        """Expose `analyze_requirements` as an MCP tool."""

        @mcp.tool()
        async def analyze_requirements(description: str, requirements: str) -> dict:
            """Turn a software description and requirements into a JSON specification."""
            return await self.serve(self.analyze, description, requirements)


def is_usable_spec(spec: object) -> bool:
    """A spec is usable when it is a dict with at least one feature."""
    return isinstance(spec, dict) and bool(spec.get("features"))


def fallback_spec(description: str, requirements: str, max_features: int = 10) -> dict:
    """Spec built from the text alone: one feature per non-empty requirement line."""
    name_words = re.findall(r"[a-z]+", description.lower())[:3] or ["app"]
    lines = [line.strip(" -*\t") for line in requirements.splitlines()]
    lines = [line for line in lines if line][:max_features]
    return {
        "app_name": "_".join(name_words),
        "summary": description.strip()[:300],
        "features": [{"id": f"F{n}", "name": line[:60], "details": line, "acceptance": [line]}
                     for n, line in enumerate(lines, start=1)],
        "data_entities": [],
        "constraints": [],
    }
