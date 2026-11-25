# Author: Yuxiao Xiao (Student ID: 62185048)
# Written with AI assistance (Claude Code); see README section 9.
# SWE 270P Final Project - AI Coder: Tester agent.
"""Tester: public API + acceptance criteria -> pytest files in the workspace.

The Tester never sees full source files: it asks the workspace server for an
API summary (`extract_api`: signatures and first docstring lines), which costs
far fewer prompt tokens. `tests/__init__.py` and `tests/conftest.py` are
written by code, not by the model.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .. import prompts
from ..parsing import compact_json, extract_files
from .base import AgentError, BaseAgent

CONFTEST = (
    '"""Make the project root importable when running pytest."""\n'
    "import os\nimport sys\n\n"
    "sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))\n"
)


class TesterAgent(BaseAgent):
    """Writes pytest tests against the code's real public API."""

    __test__ = False  # the name starts with "Test": tell pytest this is not a test class
    role = "tester"
    tools = frozenset({"extract_api", "write_files", "check_syntax"})
    max_tokens = 6144

    async def write_tests(self, spec: dict, workspace: str) -> dict:
        """Generate test files; return their paths and any syntax errors."""
        async with self.workspace(workspace) as ws:
            api = (await ws.call("extract_api"))["api"]
            prompt = (f"PACKAGE: {spec.get('app_name', '')}\n"
                      f"ACCEPTANCE CRITERIA:\n{compact_json(acceptance_criteria(spec))}\n\nPUBLIC API:\n{api}")
            tests = place_under_tests(extract_files(await self.ask(prompts.TESTER, prompt, self.max_tokens)))
            if not tests:
                raise AgentError("the model returned no test files in the expected format")
            tests["tests/__init__.py"] = ""
            tests["tests/conftest.py"] = CONFTEST
            await ws.call("write_files", files=tests)
            check = await ws.call("check_syntax")
        return {"files": sorted(tests), "syntax_errors": check["errors"]}

    def register(self, mcp: FastMCP) -> None:
        """Expose `generate_tests` as an MCP tool."""

        @mcp.tool()
        async def generate_tests(spec: dict, workspace: str) -> dict:
            """Read the code's public API from the workspace and write pytest tests."""
            return await self.serve(self.write_tests, spec, workspace)


def acceptance_criteria(spec: dict) -> list[dict]:
    """Feature ids, names and acceptance criteria only: what the tests must cover."""
    return [{"id": f.get("id"), "name": f.get("name"), "acceptance": f.get("acceptance", [])}
            for f in spec.get("features", []) if isinstance(f, dict)]


def place_under_tests(files: dict[str, str]) -> dict[str, str]:
    """Move every file to tests/ and make sure its name starts with test_ so pytest finds it."""
    placed = {}
    for path, text in files.items():
        name = path.rsplit("/", 1)[-1]
        placed["tests/" + (name if name.startswith("test_") else "test_" + name)] = text
    return placed
