# Author: Yuxiao Xiao (Student ID: 62185048)
# Written with AI assistance (Claude Code); see README section 9.
# SWE 270P Final Project - AI Coder: shared base class for the five LLM agents.
"""Base class for the LLM agents.

An agent owns one LLM client, keeps the usage records of the calls it makes
while serving one MCP tool call, and may open an MCP session to the workspace
server limited to its own allow-list of tools (`tools`).

Subclasses implement their skill as a plain async method (easy to unit test
without MCP) and expose it as an MCP tool in `register`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from ..config import Settings, get_settings
from ..llm_client import BaseClient, LLMError, make_client
from ..mcp_client import WorkspaceClient
from ..parsing import compact_json, extract_json

JSON_RETRY_HINT = "\n\nReply with valid JSON only."


class AgentError(Exception):
    """The agent could not produce a usable result (reported to the orchestrator, not raised)."""


class BaseAgent:
    """Common machinery for every agent role."""

    role: str = ""
    # Workspace MCP tools this role may call; enforced by WorkspaceClient.
    tools: frozenset[str] = frozenset()

    def __init__(self, settings: Settings | None = None, client: BaseClient | None = None) -> None:
        """Create the agent with its own LLM client (injectable for tests)."""
        self.settings = settings or get_settings()
        self.client = client or make_client(self.settings)
        self.usage: list[dict] = []

    async def ask(self, system: str, user: str, max_tokens: int = 4096, json_mode: bool = False) -> str:
        """Make one LLM call in a worker thread and record its token usage."""
        text, record = await asyncio.to_thread(
            self.client.chat, self.role, system, user, max_tokens, json_mode)
        self.usage.append(record.to_dict())
        return text

    async def ask_json(self, system: str, user: str, max_tokens: int) -> Any:
        """Ask for JSON; if the reply cannot be parsed, ask once more. Raises ValueError."""
        try:
            return extract_json(await self.ask(system, user, max_tokens, json_mode=True))
        except ValueError:
            return extract_json(await self.ask(system, user + JSON_RETRY_HINT, max_tokens, json_mode=True))

    def workspace(self, root: str) -> WorkspaceClient:
        """Open an MCP client to the workspace server limited to this role's tools."""
        return WorkspaceClient(root, set(self.tools))

    def take_usage(self) -> list[dict]:
        """Return and clear the usage records collected for the current tool call."""
        records, self.usage = self.usage, []
        return records

    async def serve(self, work: Callable[..., Awaitable[dict]], *args: Any) -> dict:
        """Run one skill and wrap its result for MCP.

        The reply is always a dict with `ok`, the usage records of every LLM
        call made, and either the skill's fields or an `error` message. Model
        server failures and AgentError become `ok: False` instead of an MCP
        error, so the orchestrator can still count the tokens spent."""
        try:
            result = await work(*args)
        except (LLMError, AgentError) as exc:
            return {"ok": False, "error": f"{self.role}: {exc}", "usage": self.take_usage()}
        return {"ok": True, **result, "usage": self.take_usage()}

    def register(self, mcp: FastMCP) -> None:
        """Expose this agent's skill as an MCP tool on `mcp`."""
        raise NotImplementedError


def spec_brief(spec: dict) -> str:
    """Compact JSON of the spec fields that later agents need (saves prompt tokens)."""
    return compact_json({k: spec[k] for k in ("app_name", "summary", "features", "constraints") if k in spec})


def render_files(files: dict[str, str]) -> str:
    """Show files to the model in the same block format it is asked to answer in."""
    return "\n\n".join(f"=== FILE: {path} ===\n{text}\n=== END FILE ===" for path, text in files.items())
