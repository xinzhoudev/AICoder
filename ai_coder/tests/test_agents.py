# Author: Yuxiao Xiao (Student ID: 62185048)
# Written with AI assistance (Claude Code); see README section 9.
# SWE 270P Final Project - AI Coder: unit tests for the five agents.
"""Unit tests for `ai_coder.agents`.

Each agent runs in-process with a scripted fake LLM and a local workspace
that enforces the same tool allow-list as the MCP WorkspaceClient, so these
tests are fast and check each agent's logic on its own. The MCP wiring is
covered by test_mcp_pipeline.py.
"""

import asyncio

import pytest

from ai_coder.agents import (AGENTS, ROLE_TOOLS, AnalystAgent, ArchitectAgent, CoderAgent, DebuggerAgent,
                             TesterAgent)
from ai_coder.config import Settings
from ai_coder.llm_client import BaseClient, LLMError
from ai_coder.usage import CallRecord
from ai_coder.workspace import Workspace


class ScriptedClient(BaseClient):
    """Fake LLM: returns the given replies in order and remembers every prompt."""

    def __init__(self, *replies):
        """Queue the replies; an exception instance is raised instead of returned."""
        super().__init__(Settings(provider="mock"))
        self.replies = list(replies)
        self.prompts = []

    def chat(self, agent, system, user, max_tokens=4096, json_mode=False):
        """Pop the next scripted reply."""
        self.prompts.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, CallRecord(agent, "fake", 10, 5, 0.0)


class LocalWorkspace:
    """In-process stand-in for WorkspaceClient with the same tool replies and allow-list."""

    def __init__(self, root, allowed):
        """Wrap a Workspace at `root`, allowing only the tools in `allowed`."""
        self.ws, self.allowed = Workspace(root), set(allowed)

    async def __aenter__(self):
        """Nothing to start."""
        return self

    async def __aexit__(self, *exc):
        """Nothing to stop."""

    async def call(self, tool, **args):
        """Run a workspace tool, refusing tools outside the allow-list."""
        if tool not in self.allowed:
            raise PermissionError(tool)
        ws = self.ws
        if tool == "check_syntax":
            errors = ws.check_syntax()
            return {"ok": not errors, "errors": errors}
        return {"write_files": lambda: {"written": ws.write_files(args["files"])},
                "read_files": lambda: {"files": ws.read_files(args["paths"])},
                "list_files": lambda: {"files": ws.list_files()},
                "extract_api": lambda: {"api": ws.extract_api()}}[tool]()


def make(agent_cls, *replies):
    """Build an agent with a scripted LLM and a local, allow-listed workspace."""
    agent = agent_cls(Settings(provider="mock"), ScriptedClient(*replies))
    agent.workspace = lambda root: LocalWorkspace(root, agent.tools)
    return agent


def block(path, text):
    """One file in the format the agents parse."""
    return f"=== FILE: {path} ===\n{text}\n=== END FILE ===\n"


def serve(agent, method, *args):
    """Run a skill through `serve`, as the MCP tool does."""
    return asyncio.run(agent.serve(method, *args))


def test_registry_and_allow_lists():
    """Every role is registered with the documented workspace tool allow-list."""
    assert set(AGENTS) == {"analyst", "architect", "coder", "tester", "debugger"}
    assert ROLE_TOOLS == {
        "analyst": set(), "architect": set(),
        "coder": {"write_files", "check_syntax", "read_files"},
        "tester": {"extract_api", "write_files", "check_syntax"},
        "debugger": {"list_files", "read_files", "write_files", "check_syntax"},
    }


def test_analyst_returns_model_spec():
    """A valid JSON reply becomes the spec after a single LLM call."""
    agent = make(AnalystAgent, '{"app_name": "todo", "features": [{"id": "F1", "name": "Add"}]}')
    result = serve(agent, agent.analyze, "A todo app", "FR1 add items")
    assert result["ok"] and result["spec"]["app_name"] == "todo"
    assert [u["agent"] for u in result["usage"]] == ["analyst"]


def test_analyst_falls_back_after_two_bad_replies():
    """Unparseable JSON is retried once, then a spec is built from the requirement lines."""
    agent = make(AnalystAgent, "not json", '{"features": []}')
    result = serve(agent, agent.analyze, "Budget tracker app", "- FR1 add expense\n\n- FR2 monthly report")
    spec = result["spec"]
    assert result["ok"] and spec["app_name"] == "budget_tracker_app"
    assert [f["details"] for f in spec["features"]] == ["FR1 add expense", "FR2 monthly report"]
    assert len(result["usage"]) == 2 and "valid JSON only" in agent.client.prompts[1]


