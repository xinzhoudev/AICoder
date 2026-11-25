# Author: Xin Zhou (Student ID: 42742822)
# Reference implementation used as canned output by the offline mock LLM.
"""Tests for models (FR1-FR3) and SQLite storage / CSV (FR6)."""

from datetime import datetime

import pytest

from customer_feedback_tracker.models import FeedbackForm, Question
from customer_feedback_tracker.storage import FeedbackStore


@pytest.fixture
def store():
    """In-memory store with one form: rating, nps, choice, optional text."""
    s = FeedbackStore(":memory:")
    s.create_form(FeedbackForm(title="Survey", description="d", questions=[
        Question("Product?", "rating"), Question("Recommend?", "nps"),
        Question("Source?", "choice", options=["Friend", "Ad"], required=False),
        Question("Comments?", "text", required=False)]))
    yield s
    s.close()


def ids(store):
    """Question ids of form 1 in order."""
    return [q.id for q in store.get_form(1).questions]


def test_create_and_get_form(store):
    """A form is created and read back with its questions (FR1)."""
    form = store.get_form(1)
    assert form.title == "Survey"
    assert [q.qtype for q in form.questions] == ["rating", "nps", "choice", "text"]


def test_empty_title_rejected():
    """A form without a title is rejected (FR1)."""
    with pytest.raises(ValueError):
        FeedbackForm(title="  ")


def test_choice_needs_two_options():
    """A choice question needs at least two options (FR2)."""
    with pytest.raises(ValueError):
        Question("Pick", "choice", options=["only"])


def test_unknown_question_type_rejected():
    """Unknown question types are rejected (FR2)."""
    with pytest.raises(ValueError):
        Question("What?", "slider")


def test_update_and_delete_form(store):
    """Forms can be updated and deleted (FR1)."""
    store.update_form(1, title="New", active=False)
    assert store.get_form(1).title == "New" and not store.get_form(1).active
    store.delete_form(1)
    with pytest.raises(KeyError):
        store.get_form(1)


def test_submit_valid_response(store):
    """A valid response is stored with channel and timestamp (FR3)."""
    r, n, c, t = ids(store)
    resp = store.submit_response(1, {r: 4, n: 9, c: "Friend", t: "Great"}, channel="twitter",
                                 respondent_email="a@b.com")
    assert resp.id is not None
    saved = store.list_responses(1)[0]
    assert saved.answers[r] == 4 and saved.channel == "twitter"


@pytest.mark.parametrize("answers_index, value", [(0, 6), (0, 0), (1, 11), (2, "Nope")])
def test_invalid_answers_rejected(store, answers_index, value):
    """Out-of-range and wrong-type answers are rejected (FR3)."""
    r, n, c, _ = ids(store)
    answers = {r: 3, n: 5}
    answers[[r, n, c][answers_index]] = value
    with pytest.raises(ValueError):
        store.submit_response(1, answers)


def test_missing_required_answer_rejected(store):
    """A missing answer to a required question is rejected (FR3)."""
    r, _, _, _ = ids(store)
    with pytest.raises(ValueError):
        store.submit_response(1, {r: 3})


def test_inactive_and_unknown_forms_reject_responses(store):
    """Inactive or unknown forms accept no responses (FR3)."""
    r, n, _, _ = ids(store)
    with pytest.raises(KeyError):
        store.submit_response(99, {r: 3, n: 5})
    store.update_form(1, active=False)
    with pytest.raises(ValueError):
        store.submit_response(1, {r: 3, n: 5})


def test_invalid_channel_and_email_rejected(store):
    """Unknown channels and invalid emails are rejected (FR3)."""
    r, n, _, _ = ids(store)
    with pytest.raises(ValueError):
        store.submit_response(1, {r: 3, n: 5}, channel="fax")
    with pytest.raises(ValueError):
        store.submit_response(1, {r: 3, n: 5}, respondent_email="not-an-email")


def test_csv_round_trip(store, tmp_path):
    """Responses survive a CSV export and import (FR6)."""
    r, n, _, t = ids(store)
    store.submit_response(1, {r: 5, n: 10, t: "love it"}, submitted_at=datetime(2026, 1, 5))
    store.submit_response(1, {r: 2, n: 3}, channel="email", submitted_at=datetime(2026, 2, 5))
    path = tmp_path / "out.csv"
    assert store.export_csv(1, path) == 2
    other = FeedbackStore(":memory:")
    other.create_form(FeedbackForm.from_dict(store.get_form(1).to_dict()))
    assert other.import_csv(1, path) == 2
    imported = other.list_responses(1)
    assert [x.channel for x in imported] == ["web", "email"]
    assert imported[0].submitted_at == datetime(2026, 1, 5)


def test_file_database_persists(tmp_path):
    """Data persists in a SQLite file between connections (FR6)."""
    db = str(tmp_path / "f.db")
    s = FeedbackStore(db)
    s.create_form(FeedbackForm(title="Keep", questions=[Question("Q?", "rating")]))
    s.close()
    assert FeedbackStore(db).get_form(1).title == "Keep"
