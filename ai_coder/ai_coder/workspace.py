# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: workspace operations (files, syntax, API, tests).
"""Operations on a generated project's folder.

These plain functions hold the logic; `servers/workspace_server.py` exposes
them as MCP tools. Keeping them separate makes them easy to unit test.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv"}


class Workspace:
    """A sandboxed project directory. All paths are relative to `root`."""

    def __init__(self, root: str | Path) -> None:
        """Create the workspace (and its folder) at `root`."""
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, rel_path: str) -> Path:
        """Resolve a relative path and refuse anything outside the root."""
        target = (self.root / rel_path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"Path escapes workspace: {rel_path}")
        return target

    def write_files(self, files: dict[str, str]) -> list[str]:
        """Write several files at once; returns the written relative paths."""
        written = []
        for rel, content in files.items():
            path = self._safe(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(rel)
        return written

    def read_file(self, rel_path: str) -> str:
        """Return the text of one file."""
        return self._safe(rel_path).read_text(encoding="utf-8")

    def read_files(self, paths: list[str]) -> dict[str, str]:
        """Return {path: text} for every path that exists."""
        result = {}
        for rel in paths:
            path = self._safe(rel)
            if path.is_file():
                result[rel] = path.read_text(encoding="utf-8")
        return result

    def list_files(self) -> list[str]:
        """List all files (relative, sorted), skipping caches."""
        files = []
        for path in self.root.rglob("*"):
            if path.is_file() and not (set(path.relative_to(self.root).parts) & SKIP_DIRS):
                files.append(path.relative_to(self.root).as_posix())
        return sorted(files)

    def python_files(self, include_tests: bool = True) -> list[str]:
        """List .py files, optionally excluding the tests/ folder."""
        return [f for f in self.list_files() if f.endswith(".py")
                and (include_tests or not f.startswith("tests/"))]

    def check_syntax(self) -> list[dict]:
        """Compile every .py file; return a list of {path, error} for failures."""
        errors = []
        for rel in self.python_files():
            source = self.read_file(rel)
            try:
                compile(source, rel, "exec")
            except SyntaxError as exc:
                errors.append({"path": rel, "error": f"line {exc.lineno}: {exc.msg}"})
        return errors

    def extract_api(self) -> str:
        """Summarise the public API of all non-test modules (signatures plus
        first docstring line). This is far cheaper in tokens than sending
        whole files to the Tester agent."""
        lines: list[str] = []
        for rel in self.python_files(include_tests=False):
            try:
                tree = ast.parse(self.read_file(rel))
            except SyntaxError as exc:
                lines.append(f"# {rel}: SYNTAX ERROR {exc}")
                continue
            module = rel[:-3].replace("/", ".")
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")]
            lines.append(f"# module {module}  ({rel})")
            for node in tree.body:
                lines.extend(_describe(node, indent=""))
            lines.append("")
        return "\n".join(lines).strip()

    def run_tests(self, timeout: int = 120) -> dict:
        """Run pytest in the workspace and return counts plus output."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.root) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--color=no",
               "-p", "no:cacheprovider", "tests"]
        try:
            proc = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True,
                                  text=True, timeout=timeout)
            output = proc.stdout + proc.stderr
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            output += f"\nTIMEOUT: tests did not finish within {timeout}s"
            exit_code = -1
        counts = parse_pytest_counts(output)
        counts.update({"exit_code": exit_code, "output": output,
                       "success": exit_code == 0 and counts["passed"] > 0})
        return counts

    def add_headers(self, header: str) -> list[str]:
        """Prepend `header` to every .py file that does not already have an author line."""
        changed = []
        for rel in self.python_files():
            text = self.read_file(rel)
            if not text.startswith("# Author:"):
                self._safe(rel).write_text(header.rstrip() + "\n" + text, encoding="utf-8")
                changed.append(rel)
        return changed


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render a function signature like `name(a: int, b=2) -> str`."""
    args = ast.unparse(node.args)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    return f"{prefix}{node.name}({args}){ret}"


def _doc(node) -> str:
    """First line of a node's docstring, formatted as a trailing comment."""
    doc = ast.get_docstring(node)
    return f"  # {doc.strip().splitlines()[0]}" if doc else ""


def _describe(node, indent: str) -> list[str]:
    """Describe one top-level AST node for the API summary."""
    out: list[str] = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if not node.name.startswith("_"):
            out.append(f"{indent}{_signature(node)}{_doc(node)}")
    elif isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        decorators = "".join(f"@{ast.unparse(d)} " for d in node.decorator_list)
        out.append(f"{indent}{decorators}class {node.name}({bases}):{_doc(node)}")
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                default = f" = {ast.unparse(item.value)}" if item.value is not None else ""
                out.append(f"{indent}    {item.target.id}: {ast.unparse(item.annotation)}{default}")
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not item.name.startswith("_") or item.name == "__init__":
                    deco = "".join(f"@{ast.unparse(d)} " for d in item.decorator_list)
                    out.append(f"{indent}    {deco}{_signature(item)}{_doc(item)}")
            elif isinstance(item, ast.Assign):
                names = [t.id for t in item.targets if isinstance(t, ast.Name)]
                if names and names[0].isupper():
                    out.append(f"{indent}    {names[0]} = {ast.unparse(item.value)[:80]}")
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if isinstance(target, ast.Name) and target.id.isupper() and node.value is not None:
            out.append(f"{indent}{target.id} = {ast.unparse(node.value)[:120]}")
    return out


_COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")


def parse_pytest_counts(output: str) -> dict:
    """Read the passed/failed/error counts from pytest's summary line."""
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    summary_lines = [ln for ln in output.splitlines() if re.search(r"\d+ (passed|failed|error)", ln)]
    line = summary_lines[-1] if summary_lines else ""
    for number, kind in _COUNT_RE.findall(line):
        key = "errors" if kind.startswith("error") else kind
        if key in counts:
            counts[key] = int(number)
    counts["summary"] = line.strip("= ").strip() or "no test summary (collection failed?)"
    return counts
