# AI Coder: a multi-agent code generator over MCP

SWE 270P Final Project. Team: Xin Zhou (Student ID: 42742822), Yuxiao Xiao (Student ID: 62185048).

AI Coder takes a **software description plus requirements** from a web UI and returns a
**runnable Python application and its pytest test cases**. Five LLM agents, each an
independent **Model Context Protocol (MCP) server**, collaborate through an orchestrator
(an MCP client) and a shared workspace MCP server. Every LLM call is counted, and each run
produces a report of **API calls and tokens** per agent.

Chosen software: **Customer Feedback Tracker** (`specs/customer_feedback_tracker.md`).

---

## 1. Architecture

```
                         ┌──────────────────────────────┐
  Browser UI  ──HTTP──▶  │  Flask UI  (ai_coder/ui)     │
                         └──────────────┬───────────────┘
                                        │ background thread
                         ┌──────────────▼───────────────┐
                         │  Orchestrator  (MCP client)  │──▶ usage_report.json
                         └─┬─────┬─────┬─────┬─────┬────┘
                     stdio │MCP  │     │     │     │      stdio MCP
        ┌──────────────────┘     │     │     │     └──────────────────┐
        ▼                        ▼     ▼     ▼                        ▼
  ┌──────────┐  ┌───────────┐ ┌───────┐ ┌────────┐ ┌──────────┐  ┌───────────────┐
  │ Analyst  │  │ Architect │ │ Coder │ │ Tester │ │ Debugger │  │ Workspace     │
  │ server   │  │ server    │ │server │ │ server │ │ server   │  │ server (tools)│
  └──────────┘  └───────────┘ └───┬───┘ └───┬────┘ └────┬─────┘  └───────▲───────┘
                                  │ MCP     │ MCP       │ MCP            │
                                  └─────────┴───────────┴────────────────┘
                                  agents are MCP clients of the workspace
```

* Each agent runs in its **own process** (`python -m ai_coder.servers.agent_server --role <role>`)
  and exposes its skill as an MCP tool. The agents themselves are in `ai_coder/agents/`, one
  module per role on a shared `BaseAgent` (LLM calls, usage records, allow-listed workspace
  access, uniform `ok`/`error` replies).
* The **workspace server** (`ai_coder/servers/workspace_server.py`) is the shared
  "blackboard": agents exchange code by writing to and reading from it over MCP instead of
  passing whole files through prompts.
* Agents that work on code are **MCP clients of the workspace server** with an
  **allow-list** of tools (each agent class's `tools`, collected in `ROLE_TOOLS`; `WorkspaceClient` refuses
  anything else).

### Agents, tools and collaboration

| Agent | MCP tool it serves | Workspace tools it may call | Output |
|---|---|---|---|
| Requirements Analyst | `analyze_requirements(description, requirements)` | none | JSON spec: features + acceptance criteria |
| Software Architect | `design_architecture(spec)` | none | JSON design: files + exact public signatures |
| Coder | `generate_code(spec, design, workspace)` | `write_files`, `check_syntax`, `read_files` | source files, one self-repair round for syntax errors |
| Tester | `generate_tests(spec, workspace)` | `extract_api`, `write_files`, `check_syntax` | `tests/test_*.py` written against the real API |
| Debugger | `fix_failures(spec, workspace, test_output)` | `list_files`, `read_files`, `write_files`, `check_syntax` | corrected files |

Workspace server tools: `write_files`, `read_files`, `list_files`, `check_syntax`,
`extract_api`, `run_tests`, `add_headers`, plus `snapshot` and `restore`, which only the
orchestrator uses (no agent has them on its allow-list).

**Collaboration pattern** (sequential pipeline with a feedback loop):

```
Analyst ─▶ Architect ─▶ Coder ─▶ Tester ─▶ run_tests ──pass──▶ done
                                              │  ▲
                                         fail ▼  │ (at most MAX_FIX_ROUNDS)
                                             Debugger
```

The orchestrator is deterministic code, not another LLM: the order of steps never changes for
this task, so an LLM "manager" would only add API calls and tokens.

**Debugger regression guard.** Before each Debugger round the orchestrator snapshots the
workspace. If the round makes the tests worse, the snapshot is restored, so a bad fix never
replaces better code. If a round does not improve the result, the loop stops early instead of
spending more tokens on the same failures. Every round (passed / failed / errors / outcome) is
shown in the UI's **Test run** tab and saved in `result.json`.

