# Author: Xin Zhou (Student ID: 42742822)
# SWE 270P Final Project - AI Coder: shared pytest fixtures.
"""Fixtures for the AI Coder system tests. All tests use the offline mock LLM."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    """Force the mock provider so tests never need a running model."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    yield


@pytest.fixture
def reference_project(tmp_path):
    """A workspace pre-filled with the reference Customer Feedback Tracker and its tests."""
    import shutil

    mock = ROOT / "ai_coder" / "mock_data"
    target = tmp_path / "project"
    shutil.copytree(mock / "project", target)
    shutil.copytree(mock / "tests", target / "tests")
    (target / "tests" / "__init__.py").write_text("")
    return target


def pytest_configure(config):
    """Keep generated runs from the tests out of the real output folder."""
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
