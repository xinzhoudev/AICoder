# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: tests for workspace tools and LLM clients.
"""Unit tests for `ai_coder.workspace` and `ai_coder.llm_client`."""

import httpx
import pytest

from ai_coder.config import Settings
from ai_coder.llm_client import LLMError, MockClient, OllamaClient, OpenAICompatClient, make_client
from ai_coder.workspace import Workspace, parse_pytest_counts, safe_test_env


def test_write_read_list_and_path_escape(tmp_path):
    ws = Workspace(tmp_path)
    ws.write_files({"pkg/a.py": "A = 1\n", "README.md": "hi"})
    assert ws.list_files() == ["README.md", "pkg/a.py"]
    assert ws.read_files(["pkg/a.py", "missing.py"]) == {"pkg/a.py": "A = 1\n"}
    with pytest.raises(ValueError):
        ws.write_files({"../outside.py": "x"})


def test_check_syntax_reports_errors(tmp_path):
    ws = Workspace(tmp_path)
    ws.write_files({"good.py": "x = 1\n", "bad.py": "def f(:\n"})
    errors = ws.check_syntax()
    assert [e["path"] for e in errors] == ["bad.py"]


def test_extract_api_lists_public_signatures(tmp_path):
    ws = Workspace(tmp_path)
    ws.write_files({"pkg/m.py": (
        '"""Mod."""\nLIMIT = 5\n\nclass Store:\n    """A store."""\n    def __init__(self, path: str = ":memory:"):\n'
        '        pass\n    def get(self, key: int) -> str:\n        """Get one."""\n        return ""\n'
        '    def _hidden(self):\n        pass\n\ndef helper(x, y=2) -> int:\n    return x\n'),
        "tests/test_x.py": "def test_a(): pass\n"})
    api = ws.extract_api()
    assert "class Store():  # A store." in api
    assert "def get(self, key: int) -> str  # Get one." in api
    assert "def helper(x, y=2) -> int" in api and "LIMIT = 5" in api
    assert "_hidden" not in api and "test_a" not in api


def test_run_tests_on_reference_project(reference_project):
    report = Workspace(reference_project).run_tests(timeout=120)
    assert report["success"] and report["failed"] == 0 and report["passed"] >= 20


def test_add_headers_is_idempotent(tmp_path):
    ws = Workspace(tmp_path)
    ws.write_files({"a.py": "x = 1\n"})
    header = "# Author: Me (Student ID: 1)\n"
    assert ws.add_headers(header) == ["a.py"]
    assert ws.add_headers(header) == []
    assert ws.read_file("a.py").startswith("# Author: Me")


def test_parse_pytest_counts():
    counts = parse_pytest_counts("....F\n=== 1 failed, 4 passed, 2 errors in 0.3s ===")
    assert (counts["passed"], counts["failed"], counts["errors"]) == (4, 1, 2)
    assert parse_pytest_counts("collection error")["passed"] == 0


def test_make_client_picks_provider():
    assert isinstance(make_client(Settings(provider="mock")), MockClient)
    assert isinstance(make_client(Settings(provider="openai")), OpenAICompatClient)
    assert isinstance(make_client(Settings(provider="ollama")), OllamaClient)


def test_ollama_client_reads_token_counts(monkeypatch):
    def fake_post(url, json, timeout, **kw):
        assert url.endswith("/api/chat") and json["options"]["num_ctx"] > 0
        return httpx.Response(200, json={"message": {"content": "hello"}, "prompt_eval_count": 12,
                                         "eval_count": 3}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)
    text, record = OllamaClient(Settings(provider="ollama")).chat("coder", "sys", "user")
    assert text == "hello" and record.prompt_tokens == 12 and record.completion_tokens == 3
    assert not record.estimated


def test_openai_client_reads_usage_and_errors(monkeypatch):
    def fake_post(url, json, headers, timeout):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)
    _, record = OpenAICompatClient(Settings(provider="openai")).chat("tester", "s", "u")
    assert record.total_tokens == 9

    def failing_post(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "post", failing_post)
    with pytest.raises(LLMError):
        OpenAICompatClient(Settings(provider="openai", max_retries=0)).chat("tester", "s", "u")


def test_openai_client_retries_rate_limit_and_counts_reasoning(monkeypatch):
    """Gemini-style: first a 429, then a reply whose total includes thinking tokens."""
    replies = [
        httpx.Response(429, headers={"retry-after": "1"}, json={"error": "quota"}),
        httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 40}}),
    ]
    sleeps, payloads = [], []

    def fake_post(url, json, headers, timeout):
        payloads.append(json)
        resp = replies.pop(0)
        resp.request = httpx.Request("POST", url)
        return resp
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("ai_coder.llm_client.time.sleep", sleeps.append)
    settings = Settings(provider="openai", model="gemini-x", reasoning_effort="low",
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    text, record = OpenAICompatClient(settings).chat("coder", "s", "u")
    assert text == "ok" and sleeps == [1.0]
    assert payloads[0]["reasoning_effort"] == "low"
    assert record.prompt_tokens == 10 and record.completion_tokens == 30


def test_safe_test_env_drops_secrets():
    env = safe_test_env({"LLM_API_KEY": "sk-1", "GEMINI_API_KEY": "g", "GITHUB_TOKEN": "t",
                         "DB_PASSWORD": "p", "PATH": "/bin", "AUTHOR_NAME": "Me", "LLM_MODEL": "m"})
    assert env == {"PATH": "/bin", "AUTHOR_NAME": "Me", "LLM_MODEL": "m"}


def test_generated_tests_cannot_read_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-secret")
    ws = Workspace(tmp_path)
    ws.write_files({"tests/test_env.py": (
        "import os\n\ndef test_no_key():\n    assert 'LLM_API_KEY' not in os.environ\n")})
    report = ws.run_tests(timeout=60)
    assert report["success"], report["output"]


def test_snapshot_and_restore(tmp_path):
    ws = Workspace(tmp_path)
    ws.write_files({"a.py": "x = 1\n", "pkg/b.py": "y = 2\n"})
    assert ws.snapshot("s") == 2
    ws.write_files({"a.py": "x = 99\n", "new.py": "z = 3\n"})
    (tmp_path / "pkg" / "b.py").unlink()
    assert ws.restore("s") == ["a.py", "new.py", "pkg/b.py"]
    assert ws.read_files(["a.py", "pkg/b.py"]) == {"a.py": "x = 1\n", "pkg/b.py": "y = 2\n"}
    assert ws.list_files() == ["a.py", "pkg/b.py"]
    assert ws.restore("s") == []
    with pytest.raises(KeyError):
        ws.restore("missing")
