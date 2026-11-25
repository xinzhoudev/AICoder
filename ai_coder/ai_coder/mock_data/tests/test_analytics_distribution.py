# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# Reference implementation used as canned output by the offline mock LLM.
"""Tests for distribution (FR4-FR5), analytics/insights (FR7-FR9), charts (FR10) and the CLI (FR11)."""

import json
from datetime import datetime
from urllib.parse import unquote

import pytest

from customer_feedback_tracker import analytics, distribution, visualization
from customer_feedback_tracker.cli import main
from customer_feedback_tracker.models import FeedbackForm, Question, Response


@pytest.fixture
def form():
    """Form with ids already assigned: 1 rating, 2 nps, 3 text."""
    return FeedbackForm(title="Shop", id=7, questions=[
        Question("Product?", "rating", id=1), Question("Recommend?", "nps", id=2),
        Question("Comments?", "text", required=False, id=3)])


def resp(rating, nps, text="", when=datetime(2026, 1, 1), channel="web"):
    """Helper to build a Response."""
    answers = {1: rating, 2: nps}
    if text:
        answers[3] = text
    return Response(form_id=7, answers=answers, channel=channel, submitted_at=when)


def test_form_link_and_social_links(form):
    assert distribution.form_link("https://x.io/", 7, "email") == "https://x.io/forms/7?channel=email"
    links = distribution.social_share_links(form, "https://x.io")
    assert set(links) == {"twitter", "facebook", "linkedin"}
    assert "channel=linkedin" in unquote(links["linkedin"])


def test_invitations_saved_to_outbox(form, tmp_path):
    msgs = distribution.build_invitations(form, ["ann@x.com", "bob@y.org"], "https://x.io")
    assert len(msgs) == 2 and "channel=email" in msgs[0].get_content()
    paths = distribution.save_to_outbox(msgs, tmp_path / "outbox")
    assert len(paths) == 2 and all(p.exists() for p in paths)


def test_invalid_invitation_email_rejected(form):
    with pytest.raises(ValueError):
        distribution.build_invitations(form, ["bad-address"], "https://x.io")


def test_smtp_sender_is_injectable(form):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, m): sent.append(m)

    msgs = distribution.build_invitations(form, ["ann@x.com"], "https://x.io")
    assert distribution.send_via_smtp(msgs, "smtp.test", smtp_factory=FakeSMTP) == 1 and len(sent) == 1


def test_rating_summary_and_nps(form):
    rs = [resp(5, 10), resp(4, 9), resp(1, 2)]
    summary = analytics.rating_summary(form, rs)[1]
    assert summary["count"] == 3
    assert summary["average"] == pytest.approx(3.33, abs=0.01)
    assert summary["distribution"][5] == 1
    assert analytics.nps_score([10, 9, 2]) == pytest.approx(33.3, abs=0.1)
    assert analytics.nps_score([]) is None


def test_sentiment_and_keywords(form):
    assert analytics.classify_sentiment("Great service, love it") == "positive"
    assert analytics.classify_sentiment("Delivery was slow and the box was broken") == "negative"
    assert analytics.classify_sentiment("It arrived on Tuesday") == "neutral"
    s = analytics.sentiment_summary(form, [resp(5, 9, "great"), resp(1, 1, "slow delivery")])
    assert s["positive"] == pytest.approx(50) and s["negative"] == pytest.approx(50)
    words = [w for w, _ in analytics.top_keywords(["slow delivery", "delivery slow again", "the"])]
    assert words[:2] == ["slow", "delivery"] or words[:2] == ["delivery", "slow"]
    assert "the" not in words


def test_trend_by_month_and_invalid_period(form):
    rs = [resp(5, 9, when=datetime(2026, 1, 3)), resp(3, 9, when=datetime(2026, 1, 20)),
          resp(2, 5, when=datetime(2026, 2, 2))]
    rows = analytics.trend(form, rs, "month")
    assert [r["period"] for r in rows] == ["2026-01", "2026-02"]
    assert rows[0]["count"] == 2 and rows[0]["average_rating"] == pytest.approx(4.0)
    with pytest.raises(ValueError):
        analytics.trend(form, rs, "year")


def test_insights_flag_problems(form):
    rs = [resp(4, 3, "slow and broken", datetime(2026, 1, 1), "email"),
          resp(2, 2, "terrible slow support", datetime(2026, 3, 1), "email"),
          resp(1, 1, "", datetime(2026, 3, 2))]
    text = " ".join(analytics.generate_insights(form, rs, "month")).lower()
    assert "low satisfaction" in text
    assert "nps is negative" in text
    assert "declining" in text
    assert "slow" in text


def test_insights_without_responses(form):
    assert len(analytics.generate_insights(form, [])) == 1


def test_svg_charts(tmp_path):
    bar = visualization.bar_chart_svg({"1": 2, "5": 7}, "Ratings <&>")
    assert bar.startswith("<svg") and bar.count("<rect") == 3 and "&lt;&amp;&gt;" in bar
    line = visualization.line_chart_svg([("Jan", 4.0), ("Feb", 3.0)], "Trend")
    assert "<polyline" in line
    assert visualization.save_svg(line, tmp_path / "c" / "t.svg").exists()
    with pytest.raises(ValueError):
        visualization.bar_chart_svg({}, "empty")
    assert "#" in visualization.text_bar_chart({"a": 3, "b": 1})


def test_cli_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    form_file = tmp_path / "form.json"
    form_file.write_text(json.dumps({"title": "CLI", "questions": [
        {"text": "Rate", "type": "rating"}, {"text": "Why?", "type": "text", "required": False}]}))
    assert main(["--db", db, "create-form", str(form_file)]) == 0
    assert main(["--db", db, "submit", "1", "--answers", '{"1": 2, "2": "slow"}', "--channel", "email"]) == 0
    assert main(["--db", db, "share", "1", "--emails", "a@b.com", "--outbox", str(tmp_path / "ob")]) == 0
    assert main(["--db", db, "report", "1"]) == 0
    assert main(["--db", db, "charts", "1", "--out", str(tmp_path / "charts")]) == 0
    out = capsys.readouterr().out
    assert "Created form 1" in out and "Insights" in out
    assert list((tmp_path / "charts").glob("*.svg"))


def test_cli_friendly_error(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "e.db"), "show-form", "42"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error:") and "Traceback" not in err
