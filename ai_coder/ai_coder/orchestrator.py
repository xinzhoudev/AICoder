# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# Contributor: Yuxiao Xiao (Student ID: 62185048)
# SWE 270P Final Project - AI Coder: orchestrator (MCP client coordinating the agents).
"""Orchestrator.

The orchestrator is an MCP client. It launches the five agent servers and a
workspace server, then runs a fixed pipeline:

    Analyst -> Architect -> Coder -> Tester -> run tests -> Debugger loop

The control flow is deterministic code rather than another LLM agent: a
planner LLM would add an API call (and its tokens) at every step without
changing the order, which is always the same for this task.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import LOG_DIR, OUTPUT_DIR, Settings, get_settings
from .mcp_client import ToolCallError, call_json, open_session
from .usage import UsageTracker

try:  # built in from Python 3.11; anyio installs the backport on 3.10
    BaseExceptionGroup
except NameError:  # pragma: no cover - only on Python 3.10
    from exceptiongroup import BaseExceptionGroup

AGENT_MODULE = "ai_coder.servers.agent_server"
WORKSPACE_MODULE = "ai_coder.servers.workspace_server"
ROLES = ("analyst", "architect", "coder", "tester", "debugger")


@dataclass
class RunResult:
    """Everything a generation run produced."""

    job_id: str
    project_dir: str
    success: bool
    spec: dict = field(default_factory=dict)
    design: dict = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    test_report: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    fix_rounds: int = 0
    rounds: list[dict] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        """JSON-serialisable view of the result."""
        return self.__dict__.copy()


class Orchestrator:
    """Coordinates the agent MCP servers for one generation job."""

    def __init__(self, settings: Settings | None = None,
                 on_event: Callable[[dict], None] | None = None,
                 output_dir: Path = OUTPUT_DIR) -> None:
        """Set up settings, the progress callback and the output folder."""
        self.settings = settings or get_settings()
        self.on_event = on_event or (lambda event: None)
        self.output_dir = Path(output_dir)
        self.usage = UsageTracker()

    def emit(self, agent: str, message: str, level: str = "info", **extra) -> None:
        """Send a progress event to the UI / CLI."""
        event = {"time": time.strftime("%H:%M:%S"), "agent": agent, "message": message,
                 "level": level, "api_calls": self.usage.total_calls,
                 "tokens": self.usage.total_tokens}
        event.update(extra)
        self.on_event(event)

    async def _call_agent(self, session, role: str, tool: str, args: dict) -> dict:
        """Call one agent tool over MCP, record its usage and check for errors."""
        before = self.usage.total_tokens
        result = await call_json(session, tool, args)
        self.usage.extend(result.get("usage", []))
        spent = self.usage.total_tokens - before
        if not result.get("ok"):
            raise ToolCallError(f"{role}: {result.get('error', 'unknown error')}")
        self.emit(role, f"{tool} done ({spent} tokens)", level="done")
        return result

    async def run(self, description: str, requirements: str, job_id: str | None = None) -> RunResult:
        """Run the full pipeline and return a `RunResult`."""
        job_id = job_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job_dir = self.output_dir / job_id
        project_dir = job_dir / "project"
        project_dir.mkdir(parents=True, exist_ok=True)
        root = str(project_dir)
        result = RunResult(job_id=job_id, project_dir=root, success=False)

        try:
            async with AsyncExitStack() as stack:
                self.emit("orchestrator", f"Starting MCP servers (model: {self.settings.public_dict()['model']})")
                ws = await stack.enter_async_context(open_session(WORKSPACE_MODULE, "--root", root))
                agents = {}
                for role in ROLES:
                    agents[role] = await stack.enter_async_context(open_session(AGENT_MODULE, "--role", role))
                tools = {role: [t.name for t in (await s.list_tools()).tools] for role, s in agents.items()}
                self.emit("orchestrator", "Agents online: " + ", ".join(f"{r}({', '.join(t)})" for r, t in tools.items()))

                # 1. Requirements analysis
                self.emit("analyst", "Analyzing description and requirements...", level="working")
                spec = (await self._call_agent(agents["analyst"], "analyst", "analyze_requirements",
                                               {"description": description, "requirements": requirements}))["spec"]
                result.spec = spec
                self.emit("analyst", f"Spec: {len(spec.get('features', []))} features for '{spec.get('app_name')}'")

                # 2. Architecture
                self.emit("architect", "Designing modules and public API...", level="working")
                design = (await self._call_agent(agents["architect"], "architect", "design_architecture",
                                                 {"spec": spec}))["design"]
                result.design = design
                self.emit("architect", "Planned files: " + ", ".join(f.get("path", "?") for f in design["files"]))

                # 3. Code generation
                self.emit("coder", "Writing source code...", level="working")
                code = await self._call_agent(agents["coder"], "coder", "generate_code",
                                              {"spec": spec, "design": design, "workspace": root})
                self.emit("coder", f"Wrote {len(code['files'])} files"
                          + (f"; syntax errors left: {code['syntax_errors']}" if code["syntax_errors"] else ""))

                # 4. Test generation
                self.emit("tester", "Writing pytest test cases from the code's API...", level="working")
                tests = await self._call_agent(agents["tester"], "tester", "generate_tests",
                                               {"spec": spec, "workspace": root})
                self.emit("tester", f"Wrote {len(tests['files'])} test files")

                # 5. Test / debug loop
                report = await self._debug_loop(ws, agents["debugger"], spec, root, result)
                result.test_report = report
                result.success = report["success"]

                # 6. Finishing touches (no LLM calls)
                header = (f"# Author: {self.settings.author_name} (Student ID: {self.settings.student_id})\n"
                          f"# Generated by AI Coder (multi-agent MCP system) from: {spec.get('app_name', '')}\n")
                await call_json(ws, "add_headers", {"header": header})
                await call_json(ws, "write_files", {"files": {
                    "README.md": render_readme(spec, design, report),
                    "requirements.txt": "pytest>=7\n"}})
                result.files = (await call_json(ws, "list_files"))["files"]
        except Exception as exc:  # report any failure to the UI instead of crashing
            root_cause = _first_leaf(exc)
            result.error = f"{type(root_cause).__name__}: {root_cause}"
            if "Connect" in result.error or "refused" in result.error:
                result.error += (f"  -> Is the model server running at {self.settings.resolved_base_url()}?"
                                 " (try: ollama serve)")
            self.emit("orchestrator", result.error, level="error")

        result.usage = self.usage.report()
        self._save(job_dir, result, description, requirements)
        verdict = "all tests pass" if result.success else "finished with failing tests" if not result.error else "failed"
        self.emit("orchestrator", f"Done: {verdict}. {self.usage.total_calls} API calls, "
                  f"{self.usage.total_tokens} tokens.", level="success" if result.success else "warn")
        return result

    async def _debug_loop(self, ws, debugger, spec: dict, root: str, result: RunResult) -> dict:
        """Run the tests and let the Debugger fix failures, keeping only progress.

        Before every Debugger round the workspace is snapshotted. If the round
        makes the test results worse, the snapshot is restored; if it does not
        make them better, the loop stops, because another round on the same
        failures would most likely spend tokens for nothing. Each round is
        recorded in `result.rounds` for the UI. Returns the report that matches
        the code left in the workspace.
        """
        best = await self._run_tests(ws)
        result.rounds.append(_round_row(0, best, "initial run"))
        while not best["success"] and result.fix_rounds < self.settings.max_fix_rounds:
            result.fix_rounds += 1
            n = result.fix_rounds
            self.emit("debugger", f"Fix round {n}: {best['summary']}", level="working")
            await call_json(ws, "snapshot", {"name": "before_fix"})
            fix = await self._call_agent(debugger, "debugger", "fix_failures",
                                         {"spec": spec, "workspace": root,
                                          "test_output": best["output"],
                                          "max_chars": self.settings.max_report_chars})
            self.emit("debugger", "Changed: " + (", ".join(fix["changed"]) or "nothing"))
            if not fix["changed"]:
                result.rounds.append(_round_row(n, best, "no changes, stopped"))
                break
            report = await self._run_tests(ws)
            if _score(report) > _score(best):
                best = report
                result.rounds.append(_round_row(n, report, "improved, kept"))
                continue
            if _score(report) < _score(best):
                await call_json(ws, "restore", {"name": "before_fix"})
                result.rounds.append(_round_row(n, report, "worse, rolled back"))
                self.emit("orchestrator", f"Fix round {n} made tests worse ({report['summary']}); "
                          "restored the previous code", level="warn")
            else:
                result.rounds.append(_round_row(n, report, "no improvement, stopped"))
                best = report
                self.emit("orchestrator", f"Fix round {n} did not improve the tests; stopping early "
                          "to save tokens", level="warn")
            break
        return best

    async def _run_tests(self, ws) -> dict:
        """Run pytest through the workspace MCP server and report the result."""
        self.emit("workspace", "Running pytest...", level="working")
        report = await call_json(ws, "run_tests", {"timeout": self.settings.test_timeout})
        self.emit("workspace", f"pytest: {report['summary']}",
                  level="success" if report["success"] else "warn")
        return report

    def _save(self, job_dir: Path, result: RunResult, description: str, requirements: str) -> None:
        """Save spec, design, usage report, test output and a ZIP of the project."""
        (job_dir / "input.json").write_text(json.dumps(
            {"description": description, "requirements": requirements}, indent=2), encoding="utf-8")
        (job_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        (job_dir / "spec.json").write_text(json.dumps(result.spec, indent=2), encoding="utf-8")
        (job_dir / "design.json").write_text(json.dumps(result.design, indent=2), encoding="utf-8")
        (job_dir / "test_output.txt").write_text(result.test_report.get("output", ""), encoding="utf-8")
        self.usage.save(job_dir / "usage_report.json")
        self.usage.append_history(LOG_DIR / "usage_history.jsonl", result.job_id, {
            "success": result.success, "model": self.settings.model, "provider": self.settings.provider,
            "fix_rounds": result.fix_rounds, "tests": result.test_report.get("summary", "")})
        shutil.make_archive(str(job_dir / "generated_project"), "zip", root_dir=result.project_dir)


def _score(report: dict) -> tuple:
    """Rank test reports: all passing first, then more passes, then fewer failures."""
    return (bool(report.get("success")), report.get("passed", 0),
            -(report.get("failed", 0) + report.get("errors", 0)))


def _round_row(number: int, report: dict, outcome: str) -> dict:
    """One row of the per-round test history shown in the UI."""
    return {"round": number, "passed": report.get("passed", 0), "failed": report.get("failed", 0),
            "errors": report.get("errors", 0), "summary": report.get("summary", ""), "outcome": outcome}


def _first_leaf(exc: BaseException) -> BaseException:
    """Unwrap (nested) ExceptionGroups raised by the MCP SDK's task groups."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def render_readme(spec: dict, design: dict, report: dict) -> str:
    """README for the generated project, built from a template (no LLM tokens)."""
    features = "\n".join(f"- **{f.get('id', '')} {f.get('name', '')}**: {f.get('details', '')}"
                         for f in spec.get("features", []) if isinstance(f, dict))
    files = "\n".join(f"- `{f.get('path')}`: {f.get('purpose', '')}"
                      for f in design.get("files", []) if isinstance(f, dict))
    run = design.get("run_command") or "python main.py --help"
    return (f"# {spec.get('app_name', 'Generated application')}\n\n{spec.get('summary', '')}\n\n"
            f"## Features\n{features}\n\n## Files\n{files}\n\n"
            f"## Run\n```bash\n{run}\n```\n\n## Test\n```bash\npip install -r requirements.txt\n"
            f"python -m pytest -q tests\n```\n\nLast test run: {report.get('summary', 'n/a')}\n\n"
            "_Generated by AI Coder (multi-agent MCP system)._\n")


def run_sync(description: str, requirements: str, settings: Settings | None = None,
             on_event: Callable[[dict], None] | None = None, job_id: str | None = None) -> RunResult:
    """Blocking wrapper used by the CLI and the web UI's worker thread."""
    return asyncio.run(Orchestrator(settings, on_event).run(description, requirements, job_id))
