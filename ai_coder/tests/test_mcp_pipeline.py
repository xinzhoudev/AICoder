# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# Contributor: Yuxiao Xiao (Student ID: 62185048)
# SWE 270P Final Project - AI Coder: integration tests over real MCP stdio servers.
"""Integration tests: start the real MCP servers (with the mock LLM) and
exercise tool listing, tool permissions, the debugger loop and the whole
pipeline, plus the web UI endpoints."""

import asyncio
import json

import pytest

from ai_coder.config import Settings
from ai_coder.mcp_client import WorkspaceClient, call_json, open_session
from ai_coder.orchestrator import Orchestrator, RunResult
from ai_coder.parsing import split_spec
from ai_coder.config import SPECS_DIR

AGENT = "ai_coder.servers.agent_server"


def run(coro):
    """Run a coroutine to completion."""
    return asyncio.run(coro)


def test_each_agent_exposes_its_tool():
    async def go():
        names = {}
        for role in ("analyst", "architect", "coder", "tester", "debugger"):
            async with open_session(AGENT, "--role", role) as session:
                names[role] = [t.name for t in (await session.list_tools()).tools]
        return names
    assert run(go()) == {"analyst": ["analyze_requirements"], "architect": ["design_architecture"],
                         "coder": ["generate_code"], "tester": ["generate_tests"], "debugger": ["fix_failures"]}


def test_workspace_client_enforces_allow_list(tmp_path):
    async def go():
        async with WorkspaceClient(str(tmp_path), {"write_files"}) as ws:
            await ws.call("write_files", files={"a.py": "x = 1\n"})
            with pytest.raises(PermissionError):
                await ws.call("run_tests")
    run(go())
    assert (tmp_path / "a.py").exists()


def test_debugger_agent_repairs_broken_code(reference_project):
    analytics = reference_project / "customer_feedback_tracker" / "analytics.py"
    analytics.write_text(analytics.read_text().replace("promoters - detractors", "promoters + detractors"))

    async def go():
        async with open_session("ai_coder.servers.workspace_server", "--root", str(reference_project)) as ws:
            before = await call_json(ws, "run_tests", {"timeout": 120})
            async with open_session(AGENT, "--role", "debugger") as dbg:
                fix = await call_json(dbg, "fix_failures", {
                    "spec": {"app_name": "customer_feedback_tracker"}, "workspace": str(reference_project),
                    "test_output": before["output"]})
            after = await call_json(ws, "run_tests", {"timeout": 120})
        return before, fix, after

    before, fix, after = run(go())
    assert before["failed"] >= 1
    assert fix["ok"] and "customer_feedback_tracker/analytics.py" in fix["changed"]
    assert len(fix["usage"]) == 1 and fix["usage"][0]["agent"] == "debugger"
    assert after["success"]


def test_full_pipeline_with_mock_llm(tmp_path):
    description, requirements = split_spec((SPECS_DIR / "customer_feedback_tracker.md").read_text())
    events = []
    orch = Orchestrator(Settings(provider="mock"), on_event=events.append, output_dir=tmp_path)
    result = run(orch.run(description, requirements, job_id="t1"))
    assert result.error == "" and result.success
    assert result.usage["total_api_calls"] == 4
    assert [r["outcome"] for r in result.rounds] == ["initial run"] and result.rounds[0]["passed"] > 0
    assert set(result.usage["by_agent"]) == {"analyst", "architect", "coder", "tester"}
    assert "main.py" in result.files and any(f.startswith("tests/test_") for f in result.files)
    job = tmp_path / "t1"
    assert (job / "generated_project.zip").exists()
    assert json.loads((job / "usage_report.json").read_text())["total_tokens"] > 0
    assert (job / "project" / "main.py").read_text().startswith("# Author:")
    assert any(e["agent"] == "workspace" and "passed" in e["message"] for e in events)


def test_web_ui_endpoints():
    from ai_coder.ui.app import app

    client = app.test_client()
    assert client.get("/").status_code == 200
    cfg = client.get("/api/config").get_json()
    assert "Customer Feedback Tracker" in cfg["example"]["description"]
    assert client.post("/api/generate", json={"description": ""}).status_code == 400
    assert client.get("/api/jobs/does-not-exist").status_code == 404


def _debug_loop_with_fake_debugger(project, new_source, settings=None):
    """Run the orchestrator's debug loop against a real workspace server,
    with a fake Debugger that writes `new_source` into analytics.py."""
    target = "customer_feedback_tracker/analytics.py"
    orch = Orchestrator(settings or Settings(provider="mock", max_fix_rounds=2))
    calls = []

    async def fake_call_agent(session, role, tool, args):
        calls.append(role)
        (project / target).write_text(new_source)
        return {"ok": True, "changed": [target], "syntax_errors": []}

    orch._call_agent = fake_call_agent

    async def go():
        async with open_session("ai_coder.servers.workspace_server", "--root", str(project)) as ws:
            result = RunResult(job_id="t", project_dir=str(project), success=False)
            report = await orch._debug_loop(ws, None, {}, str(project), result)
        return report, result

    report, result = run(go())
    return report, result, calls


def test_debug_loop_rolls_back_a_round_that_makes_things_worse(reference_project):
    analytics = reference_project / "customer_feedback_tracker" / "analytics.py"
    broken_once = analytics.read_text().replace("promoters - detractors", "promoters + detractors")
    analytics.write_text(broken_once)

    report, result, calls = _debug_loop_with_fake_debugger(reference_project, "def oops(:\n")
    assert calls == ["debugger"]  # stopped after the bad round instead of trying again
    assert analytics.read_text() == broken_once  # the bad edit was undone
    assert [r["outcome"] for r in result.rounds] == ["initial run", "worse, rolled back"]
    assert report["failed"] >= 1 and result.rounds[0]["failed"] == report["failed"]


def test_debug_loop_keeps_a_round_that_fixes_the_tests(reference_project):
    analytics = reference_project / "customer_feedback_tracker" / "analytics.py"
    good = analytics.read_text()
    analytics.write_text(good.replace("promoters - detractors", "promoters + detractors"))

    report, result, calls = _debug_loop_with_fake_debugger(reference_project, good)
    assert calls == ["debugger"] and report["success"]
    assert analytics.read_text() == good
    assert [r["outcome"] for r in result.rounds] == ["initial run", "improved, kept"]


def test_first_leaf_unwraps_nested_exception_groups():
    from ai_coder.orchestrator import _first_leaf

    try:
        group = BaseExceptionGroup
    except NameError:  # Python 3.10
        from exceptiongroup import BaseExceptionGroup as group
    inner = ConnectionRefusedError("refused")
    nested = group("outer", [group("inner", [inner])])
    assert _first_leaf(nested) is inner
    assert _first_leaf(inner) is inner
