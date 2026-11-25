# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: Flask web UI.
"""Web UI.

The page takes a software description and requirements, starts a
generation job in a background thread, streams the agents' progress, and
returns the generated code, the generated tests, the pytest result and the
LLM usage report (API calls and tokens). The project can be downloaded as a
ZIP.

Run:  python run_ui.py   then open http://127.0.0.1:5000
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from ai_coder.config import LOG_DIR, OUTPUT_DIR, SPECS_DIR, get_settings
from ai_coder.orchestrator import run_sync
from ai_coder.parsing import split_spec
from ai_coder.workspace import Workspace

app = Flask(__name__)
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def load_example() -> dict:
    """Read the Customer Feedback Tracker spec used to pre-fill the form."""
    path = SPECS_DIR / "customer_feedback_tracker.md"
    if not path.exists():
        return {"description": "", "requirements": ""}
    description, requirements = split_spec(path.read_text(encoding="utf-8"))
    return {"description": description, "requirements": requirements}


def _worker(job_id: str, description: str, requirements: str) -> None:
    """Background thread: run the pipeline and store events and result."""
    job = JOBS[job_id]

    def on_event(event: dict) -> None:
        """Append a progress event to the job (read by the polling UI)."""
        with JOBS_LOCK:
            job["events"].append(event)
            job["live_usage"] = {"api_calls": event.get("api_calls", 0), "tokens": event.get("tokens", 0)}

    try:
        result = run_sync(description, requirements, on_event=on_event, job_id=job_id)
        with JOBS_LOCK:
            job["result"] = result.to_dict()
            job["status"] = "done" if result.success else ("error" if result.error else "failed_tests")
    except Exception as exc:  # last-resort guard so the UI never hangs
        with JOBS_LOCK:
            job["status"] = "error"
            job["events"].append({"time": time.strftime("%H:%M:%S"), "agent": "orchestrator",
                                  "message": f"{type(exc).__name__}: {exc}", "level": "error"})


def _project_dir(job_id: str) -> Path:
    """Folder of a job's generated project (404 if unknown)."""
    folder = (OUTPUT_DIR / job_id / "project").resolve()
    if OUTPUT_DIR.resolve() not in folder.parents or not folder.is_dir():
        abort(404)
    return folder


@app.get("/")
def index():
    """Main page."""
    return render_template("index.html")


@app.get("/api/config")
def config():
    """Model settings and the example spec for the UI."""
    return jsonify({"settings": get_settings().public_dict(), "example": load_example()})


@app.post("/api/generate")
def generate():
    """Start a generation job; returns its id."""
    data = request.get_json(force=True, silent=True) or {}
    description = (data.get("description") or "").strip()
    requirements = (data.get("requirements") or "").strip()
    if not description:
        return jsonify({"error": "Please enter a software description."}), 400
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "events": [], "result": None,
                        "live_usage": {"api_calls": 0, "tokens": 0}, "started": time.time()}
    threading.Thread(target=_worker, args=(job_id, description, requirements), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    """Job status, events since `?since=N`, and the result when finished."""
    job = JOBS.get(job_id)
    if job is None:
        abort(404)
    since = request.args.get("since", default=0, type=int)
    with JOBS_LOCK:
        payload = {"status": job["status"], "events": job["events"][since:], "event_count": len(job["events"]),
                   "live_usage": job["live_usage"], "elapsed_s": round(time.time() - job["started"], 1)}
        result = job["result"]
    if result:
        result = dict(result)
        result["test_report"] = {k: v for k, v in result.get("test_report", {}).items()}
        payload["result"] = result
    return jsonify(payload)


@app.get("/api/jobs/<job_id>/file")
def job_file(job_id: str):
    """Text of one generated file."""
    ws = Workspace(_project_dir(job_id))
    try:
        return jsonify({"path": request.args.get("path", ""), "content": ws.read_file(request.args.get("path", ""))})
    except (ValueError, OSError):
        abort(404)


@app.post("/api/jobs/<job_id>/run-tests")
def rerun_tests(job_id: str):
    """Run the generated tests again (e.g. after editing a file by hand)."""
    return jsonify(Workspace(_project_dir(job_id)).run_tests(timeout=get_settings().test_timeout))


@app.get("/api/jobs/<job_id>/download")
def download(job_id: str):
    """ZIP of the generated project."""
    _project_dir(job_id)
    archive = OUTPUT_DIR / job_id / "generated_project.zip"
    if not archive.exists():
        abort(404)
    return send_file(archive, as_attachment=True, download_name=f"generated_project_{job_id}.zip")


@app.get("/api/history")
def history():
    """Usage summary of previous runs (newest first)."""
    path = LOG_DIR / "usage_history.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return jsonify(list(reversed(rows))[:20])
