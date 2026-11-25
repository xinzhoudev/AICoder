# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: agent MCP servers (one process per role).
"""LLM agent MCP server.

Each agent runs as its own MCP server process and exposes its skill as an
MCP tool:

| role      | MCP tool               | workspace tools it may use                     |
|-----------|------------------------|------------------------------------------------|
| analyst   | analyze_requirements   | (none)                                         |
| architect | design_architecture    | (none)                                         |
| coder     | generate_code          | write_files, check_syntax, read_files          |
| tester    | generate_tests         | extract_api, write_files, check_syntax         |
| debugger  | fix_failures           | list_files, read_files, write_files, check_syntax |

Agents that touch code are MCP *clients* of the workspace server, so the
agent-to-tool traffic also goes through MCP. Every tool result carries the
LLM usage records of the calls it made.

Run:  python -m ai_coder.servers.agent_server --role coder
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys

from mcp.server.fastmcp import FastMCP

from ai_coder import prompts
from ai_coder.config import get_settings
from ai_coder.llm_client import LLMError, make_client
from ai_coder.mcp_client import WorkspaceClient
from ai_coder.parsing import compact_json, extract_files, extract_json, truncate

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
log = logging.getLogger("agent")

ROLE_TOOLS: dict[str, set[str]] = {
    "analyst": set(),
    "architect": set(),
    "coder": {"write_files", "check_syntax", "read_files"},
    "tester": {"extract_api", "write_files", "check_syntax"},
    "debugger": {"list_files", "read_files", "write_files", "check_syntax"},
}

CONFTEST = (
    '"""Make the project root importable when running pytest."""\n'
    "import os\nimport sys\n\n"
    "sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))\n"
)


class Agent:
    """Wraps the LLM client for one role and collects its usage records."""

    def __init__(self, role: str) -> None:
        """Create the client for `role` from the current settings."""
        self.role = role
        self.settings = get_settings()
        self.client = make_client(self.settings)
        self.usage: list[dict] = []

    async def ask(self, system: str, user: str, max_tokens: int = 4096, json_mode: bool = False) -> str:
        """Run one LLM call in a worker thread and record its usage."""
        text, record = await asyncio.to_thread(self.client.chat, self.role, system, user, max_tokens, json_mode)
        self.usage.append(record.to_dict())
        return text

    def take_usage(self) -> list[dict]:
        """Return and clear the usage records gathered during one tool call."""
        records, self.usage = self.usage, []
        return records

    def workspace(self, root: str) -> WorkspaceClient:
        """Open an MCP client to the workspace limited to this role's tools."""
        return WorkspaceClient(root, ROLE_TOOLS[self.role])


def _spec_brief(spec: dict) -> str:
    """Compact spec for later agents: drop fields they do not need."""
    keep = {k: spec.get(k) for k in ("app_name", "summary", "features", "constraints") if k in spec}
    return compact_json(keep)


def _fallback_spec(description: str, requirements: str) -> dict:
    """Spec built without the LLM when the analyst's JSON cannot be parsed."""
    words = re.findall(r"[a-z]+", description.lower())[:3] or ["app"]
    lines = [ln.strip(" -*\t") for ln in requirements.splitlines() if ln.strip(" -*\t")]
    return {
        "app_name": "_".join(words),
        "summary": description.strip()[:300],
        "features": [{"id": f"F{i + 1}", "name": ln[:60], "details": ln, "acceptance": [ln]}
                     for i, ln in enumerate(lines[:10])],
        "data_entities": [],
        "constraints": [],
    }


def _package_name(spec: dict, design: dict) -> str:
    """Package folder name taken from the design (or spec)."""
    name = design.get("package") or spec.get("app_name") or "app"
    return re.sub(r"\W+", "_", str(name)).strip("_").lower() or "app"


