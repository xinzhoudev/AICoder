# Author: Yuxiao Xiao (Student ID: 62185048)
# Written with AI assistance (Claude Code); see README section 9.
# SWE 270P Final Project - AI Coder: agent MCP server (one process per role).
"""LLM agent MCP server.

Each agent runs as its own MCP server process and exposes its skill as one
MCP tool; the agents themselves live in `ai_coder/agents/`, one module per
role (see the table in `ai_coder/agents/__init__.py`). Agents that work on
code are in turn MCP *clients* of the workspace server, limited to their
allow-list of tools. Every tool result carries the LLM usage records of the
calls it made, which the orchestrator adds up.

Run:  python -m ai_coder.servers.agent_server --role coder
"""

from __future__ import annotations

import argparse
import logging
import sys

from mcp.server.fastmcp import FastMCP

from ai_coder.agents import AGENTS, ROLE_TOOLS  # noqa: F401  (ROLE_TOOLS re-exported for docs/tools)

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)


def build_server(role: str) -> FastMCP:
    """Create the MCP server for one agent role with that agent's tool registered."""
    if role not in AGENTS:
        raise ValueError(f"unknown role: {role}")
    mcp = FastMCP(f"{role}-agent")
    AGENTS[role]().register(mcp)
    return mcp


def main() -> None:
    """Parse --role and serve that agent over stdio."""
    parser = argparse.ArgumentParser(description="AI Coder agent MCP server")
    parser.add_argument("--role", required=True, choices=sorted(AGENTS))
    args = parser.parse_args()
    build_server(args.role).run(transport="stdio")


if __name__ == "__main__":
    main()
