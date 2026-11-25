# Author: Xin Zhou (Student ID: 42742822)
# SWE 270P Final Project - AI Coder: canned replies for the offline mock LLM.
"""Canned replies used by `MockClient` (LLM_PROVIDER=mock).

The replies are built from `mock_data/`, a hand-written reference version
of the Customer Feedback Tracker, in the exact formats the real agents ask
the model for. This lets the whole MCP pipeline, the UI and the test suite
run without a model. It is a test double, not the system's real output.
"""

from __future__ import annotations

from pathlib import Path

MOCK_DIR = Path(__file__).resolve().parent / "mock_data"


def _file_blocks(folder: Path, prefix: str = "") -> str:
    """Render every file under `folder` in the === FILE === format."""
    blocks = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            rel = prefix + path.relative_to(folder).as_posix()
            blocks.append(f"=== FILE: {rel} ===\n{path.read_text(encoding='utf-8')}\n=== END FILE ===")
    return "\n\n".join(blocks)


def reply_for(agent: str, user_prompt: str) -> str:
    """Return the canned reply for one agent role."""
    if agent == "analyst":
        return (MOCK_DIR / "spec.json").read_text(encoding="utf-8")
    if agent == "architect":
        return "```json\n" + (MOCK_DIR / "design.json").read_text(encoding="utf-8") + "\n```"
    if agent == "coder":
        return _file_blocks(MOCK_DIR / "project")
    if agent == "tester":
        return _file_blocks(MOCK_DIR / "tests", prefix="tests/")
    if agent == "debugger":
        # The reference code passes its tests, so there is nothing to change;
        # returning the reference source restores anything a test corrupted.
        return _file_blocks(MOCK_DIR / "project")
    raise ValueError(f"No mock reply for agent '{agent}'")