def register_tools(mcp: FastMCP, agent: Agent) -> None:
    """Register the MCP tool(s) that belong to `agent.role`."""

    if agent.role == "analyst":
        @mcp.tool()
        async def analyze_requirements(description: str, requirements: str) -> dict:
            """Turn a software description and requirements into a JSON specification."""
            user = f"DESCRIPTION:\n{description.strip()}\n\nREQUIREMENTS:\n{requirements.strip()}"
            try:
                reply = await agent.ask(prompts.ANALYST, user, max_tokens=2048, json_mode=True)
                try:
                    spec = extract_json(reply)
                except ValueError:
                    reply = await agent.ask(prompts.ANALYST, user + "\n\nReply with valid JSON only.", 2048, True)
                    spec = extract_json(reply)
                if not isinstance(spec, dict) or not spec.get("features"):
                    raise ValueError("spec missing features")
            except ValueError:
                spec = _fallback_spec(description, requirements)
            except LLMError as exc:
                return {"ok": False, "error": str(exc), "usage": agent.take_usage()}
            return {"ok": True, "spec": spec, "usage": agent.take_usage()}

    elif agent.role == "architect":
        @mcp.tool()
        async def design_architecture(spec: dict) -> dict:
            """Produce a file-by-file design with exact public signatures."""
            user = f"SPEC:\n{compact_json(spec)}"
            try:
                reply = await agent.ask(prompts.ARCHITECT, user, max_tokens=3072, json_mode=True)
                try:
                    design = extract_json(reply)
                except ValueError:
                    reply = await agent.ask(prompts.ARCHITECT, user + "\n\nReply with valid JSON only.", 3072, True)
                    design = extract_json(reply)
            except (LLMError, ValueError) as exc:
                return {"ok": False, "error": f"architect failed: {exc}", "usage": agent.take_usage()}
            if not isinstance(design, dict) or not design.get("files"):
                return {"ok": False, "error": "architect returned no files", "usage": agent.take_usage()}
            return {"ok": True, "design": design, "usage": agent.take_usage()}

    elif agent.role == "coder":
        @mcp.tool()
        async def generate_code(spec: dict, design: dict, workspace: str) -> dict:
            """Implement the design, write the files to the workspace and fix syntax errors once."""
            package = _package_name(spec, design)
            expected = [f["path"] for f in design.get("files", []) if isinstance(f, dict) and f.get("path")]
            expected = [p for p in expected if not p.startswith("tests/")]
            if "main.py" not in expected:
                expected.append("main.py")
            user = f"SPEC:\n{_spec_brief(spec)}\n\nDESIGN:\n{compact_json(design)}"
            try:
                files = extract_files(await agent.ask(prompts.CODER, user, max_tokens=8192))
                missing = [p for p in expected if p not in files]
                if missing:  # one targeted follow-up for files the model skipped
                    follow = (user + f"\n\nAlready written: {', '.join(sorted(files))}.\n"
                              f"Now write ONLY these missing files: {', '.join(missing)}")
                    files.update(extract_files(await agent.ask(prompts.CODER, follow, max_tokens=6144)))
                if not files:
                    return {"ok": False, "error": "coder produced no parseable files",
                            "usage": agent.take_usage()}
                files = {p: c for p, c in files.items() if not p.startswith("tests/")}
                init = f"{package}/__init__.py"
                if init not in files and any(p.startswith(package + "/") for p in files):
                    files[init] = f'"""{spec.get("summary", package)[:80]}"""\n'
                async with agent.workspace(workspace) as ws:
                    await ws.call("write_files", files=files)
                    check = await ws.call("check_syntax")
                    if not check["ok"]:  # one self-repair round for syntax errors
                        broken = [e["path"] for e in check["errors"]]
                        sources = (await ws.call("read_files", paths=broken))["files"]
                        fix_user = compact_json(check["errors"]) + "\n\n" + "\n\n".join(
                            f"=== FILE: {p} ===\n{c}\n=== END FILE ===" for p, c in sources.items())
                        fixed = extract_files(await agent.ask(prompts.CODER_FIX_SYNTAX, fix_user, 6144))
                        if fixed:
                            await ws.call("write_files", files=fixed)
                        check = await ws.call("check_syntax")
            except LLMError as exc:
                return {"ok": False, "error": str(exc), "usage": agent.take_usage()}
            return {"ok": True, "files": sorted(files), "syntax_errors": check["errors"],
                    "usage": agent.take_usage()}

    elif agent.role == "tester":
        @mcp.tool()
        async def generate_tests(spec: dict, workspace: str) -> dict:
            """Read the code's public API from the workspace and write pytest tests."""
            try:
                async with agent.workspace(workspace) as ws:
                    api = (await ws.call("extract_api"))["api"]
                    acceptance = [{"id": f.get("id"), "name": f.get("name"), "acceptance": f.get("acceptance", [])}
                                  for f in spec.get("features", []) if isinstance(f, dict)]
                    user = (f"PACKAGE: {spec.get('app_name', '')}\n"
                            f"ACCEPTANCE CRITERIA:\n{compact_json(acceptance)}\n\nPUBLIC API:\n{api}")
                    files = extract_files(await agent.ask(prompts.TESTER, user, max_tokens=6144))
                    tests = {}
                    for path, content in files.items():
                        name = path.split("/")[-1]
                        if not name.startswith("test_"):
                            name = "test_" + name
                        tests[f"tests/{name}"] = content
                    if not tests:
                        return {"ok": False, "error": "tester produced no parseable test files",
                                "usage": agent.take_usage()}
                    tests["tests/__init__.py"] = ""
                    tests["tests/conftest.py"] = CONFTEST
                    await ws.call("write_files", files=tests)
                    check = await ws.call("check_syntax")
            except LLMError as exc:
                return {"ok": False, "error": str(exc), "usage": agent.take_usage()}
            return {"ok": True, "files": sorted(tests), "syntax_errors": check["errors"],
                    "usage": agent.take_usage()}

    elif agent.role == "debugger":
        @mcp.tool()
        async def fix_failures(spec: dict, workspace: str, test_output: str, max_chars: int = 3500) -> dict:
            """Read failing tests and related source, ask the LLM for fixes, write them back."""
            report = truncate(test_output, max_chars)
            try:
                async with agent.workspace(workspace) as ws:
                    all_files = [f for f in (await ws.call("list_files"))["files"] if f.endswith(".py")
                                 and not f.endswith(("__init__.py", "conftest.py"))]
                    mentioned = [f for f in all_files if f in test_output or f.split("/")[-1] in test_output]
                    sources = [f for f in all_files if not f.startswith("tests/") and f != "main.py"]
                    # Failing tests + every source module, unless that is too large.
                    chosen = sorted(set(mentioned) | set(sources))
                    contents = (await ws.call("read_files", paths=chosen))["files"]
                    if sum(len(c) for c in contents.values()) > 40000:
                        contents = {p: c for p, c in contents.items() if p in mentioned} or contents
                    user = (f"SPEC:\n{_spec_brief(spec)}\n\nPYTEST OUTPUT:\n{report}\n\nFILES:\n" + "\n\n".join(
                        f"=== FILE: {p} ===\n{c}\n=== END FILE ===" for p, c in contents.items()))
                    fixed = extract_files(await agent.ask(prompts.DEBUGGER, user, max_tokens=8192))
                    fixed = {p: c for p, c in fixed.items() if p.endswith(".py")}
                    if fixed:
                        await ws.call("write_files", files=fixed)
                    check = await ws.call("check_syntax")
            except LLMError as exc:
                return {"ok": False, "error": str(exc), "usage": agent.take_usage()}
            return {"ok": True, "changed": sorted(fixed), "syntax_errors": check["errors"],
                    "usage": agent.take_usage()}

    else:
        raise ValueError(f"unknown role: {agent.role}")


def build_server(role: str) -> FastMCP:
    """Create the MCP server for one agent role."""
    mcp = FastMCP(f"{role}-agent")
    register_tools(mcp, Agent(role))
    return mcp


def main() -> None:
    """Parse --role and serve that agent over stdio."""
    parser = argparse.ArgumentParser(description="AI Coder agent MCP server")
    parser.add_argument("--role", required=True, choices=sorted(ROLE_TOOLS))
    args = parser.parse_args()
    build_server(args.role).run(transport="stdio")


if __name__ == "__main__":
    main()
