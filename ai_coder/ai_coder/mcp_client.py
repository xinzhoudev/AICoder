# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: shared MCP client helpers.
"""Helpers for opening MCP stdio sessions and calling tools.

Used by the orchestrator (to reach every agent and the workspace) and by
the agents themselves (to reach the workspace with a restricted tool set).
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import PROJECT_ROOT


class ToolCallError(RuntimeError):
    """Raised when an MCP tool call fails or returns an error payload."""


def server_params(module: str, *args: str) -> StdioServerParameters:
    """Parameters to launch `python -m <module> <args>` as an MCP stdio server.

    The full environment is passed on explicitly because the MCP SDK only
    forwards a few variables by default, and agents need the LLM_* settings.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return StdioServerParameters(command=sys.executable, args=["-m", module, *args],
                                 env=env, cwd=str(PROJECT_ROOT))


@asynccontextmanager
async def open_session(module: str, *args: str):
    """Async context manager yielding an initialised `ClientSession`."""
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(server_params(module, *args)))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        yield session


async def call_json(session: ClientSession, tool: str, arguments: dict | None = None) -> dict:
    """Call an MCP tool and decode its JSON result into a dict."""
    result = await session.call_tool(tool, arguments or {})
    text = "".join(getattr(part, "text", "") for part in result.content)
    if result.isError:
        raise ToolCallError(f"MCP tool '{tool}' failed: {text[:2000]}")
    if getattr(result, "structuredContent", None):
        data = result.structuredContent
        # FastMCP wraps non-dict return values as {"result": ...}
        return data
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise ToolCallError(f"MCP tool '{tool}' returned non-JSON output: {text[:500]}") from exc


class WorkspaceClient:
    """MCP client to the workspace server, limited to an allow-list of tools.

    This is how each agent's tool access is enforced: e.g. the Tester can
    read the API summary and write tests but cannot run arbitrary tools.
    """

    MODULE = "ai_coder.servers.workspace_server"

    def __init__(self, root: str, allowed: set[str]) -> None:
        """Remember the workspace folder and the tools this agent may use."""
        self.root = root
        self.allowed = allowed
        self._cm = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "WorkspaceClient":
        """Start the workspace server and open the session."""
        self._cm = open_session(self.MODULE, "--root", self.root)
        self.session = await self._cm.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        """Close the session and stop the server process."""
        await self._cm.__aexit__(*exc)

    async def call(self, tool: str, **arguments) -> dict:
        """Call a workspace tool if it is on this agent's allow-list."""
        if tool not in self.allowed:
            raise PermissionError(f"Tool '{tool}' is not available to this agent")
        return await call_json(self.session, tool, arguments)
