# Author: Yuxiao Xiao (Student ID: 62185048)
# Written with AI assistance (Claude Code); see README section 9.
# SWE 270P Final Project - AI Coder: Coder agent.
"""Coder: spec + design -> source files written to the workspace over MCP.

Token-saving rules:
* one call for the whole project;
* if the model skipped files of the design, one follow-up call asks for
  those files only;
* if files do not compile, one repair call sends only the broken files.
"""

from __future__ import annotations

import re

from mcp.server.fastmcp import FastMCP

from .. import prompts
from ..parsing import compact_json, extract_files
from .base import AgentError, BaseAgent, render_files, spec_brief


class CoderAgent(BaseAgent):
    """Implements the architect's design."""

    role = "coder"
    tools = frozenset({"write_files", "check_syntax", "read_files"})
    max_tokens = 8192
    follow_up_tokens = 6144

    async def implement(self, spec: dict, design: dict, workspace: str) -> dict:
        """Write the project's source files; return their paths and any syntax errors left."""
        prompt = f"SPEC:\n{spec_brief(spec)}\n\nDESIGN:\n{compact_json(design)}"
        files = extract_files(await self.ask(prompts.CODER, prompt, self.max_tokens))
        missing = [path for path in planned_paths(design) if path not in files]
        if missing:
            files.update(extract_files(await self.ask(prompts.CODER, missing_files_prompt(prompt, files, missing),
                                                      self.follow_up_tokens)))
        files = {path: text for path, text in files.items() if not path.startswith("tests/")}
        if not files:
            raise AgentError("the model returned no files in the expected format")
        add_package_init(files, package_name(spec, design), spec.get("summary", ""))

        async with self.workspace(workspace) as ws:
            await ws.call("write_files", files=files)
            check = await ws.call("check_syntax")
            if not check["ok"]:
                check = await self._repair_syntax(ws, check["errors"])
        return {"files": sorted(files), "syntax_errors": check["errors"]}

    async def _repair_syntax(self, ws, errors: list[dict]) -> dict:
        """One LLM round that rewrites only the files that do not compile."""
        broken = (await ws.call("read_files", paths=[e["path"] for e in errors]))["files"]
        reply = await self.ask(prompts.CODER_FIX_SYNTAX, compact_json(errors) + "\n\n" + render_files(broken),
                               self.follow_up_tokens)
        fixed = extract_files(reply)
        if fixed:
            await ws.call("write_files", files=fixed)
        return await ws.call("check_syntax")

    def register(self, mcp: FastMCP) -> None:
        """Expose `generate_code` as an MCP tool."""

        @mcp.tool()
        async def generate_code(spec: dict, design: dict, workspace: str) -> dict:
            """Implement the design, write the files to the workspace and fix syntax errors once."""
            return await self.serve(self.implement, spec, design, workspace)


def package_name(spec: dict, design: dict) -> str:
    """Python package folder name from the design (or the spec's app name)."""
    raw = design.get("package") or spec.get("app_name") or "app"
    return re.sub(r"\W+", "_", str(raw)).strip("_").lower() or "app"


def planned_paths(design: dict) -> list[str]:
    """Source files the design asks for (tests excluded), always including main.py."""
    paths = [f["path"] for f in design.get("files", []) if isinstance(f, dict) and f.get("path")]
    paths = [p for p in paths if not p.startswith("tests/")]
    return paths if "main.py" in paths else paths + ["main.py"]


def missing_files_prompt(prompt: str, written: dict[str, str], missing: list[str]) -> str:
    """Follow-up request for the files the first reply left out."""
    return (f"{prompt}\n\nAlready written: {', '.join(sorted(written))}.\n"
            f"Now write ONLY these missing files: {', '.join(missing)}")


def add_package_init(files: dict[str, str], package: str, summary: str) -> None:
    """Add `<package>/__init__.py` (made by code, no tokens) if the package has files but no init."""
    init = f"{package}/__init__.py"
    if init not in files and any(path.startswith(package + "/") for path in files):
        files[init] = f'"""{(summary or package)[:80]}"""\n'
