# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# Contributor: Yuxiao Xiao (Student ID: 62185048)
# SWE 270P Final Project - AI Coder: Workspace MCP server (file and test tools).
"""Workspace MCP server.

Exposes file, syntax-check, API-extraction and pytest tools for one project
folder. Agents and the orchestrator talk to it over MCP (stdio). It acts as
the shared "blackboard": agents exchange code by writing to and reading from
this workspace instead of passing whole files through prompts.

Run:  python -m ai_coder.servers.workspace_server --root output/<job_id>
"""

from __future__ import annotations

import argparse
import logging
import sys

from mcp.server.fastmcp import FastMCP

from ai_coder.workspace import Workspace

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)


def build_server(root: str) -> FastMCP:
    """Create the MCP server with its tools bound to the workspace at `root`."""
    ws = Workspace(root)
    mcp = FastMCP("workspace")

    @mcp.tool()
    def write_files(files: dict[str, str]) -> dict:
        """Write files into the workspace. `files` maps relative path -> full content."""
        return {"written": ws.write_files(files)}

    @mcp.tool()
    def read_files(paths: list[str]) -> dict:
        """Read several files; returns {path: content} for those that exist."""
        return {"files": ws.read_files(paths)}

    @mcp.tool()
    def list_files() -> dict:
        """List every file in the workspace (relative paths)."""
        return {"files": ws.list_files()}

    @mcp.tool()
    def check_syntax() -> dict:
        """Compile every Python file and report syntax errors."""
        errors = ws.check_syntax()
        return {"ok": not errors, "errors": errors}

    @mcp.tool()
    def extract_api() -> dict:
        """Summarise the public API (signatures and docstrings) of non-test modules."""
        return {"api": ws.extract_api()}

    @mcp.tool()
    def run_tests(timeout: int = 120) -> dict:
        """Run pytest on tests/ and return passed/failed/error counts and output."""
        return ws.run_tests(timeout=timeout)

    @mcp.tool()
    def snapshot(name: str = "default") -> dict:
        """Save the current state of all files so it can be restored later.

        Used by the orchestrator before each Debugger round; no agent has it
        on its allow-list."""
        return {"files": ws.snapshot(name)}

    @mcp.tool()
    def restore(name: str = "default") -> dict:
        """Return the workspace to a saved snapshot (undo a bad Debugger round)."""
        return {"changed": ws.restore(name)}

    @mcp.tool()
    def add_headers(header: str) -> dict:
        """Prepend an author header comment to every Python file."""
        return {"changed": ws.add_headers(header)}

    return mcp


def main() -> None:
    """Parse --root and serve over stdio."""
    parser = argparse.ArgumentParser(description="Workspace MCP server")
    parser.add_argument("--root", required=True, help="project folder to operate on")
    args = parser.parse_args()
    build_server(args.root).run(transport="stdio")


if __name__ == "__main__":
    main()
