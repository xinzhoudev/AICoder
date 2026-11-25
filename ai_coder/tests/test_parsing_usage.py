# Author: Xin Zhou (Student ID: 42742822)
# SWE 270P Final Project - AI Coder: tests for output parsing and usage tracking.
"""Unit tests for `ai_coder.parsing` and `ai_coder.usage`."""

import json

import pytest

from ai_coder.parsing import extract_files, extract_json, split_spec, truncate
from ai_coder.usage import CallRecord, UsageTracker, estimate_tokens


def test_extract_files_primary_format():
    """Files are parsed from the === FILE === block format."""
    text = ("Sure!\n=== FILE: pkg/a.py ===\nx = 1\n=== END FILE ===\n"
            "=== FILE: ./pkg/b.py ===\n```python\ny = 2\n```\n=== END FILE ===")
    files = extract_files(text)
    assert files == {"pkg/a.py": "x = 1\n", "pkg/b.py": "y = 2\n"}


def test_extract_files_markdown_heading_fallback():
    """Files are parsed from markdown headings with code fences."""
    text = "### pkg/models.py\n```python\nclass A:\n    pass\n```\n\n**main.py**\n```python\nprint(1)\n```"
    files = extract_files(text)
    assert set(files) == {"pkg/models.py", "main.py"}


def test_extract_files_comment_fallback_and_path_safety():
    """Files are parsed from path comments, and unsafe paths are dropped."""
    text = "```python\n# file: app/x.py\nX = 1\n```\n=== FILE: ../evil.py ===\nbad\n=== END FILE ==="
    files = extract_files(text)
    assert "../evil.py" not in files and "evil.py" not in files


def test_extract_json_variants():
    """JSON is extracted from plain, fenced and surrounded replies."""
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json('Here is the spec: {"x": {"y": 2}} hope it helps') == {"x": {"y": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_truncate_keeps_head_and_tail():
    """Long pytest output keeps its beginning and end."""
    text = "HEAD" + "x" * 1000 + "TAIL"
    short = truncate(text, 100)
    assert short.startswith("HEAD") and short.endswith("TAIL") and len(short) < 150
    assert truncate("short", 100) == "short"


def test_split_spec():
    """A spec file is split into description and requirements."""
    desc, reqs = split_spec("# T\n\n## Description\nDoes things.\n\n## Requirements\nFR1 x")
    assert desc == "Does things." and "FR1 x" in reqs


def test_usage_tracker_aggregates(tmp_path):
    """The usage tracker totals calls and tokens per agent and saves reports and history."""
    tracker = UsageTracker()
    tracker.add(CallRecord("coder", "m", 100, 50, 1.0))
    tracker.extend([CallRecord("coder", "m", 10, 5, 0.5).to_dict(),
                    CallRecord("tester", "m", 20, 10, 0.2, estimated=True).to_dict()])
    assert tracker.total_calls == 3 and tracker.total_tokens == 195
    by = tracker.by_agent()
    assert by["coder"]["calls"] == 2 and by["coder"]["total_tokens"] == 165
    report = tracker.report()
    assert report["any_estimated"] is True and report["total_prompt_tokens"] == 130
    tracker.save(tmp_path / "u.json")
    assert json.loads((tmp_path / "u.json").read_text())["total_api_calls"] == 3
    tracker.append_history(tmp_path / "h.jsonl", "job1")
    tracker.append_history(tmp_path / "h.jsonl", "job2")
    assert len((tmp_path / "h.jsonl").read_text().splitlines()) == 2


def test_estimate_tokens():
    """Token estimates are about four characters per token."""
    assert estimate_tokens("") == 0 and estimate_tokens("abcd" * 10) == 10