**Generated code runs without secrets.** `run_tests` executes LLM-written code, so it starts
pytest with an environment from which every variable whose name contains `KEY`, `TOKEN`,
`SECRET`, `PASSWORD` or `CREDENTIAL` (e.g. `LLM_API_KEY`) has been removed.

## 2. Token and API-call efficiency

The project is graded on token usage and API calls, so the design minimises both:

1. **4 LLM calls in the best case** (Analyst, Architect, Coder, Tester); the Debugger only runs
   when tests fail, at most `MAX_FIX_ROUNDS` times (default 2), and stops early when a round
   brings no improvement.
2. **The Tester never sees full source files.** The workspace's `extract_api` tool parses the
   code with Python's `ast` and sends only signatures and first docstring lines.
3. **Compact JSON** (no whitespace) for spec and design in prompts; later agents get a trimmed
   spec (`spec_brief` in `agents/base.py`).
4. **Truncated pytest output** for the Debugger (head + tail, `MAX_REPORT_CHARS`).
5. **Targeted retries only**: the Coder re-asks just for files that were missing, and fixes only
   files that fail to compile.
6. **No LLM for boilerplate**: `__init__.py`, `conftest.py`, the generated README, author
   headers and the ZIP are produced by code.
7. **JSON mode** for Analyst and Architect to avoid failed parses and their retries: Ollama's
   `format: json`, or `response_format: json_object` on OpenAI-compatible servers (Gemini,
   vLLM, LM Studio). A server that rejects it is detected once and then called without it.

## 3. LLM usage tracking

* Every LLM request creates a `CallRecord` (agent, model, prompt tokens, completion tokens,
  latency). Token counts come from the server (`prompt_eval_count` / `eval_count` for Ollama,
  `usage` for OpenAI-compatible servers); if a server reports none, they are estimated
  (characters / 4) and flagged as estimated.
* Agent servers return their records with each MCP tool result; the orchestrator aggregates
  them in `UsageTracker` (`ai_coder/usage.py`).
* Results: live counters in the UI, a per-agent table in the **LLM usage** tab,
  `output/<job>/usage_report.json`, and a one-line-per-run history in
  `logs/usage_history.jsonl` (also shown in the UI).

## 4. Setup

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then put your name and ID in it
```

Open-source model with [Ollama](https://ollama.com) (no paid API needed):

```bash
ollama pull qwen2.5-coder:7b       # or qwen2.5-coder:14b if you have the memory
ollama serve                       # if it is not already running
```

Other OpenAI-compatible servers (LM Studio, vLLM, llama.cpp server) work with
`LLM_PROVIDER=openai` and `LLM_BASE_URL=http://host:port/v1`.