def test_model_server_error_is_reported_not_raised():
    """An unreachable model gives ok=False with the role in the message."""
    agent = make(AnalystAgent, LLMError("connection refused"))
    result = serve(agent, agent.analyze, "x", "y")
    assert result == {"ok": False, "error": "analyst: connection refused", "usage": []}


def test_architect_rejects_design_without_files():
    """A design with no files is an error, and the tokens spent are still reported."""
    agent = make(ArchitectAgent, '{"package": "app", "files": []}')
    result = serve(agent, agent.design, {"app_name": "app"})
    assert not result["ok"] and "no files" in result["error"] and len(result["usage"]) == 1


def test_coder_asks_only_for_missing_files_and_adds_init(tmp_path):
    """Skipped files are requested in one follow-up; test files are dropped; __init__ is added."""
    design = {"package": "shop", "files": [{"path": "shop/cart.py"}, {"path": "main.py"}]}
    agent = make(CoderAgent,
                 block("shop/cart.py", "def total(items):\n    return sum(items)") + block("tests/test_x.py", "x"),
                 block("main.py", "print('hi')"))
    result = serve(agent, agent.implement, {"summary": "A shop"}, design, str(tmp_path))
    assert result["ok"] and result["files"] == ["main.py", "shop/__init__.py", "shop/cart.py"]
    assert result["syntax_errors"] == []
    assert "ONLY these missing files: main.py" in agent.client.prompts[1]
    assert not (tmp_path / "tests").exists()
    assert (tmp_path / "shop" / "__init__.py").read_text() == '"""A shop"""\n'


def test_coder_repairs_syntax_errors_once(tmp_path):
    """Files that do not compile are sent back once, alone, and the fix is written."""
    agent = make(CoderAgent, block("main.py", "def f(:\n    pass"), block("main.py", "def f():\n    pass"))
    result = serve(agent, agent.implement, {}, {"files": [{"path": "main.py"}]}, str(tmp_path))
    assert result["ok"] and result["syntax_errors"] == []
    assert "def f(:" in agent.client.prompts[1] and len(result["usage"]) == 2
    assert (tmp_path / "main.py").read_text().strip() == "def f():\n    pass"


def test_tester_sees_api_only_and_places_files_under_tests(tmp_path):
    """The Tester gets signatures, not bodies, and its files land in tests/ as test_*.py."""
    Workspace(tmp_path).write_files({"calc/ops.py": 'def add(a: int, b: int) -> int:\n    """Add."""\n'
                                                   "    return a + b  # SECRET_BODY\n"})
    agent = make(TesterAgent, block("test_ops.py", "def test_a():\n    pass") + block("more.py", "x = 1"))
    result = serve(agent, agent.write_tests, {"app_name": "calc", "features": []}, str(tmp_path))
    assert result["ok"]
    assert result["files"] == ["tests/__init__.py", "tests/conftest.py", "tests/test_more.py", "tests/test_ops.py"]
    assert "def add(a: int, b: int) -> int" in agent.client.prompts[0]
    assert "SECRET_BODY" not in agent.client.prompts[0]


def test_debugger_sends_relevant_files_and_writes_only_python(tmp_path):
    """The Debugger sees the failing test and source files and only .py fixes are written."""
    Workspace(tmp_path).write_files({"calc/ops.py": "def add(a, b):\n    return a - b\n",
                                     "tests/test_ops.py": "def test_add():\n    assert add(1, 2) == 3\n",
                                     "tests/test_other.py": "def test_ok():\n    pass\n"})
    agent = make(DebuggerAgent, block("calc/ops.py", "def add(a, b):\n    return a + b") + block("notes.txt", "x"))
    output = "FAILED tests/test_ops.py::test_add - assert -1 == 3"
    result = serve(agent, agent.fix, {"app_name": "calc"}, str(tmp_path), output, 3500)
    assert result["ok"] and result["changed"] == ["calc/ops.py"]
    prompt = agent.client.prompts[0]
    assert "=== FILE: calc/ops.py ===" in prompt and "=== FILE: tests/test_ops.py ===" in prompt
    assert "tests/test_other.py" not in prompt
    assert not (tmp_path / "notes.txt").exists()
    assert "a + b" in (tmp_path / "calc" / "ops.py").read_text()


def test_agents_cannot_use_tools_outside_their_list(tmp_path):
    """The local workspace used in these tests refuses tools a role does not have."""
    agent = make(TesterAgent)

    async def go():
        """Try a tool the Tester is not allowed to use."""
        async with agent.workspace(str(tmp_path)) as ws:
            await ws.call("read_files", paths=["x.py"])

    with pytest.raises(PermissionError):
        asyncio.run(go())
