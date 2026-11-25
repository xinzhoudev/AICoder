# Author: Yuxiao Xiao (Student ID: 62185048)
# SWE 270P Final Project - AI Coder: Debugger agent.
"""Debugger: failing pytest output -> corrected files in the workspace.

It sends the model the trimmed pytest output and the relevant files: the
files the output mentions plus every source module, or only the mentioned
files if that would be too large. Only .py files from the reply are written.
Whether a round is kept or rolled back is decided by the orchestrator.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .. import prompts
from ..parsing import extract_files, truncate
from .base import BaseAgent, render_files, spec_brief

MAX_CONTEXT_CHARS = 40_000
SKIP_NAMES = ("__init__.py", "conftest.py")


class DebuggerAgent(BaseAgent):
    """Fixes the code (or a wrong test) after a failing test run."""

    role = "debugger"
    tools = frozenset({"list_files", "read_files", "write_files", "check_syntax"})
    max_tokens = 8192

    async def fix(self, spec: dict, workspace: str, test_output: str, max_chars: int) -> dict:
        """Ask for fixes and write them; return the changed paths and any syntax errors."""
        async with self.workspace(workspace) as ws:
            python_files = [f for f in (await ws.call("list_files"))["files"]
                            if f.endswith(".py") and not f.endswith(SKIP_NAMES)]
            mentioned, wanted = files_for_context(python_files, test_output)
            contents = (await ws.call("read_files", paths=wanted))["files"]
            if sum(len(text) for text in contents.values()) > MAX_CONTEXT_CHARS:
                contents = {p: t for p, t in contents.items() if p in mentioned} or contents
            prompt = (f"SPEC:\n{spec_brief(spec)}\n\nPYTEST OUTPUT:\n{truncate(test_output, max_chars)}"
                      f"\n\nFILES:\n{render_files(contents)}")
            fixed = {p: t for p, t in extract_files(await self.ask(prompts.DEBUGGER, prompt, self.max_tokens)).items()
                     if p.endswith(".py")}
            if fixed:
                await ws.call("write_files", files=fixed)
            check = await ws.call("check_syntax")
        return {"changed": sorted(fixed), "syntax_errors": check["errors"]}

    def register(self, mcp: FastMCP) -> None:
        """Expose `fix_failures` as an MCP tool."""

        @mcp.tool()
        async def fix_failures(spec: dict, workspace: str, test_output: str, max_chars: int = 3500) -> dict:
            """Read failing tests and related source, ask the LLM for fixes, write them back."""
            return await self.serve(self.fix, spec, workspace, test_output, max_chars)


def files_for_context(python_files: list[str], test_output: str) -> tuple[set[str], list[str]]:
    """Return (files named in the pytest output, files to read: those plus all source modules)."""
    mentioned = {f for f in python_files if f in test_output or f.rsplit("/", 1)[-1] in test_output}
    sources = {f for f in python_files if not f.startswith("tests/") and f != "main.py"}
    return mentioned, sorted(mentioned | sources)
