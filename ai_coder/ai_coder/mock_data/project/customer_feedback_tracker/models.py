# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# Reference implementation used as canned output by the offline mock LLM.
"""Domain models and validation for the Customer Feedback Tracker."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

QUESTION_TYPES = ("rating", "nps", "text", "choice")
CHANNELS = ("email", "twitter", "facebook", "linkedin", "web")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def is_valid_email(email: str) -> bool:
    """Return True if `email` looks like a valid address."""
    return bool(email) and bool(EMAIL_RE.match(email.strip()))


@dataclass
class Question:
    """One question on a feedback form."""

    text: str
    qtype: str = "rating"
    options: list[str] = field(default_factory=list)
    required: bool = True
    id: int | None = None

    def __post_init__(self) -> None:
        """Validate the question as soon as it is created."""
        self.validate()

    def validate(self) -> None:
        """Raise ValueError if the question definition is invalid."""
        if not self.text or not self.text.strip():
            raise ValueError("Question text must not be empty")
        if self.qtype not in QUESTION_TYPES:
            raise ValueError(f"Unknown question type '{self.qtype}'; use one of {QUESTION_TYPES}")
        if self.qtype == "choice" and len(self.options) < 2:
            raise ValueError("A choice question needs at least two options")

    def validate_answer(self, value):
        """Check one answer against this question and return the normalised value."""
        if self.qtype in ("rating", "nps"):
            low, high = (1, 5) if self.qtype == "rating" else (0, 10)
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"Answer to '{self.text}' must be a number from {low} to {high}") from None
            if not low <= number <= high:
                raise ValueError(f"Answer to '{self.text}' must be between {low} and {high}")
            return number
        if self.qtype == "choice":
            if value not in self.options:
                raise ValueError(f"'{value}' is not an option for '{self.text}'")
            return value
        return str(value).strip()

    def to_dict(self) -> dict:
        """Serialise to a plain dict."""
        return {"id": self.id, "text": self.text, "type": self.qtype,
                "options": list(self.options), "required": self.required}

    @classmethod
    def from_dict(cls, data: dict) -> "Question":
        """Build a question from a dict (accepts 'type' or 'qtype')."""
        return cls(text=data.get("text", ""), qtype=data.get("type", data.get("qtype", "rating")),
                   options=list(data.get("options", [])), required=bool(data.get("required", True)),
                   id=data.get("id"))


@dataclass
class FeedbackForm:
    """A feedback form made of ordered questions."""

    title: str
    description: str = ""
    questions: list[Question] = field(default_factory=list)
    active: bool = True
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        """Validate the title."""
        if not self.title or not self.title.strip():
            raise ValueError("Form title must not be empty")

    def question(self, question_id: int) -> Question:
        """Return the question with `question_id` or raise KeyError."""
        for q in self.questions:
            if q.id == question_id:
                return q
        raise KeyError(f"Question {question_id} not found in form {self.id}")

    def questions_of_type(self, qtype: str) -> list[Question]:
        """All questions of one type, in form order."""
        return [q for q in self.questions if q.qtype == qtype]

    def to_dict(self) -> dict:
        """Serialise to a plain dict."""
        return {"id": self.id, "title": self.title, "description": self.description,
                "active": self.active, "created_at": self.created_at.isoformat(timespec="seconds"),
                "questions": [q.to_dict() for q in self.questions]}

    @classmethod
    def from_dict(cls, data: dict) -> "FeedbackForm":
        """Build a form (and its questions) from a dict, e.g. a JSON file."""
        return cls(title=data.get("title", ""), description=data.get("description", ""),
                   questions=[Question.from_dict(q) for q in data.get("questions", [])],
                   active=bool(data.get("active", True)))


@dataclass
class Response:
    """One customer's submitted answers."""

    form_id: int
    answers: dict[int, object]
    channel: str = "web"
    respondent_email: str | None = None
    submitted_at: datetime = field(default_factory=datetime.now)
    id: int | None = None


def validate_response(form: FeedbackForm, answers: dict, channel: str = "web",
                      respondent_email: str | None = None) -> dict[int, object]:
    """Validate a submission against `form` and return answers keyed by int id."""
    if not form.active:
        raise ValueError(f"Form {form.id} is not accepting responses")
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel '{channel}'; use one of {CHANNELS}")
    if respondent_email and not is_valid_email(respondent_email):
        raise ValueError(f"Invalid email address: {respondent_email}")
    clean: dict[int, object] = {}
    for key, value in answers.items():
        qid = int(str(key).lstrip("q"))
        question = form.question(qid)
        if value is None or value == "":
            continue
        clean[qid] = question.validate_answer(value)
    for q in form.questions:
        if q.required and q.id not in clean:
            raise ValueError(f"Missing answer to required question '{q.text}'")
    return clean