**Google Gemini API** (hosted, not open-source; check that your course allows it). Gemini has an
OpenAI-compatible endpoint, so it runs through the same `openai` client. Create a key in
[Google AI Studio](https://aistudio.google.com/apikey), then in `.env`:

```bash
LLM_PROVIDER=openai
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_API_KEY=<your key>            # GEMINI_API_KEY also works
LLM_MODEL=<model id>              # pick a "flash" model from the list below
LLM_REASONING_EFFORT=low          # thinking models spend output tokens on reasoning
```

List the model ids your key can use:

```bash
curl https://generativelanguage.googleapis.com/v1beta/openai/models -H "Authorization: Bearer $LLM_API_KEY"
```

The client retries HTTP 429 (free-tier rate limits) with back-off, and counts reasoning
("thinking") tokens in the usage report, because Gemini reports them only in `total_tokens`.
Never commit `.env`: it is already in `.gitignore`.

## 5. Run

**Web UI (for the demo):**

```bash
python run_ui.py          # open http://127.0.0.1:5000
```

1. Click **Load Customer Feedback Tracker** (it is pre-filled on first load).
2. Click **Generate code and tests**; watch each agent's stage light up and the token counter rise.
3. Browse **Source code**, **Test cases**, **Test run** (with *Run tests again*),
   **LLM usage**, **Spec & design**; click **Download project (.zip)**.

**Command line:**

```bash
python run_cli.py --spec specs/customer_feedback_tracker.md
```

**Offline mode** (no model; canned replies from a reference implementation, for testing the
pipeline and UI only):

```bash
LLM_PROVIDER=mock python run_ui.py
```

**Run the generated app:**

```bash
cd output/<job_id>/project
python -m pytest -q tests
python main.py --help
```

### Google Colab

Open `colab/AI_Coder_Colab.ipynb` in Colab (*File → Upload notebook*) and run the cells from top to
bottom. The notebook uploads this ZIP, installs the packages, sets up Gemini (key from Colab
Secrets) or Ollama (on a T4 GPU runtime), runs the tests and the pipeline, opens the web UI
through Colab's port proxy, and downloads the results.

## 6. Requirements we defined for the Customer Feedback Tracker

Full text: `specs/customer_feedback_tracker.md`. Summary:

| ID | Requirement |
|---|---|
| FR1 | Create, list, view, update, delete feedback forms (non-empty title) |
| FR2 | Question types: rating 1-5, NPS 0-10, text, choice (≥ 2 options); required flag |
| FR3 | Validated response collection with channel, email and timestamp |
| FR4 | Email invitations with tracked link, saved to an outbox by default; optional SMTP |
| FR5 | Share links for Twitter/X, Facebook, LinkedIn |
| FR6 | SQLite persistence; CSV import/export |
| FR7 | Analytics: averages, distributions, NPS, channel breakdown, sentiment, keywords |
| FR8 | Trends per day/week/month |
| FR9 | Plain-English actionable insights |
| FR10 | SVG bar and line charts without third-party libraries; terminal charts |
| FR11 | Command-line interface with friendly errors |
| NFR1-3 | Standard library only, docstrings everywhere, offline deterministic tests |

Why a CLI and the standard library only: it keeps the generated project small enough for a
7B local model to produce reliably and its tests runnable anywhere, which is what the TA
needs to see working in the demo.

## 7. Tests for AI Coder itself

```bash
python -m pytest -q          # 40 tests, offline (mock LLM), about 15 s
```

They cover output parsing, usage tracking, workspace tools, both LLM clients (with a faked
HTTP layer), each agent's MCP tool listing, tool allow-list enforcement, the Debugger repairing
a deliberately broken file over real MCP stdio, the full pipeline, and the UI endpoints.
`tests/test_agents.py` tests each of the five agents in-process with a scripted LLM (JSON
retry and fallback, follow-up for missing files, syntax repair, API-only prompts for the
Tester, file selection for the Debugger). The suite passes on Python 3.10 and 3.12.

## 8. Project structure

```
ai_coder/
  config.py            settings from env / .env
  llm_client.py        Ollama, OpenAI-compatible (incl. Gemini) and mock clients
  usage.py             CallRecord, UsageTracker
  parsing.py           JSON / file-block extraction from model output
  prompts.py           system prompts per agent
  workspace.py         file, syntax, API-extraction and pytest logic
  mcp_client.py        MCP stdio session helpers, WorkspaceClient (allow-list)
  orchestrator.py      pipeline (MCP client)
  agents/              BaseAgent + analyst, architect, coder, tester, debugger (one module each)
  servers/agent_server.py      MCP server entry point, one process per agent role
  servers/workspace_server.py  workspace MCP server
  ui/app.py, ui/templates/index.html   web UI
  mock_data/           reference Customer Feedback Tracker used by the mock LLM
specs/                 input descriptions and requirements
tests/                 tests for AI Coder
run_ui.py, run_cli.py  entry points
colab/                 Google Colab notebook
output/<job_id>/       generated project, ZIP, spec, design, usage report, test output
```

## 9. Third-party code and AI assistance

* Libraries: the official MCP Python SDK (`mcp`, FastMCP API), Flask, httpx, pytest.
  No code was copied from other open-source projects.
* AI assistance: Yuxiao Xiao's commits were written with the help of Claude Code
  (Anthropic): the five agents in `ai_coder/agents/` and `servers/agent_server.py`, their
  tests in `tests/test_agents.py`, and the other commits by Yuxiao (each carries a
  `Co-Authored-By: Claude` line). Files written this way say so in their header.
* `<Xin Zhou: state here, as the course policy requires, whether AI tools were used for the
  rest of the code and which parts.>`
