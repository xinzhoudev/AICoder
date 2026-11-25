# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# Reference implementation used as canned output by the offline mock LLM.
"""Analytics: ratings, NPS, channels, sentiment, trends and insights."""

from __future__ import annotations

import re
from collections import Counter, OrderedDict

from .models import FeedbackForm, Response

POSITIVE_WORDS = {"good", "great", "excellent", "love", "loved", "amazing", "fast", "friendly", "helpful",
                  "easy", "happy", "perfect", "recommend", "awesome", "nice", "satisfied", "quick"}
NEGATIVE_WORDS = {"bad", "poor", "slow", "terrible", "awful", "hate", "broken", "expensive", "rude",
                  "confusing", "late", "bug", "bugs", "crash", "crashes", "difficult", "disappointed",
                  "worst", "unhappy", "delay", "delayed", "problem", "issue", "issues"}
STOP_WORDS = {"the", "a", "an", "and", "or", "but", "is", "was", "were", "are", "it", "this", "that", "to",
              "of", "in", "on", "for", "with", "my", "i", "we", "you", "very", "so", "be", "at", "too",
              "not", "have", "had", "they", "their", "our", "me", "its", "as", "just", "really"}
PERIODS = ("day", "week", "month")


def _words(text: str) -> list[str]:
    """Lower-case words in `text`."""
    return re.findall(r"[a-z']+", text.lower())


def rating_summary(form: FeedbackForm, responses: list[Response]) -> dict[int, dict]:
    """Average, count and 1-5 distribution for every rating question."""
    result = {}
    for q in form.questions_of_type("rating"):
        values = [int(r.answers[q.id]) for r in responses if q.id in r.answers]
        dist = {score: values.count(score) for score in range(1, 6)}
        result[q.id] = {"question": q.text, "count": len(values), "distribution": dist,
                        "average": round(sum(values) / len(values), 2) if values else None}
    return result


def nps_score(scores: list[int]) -> float | None:
    """Net Promoter Score: % promoters (9-10) minus % detractors (0-6)."""
    if not scores:
        return None
    promoters = sum(1 for s in scores if s >= 9)
    detractors = sum(1 for s in scores if s <= 6)
    return round(100.0 * (promoters - detractors) / len(scores), 1)


def form_nps(form: FeedbackForm, responses: list[Response]) -> float | None:
    """NPS across all NPS questions of a form."""
    scores = [int(r.answers[q.id]) for q in form.questions_of_type("nps") for r in responses if q.id in r.answers]
    return nps_score(scores)


def channel_breakdown(responses: list[Response]) -> dict[str, int]:
    """Number of responses per distribution channel."""
    return dict(Counter(r.channel for r in responses).most_common())


def classify_sentiment(text: str) -> str:
    """Keyword-based sentiment: 'positive', 'negative' or 'neutral'."""
    words = _words(text)
    score = 0
    for i, w in enumerate(words):
        negated = i > 0 and words[i - 1] in {"not", "never", "no", "isn't", "wasn't", "don't"}
        if w in POSITIVE_WORDS:
            score += -1 if negated else 1
        elif w in NEGATIVE_WORDS:
            score += 1 if negated else -1
    return "positive" if score > 0 else "negative" if score < 0 else "neutral"


def text_answers(form: FeedbackForm, responses: list[Response]) -> list[str]:
    """All non-empty free-text answers."""
    ids = {q.id for q in form.questions_of_type("text")}
    return [str(v) for r in responses for qid, v in r.answers.items() if qid in ids and str(v).strip()]


def sentiment_summary(form: FeedbackForm, responses: list[Response]) -> dict:
    """Share (%) of positive, neutral and negative text answers."""
    labels = [classify_sentiment(t) for t in text_answers(form, responses)]
    total = len(labels)
    summary = {k: (round(100.0 * labels.count(k) / total, 1) if total else 0.0)
               for k in ("positive", "neutral", "negative")}
    summary["count"] = total
    return summary


def top_keywords(texts: list[str], n: int = 5) -> list[tuple[str, int]]:
    """Most frequent words, ignoring stop words and very short words."""
    counter = Counter(w for t in texts for w in _words(t) if w not in STOP_WORDS and len(w) > 2)
    return counter.most_common(n)


def _period_key(when, period: str) -> str:
    """Bucket label for a timestamp."""
    if period == "day":
        return when.strftime("%Y-%m-%d")
    if period == "week":
        year, week, _ = when.isocalendar()
        return f"{year}-W{week:02d}"
    return when.strftime("%Y-%m")


def trend(form: FeedbackForm, responses: list[Response], period: str = "week") -> list[dict]:
    """Responses per period with the average of rating answers, oldest first."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    rating_ids = {q.id for q in form.questions_of_type("rating")}
    buckets: "OrderedDict[str, list]" = OrderedDict()
    for r in sorted(responses, key=lambda x: x.submitted_at):
        buckets.setdefault(_period_key(r.submitted_at, period), []).append(r)
    rows = []
    for key, items in buckets.items():
        ratings = [int(v) for r in items for qid, v in r.answers.items() if qid in rating_ids]
        rows.append({"period": key, "count": len(items),
                     "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None})
    return rows


def analyze(form: FeedbackForm, responses: list[Response], period: str = "week") -> dict:
    """All analytics for a form in one dict."""
    texts = text_answers(form, responses)
    return {"form_id": form.id, "title": form.title, "response_count": len(responses),
            "ratings": rating_summary(form, responses), "nps": form_nps(form, responses),
            "channels": channel_breakdown(responses), "sentiment": sentiment_summary(form, responses),
            "keywords": top_keywords(texts), "trend": trend(form, responses, period)}


def generate_insights(form: FeedbackForm, responses: list[Response], period: str = "week") -> list[str]:
    """Plain-English, actionable insights derived from the analytics."""
    if not responses:
        return ["No responses yet: share the form by email or social media to start collecting feedback."]
    data = analyze(form, responses, period)
    insights = []
    for summary in data["ratings"].values():
        avg = summary["average"]
        if avg is not None and avg < 3:
            insights.append(f"Low satisfaction on '{summary['question']}' (average {avg}/5): prioritise improvements here.")
        elif avg is not None and avg >= 4.5:
            insights.append(f"Customers love '{summary['question']}' (average {avg}/5): highlight it in marketing.")
    if data["nps"] is not None:
        if data["nps"] < 0:
            insights.append(f"NPS is negative ({data['nps']}): detractors outnumber promoters; follow up with unhappy customers.")
        elif data["nps"] >= 50:
            insights.append(f"Excellent NPS ({data['nps']}): consider a referral programme.")
    rated = [row for row in data["trend"] if row["average_rating"] is not None]
    if len(rated) >= 2:
        first, last = rated[0]["average_rating"], rated[-1]["average_rating"]
        if last < first:
            insights.append(f"Ratings are declining ({first} in {rated[0]['period']} to {last} in {rated[-1]['period']}): investigate recent changes.")
        elif last > first:
            insights.append(f"Ratings are improving ({first} to {last}): recent changes are working.")
    sentiment = data["sentiment"]
    if sentiment["count"] and sentiment["negative"] > 30:
        insights.append(f"{sentiment['negative']}% of comments are negative.")
    negative_texts = [t for t in text_answers(form, responses) if classify_sentiment(t) == "negative"]
    complaints = [w for w, _ in top_keywords(negative_texts, 3)]
    if complaints:
        insights.append("Most frequent complaint keywords: " + ", ".join(complaints) + ".")
    if data["channels"]:
        best = next(iter(data["channels"]))
        insights.append(f"Most responses come from '{best}': focus distribution there.")
    return insights
