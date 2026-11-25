# Author: Xin Zhou (Student ID: 42742822)
# Reference implementation used as canned output by the offline mock LLM.
"""SQLite persistence plus CSV import/export."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import FeedbackForm, Question, Response, validate_response

SCHEMA = """
CREATE TABLE IF NOT EXISTS forms (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, text TEXT NOT NULL, qtype TEXT NOT NULL, options TEXT, required INTEGER);
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
    channel TEXT NOT NULL, respondent_email TEXT, submitted_at TEXT NOT NULL, answers TEXT NOT NULL);
"""


class FeedbackStore:
    """Stores forms and responses in a SQLite database."""

    def __init__(self, db_path: str = ":memory:") -> None:
        """Open (or create) the database at `db_path`."""
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    # ----- forms -------------------------------------------------------
    def create_form(self, form: FeedbackForm) -> FeedbackForm:
        """Insert a form and its questions; returns the form with ids set."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO forms (title, description, active, created_at) VALUES (?, ?, ?, ?)",
                (form.title.strip(), form.description, int(form.active), form.created_at.isoformat()))
            form.id = cur.lastrowid
            for pos, q in enumerate(form.questions):
                qcur = self.conn.execute(
                    "INSERT INTO questions (form_id, position, text, qtype, options, required) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (form.id, pos, q.text, q.qtype, json.dumps(q.options), int(q.required)))
                q.id = qcur.lastrowid
        return form

    def get_form(self, form_id: int) -> FeedbackForm:
        """Load a form by id or raise KeyError."""
        row = self.conn.execute("SELECT * FROM forms WHERE id = ?", (form_id,)).fetchone()
        if row is None:
            raise KeyError(f"Form {form_id} not found")
        qrows = self.conn.execute("SELECT * FROM questions WHERE form_id = ? ORDER BY position",
                                  (form_id,)).fetchall()
        questions = [Question(text=q["text"], qtype=q["qtype"], options=json.loads(q["options"] or "[]"),
                              required=bool(q["required"]), id=q["id"]) for q in qrows]
        return FeedbackForm(title=row["title"], description=row["description"] or "", questions=questions,
                            active=bool(row["active"]), id=row["id"],
                            created_at=datetime.fromisoformat(row["created_at"]))

    def list_forms(self) -> list[FeedbackForm]:
        """All forms, oldest first."""
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM forms ORDER BY id")]
        return [self.get_form(i) for i in ids]

    def update_form(self, form_id: int, title: str | None = None, description: str | None = None,
                    active: bool | None = None) -> FeedbackForm:
        """Change a form's title, description or active flag."""
        form = self.get_form(form_id)
        if title is not None:
            if not title.strip():
                raise ValueError("Form title must not be empty")
            form.title = title.strip()
        if description is not None:
            form.description = description
        if active is not None:
            form.active = bool(active)
        with self.conn:
            self.conn.execute("UPDATE forms SET title = ?, description = ?, active = ? WHERE id = ?",
                              (form.title, form.description, int(form.active), form_id))
        return form

    def delete_form(self, form_id: int) -> None:
        """Delete a form with its questions and responses."""
        self.get_form(form_id)
        with self.conn:
            self.conn.execute("DELETE FROM forms WHERE id = ?", (form_id,))

    # ----- responses ---------------------------------------------------
    def submit_response(self, form_id: int, answers: dict, channel: str = "web",
                        respondent_email: str | None = None,
                        submitted_at: datetime | None = None) -> Response:
        """Validate and store a response."""
        form = self.get_form(form_id)
        clean = validate_response(form, answers, channel, respondent_email)
        when = submitted_at or datetime.now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO responses (form_id, channel, respondent_email, submitted_at, answers) "
                "VALUES (?, ?, ?, ?, ?)",
                (form_id, channel, respondent_email, when.isoformat(), json.dumps(clean)))
        return Response(form_id=form_id, answers=clean, channel=channel,
                        respondent_email=respondent_email, submitted_at=when, id=cur.lastrowid)

    def list_responses(self, form_id: int, start: datetime | None = None,
                       end: datetime | None = None) -> list[Response]:
        """Responses for a form, optionally within [start, end], oldest first."""
        self.get_form(form_id)
        rows = self.conn.execute("SELECT * FROM responses WHERE form_id = ? ORDER BY submitted_at, id",
                                 (form_id,)).fetchall()
        result = []
        for r in rows:
            when = datetime.fromisoformat(r["submitted_at"])
            if (start and when < start) or (end and when > end):
                continue
            answers = {int(k): v for k, v in json.loads(r["answers"]).items()}
            result.append(Response(form_id=form_id, answers=answers, channel=r["channel"],
                                   respondent_email=r["respondent_email"], submitted_at=when, id=r["id"]))
        return result

    # ----- CSV ---------------------------------------------------------
    def export_csv(self, form_id: int, path: str | Path) -> int:
        """Write a form's responses to CSV; returns the number of rows."""
        form = self.get_form(form_id)
        responses = self.list_responses(form_id)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["submitted_at", "channel", "respondent_email"] + [f"q{q.id}" for q in form.questions])
            for r in responses:
                writer.writerow([r.submitted_at.isoformat(), r.channel, r.respondent_email or ""]
                                + [r.answers.get(q.id, "") for q in form.questions])
        return len(responses)

    def import_csv(self, form_id: int, path: str | Path) -> int:
        """Import responses from a CSV written by `export_csv`; returns rows imported."""
        count = 0
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                answers = {k: v for k, v in row.items() if k and k.startswith("q") and v != ""}
                when = datetime.fromisoformat(row["submitted_at"]) if row.get("submitted_at") else None
                self.submit_response(form_id, answers, channel=row.get("channel") or "web",
                                     respondent_email=row.get("respondent_email") or None, submitted_at=when)
                count += 1
        return count
