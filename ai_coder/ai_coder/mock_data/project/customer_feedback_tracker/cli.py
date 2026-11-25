# Author: Xin Zhou (Student ID: 42742822)
# Reference implementation used as canned output by the offline mock LLM.
"""Command-line interface for the Customer Feedback Tracker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analytics, distribution, visualization
from .models import CHANNELS, FeedbackForm
from .storage import FeedbackStore


def build_parser() -> argparse.ArgumentParser:
    """Define all sub-commands and options."""
    p = argparse.ArgumentParser(prog="feedback", description="Customer Feedback Tracker")
    p.add_argument("--db", default="feedback.db", help="SQLite database file (default: feedback.db)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create-form", help="create a form from a JSON file")
    c.add_argument("json_file")
    sub.add_parser("list-forms", help="list all forms")
    s = sub.add_parser("show-form", help="show a form and its questions")
    s.add_argument("form_id", type=int)
    u = sub.add_parser("update-form", help="update title/description/active flag")
    u.add_argument("form_id", type=int)
    u.add_argument("--title")
    u.add_argument("--description")
    u.add_argument("--active", choices=["yes", "no"])
    d = sub.add_parser("delete-form", help="delete a form and its responses")
    d.add_argument("form_id", type=int)

    sh = sub.add_parser("share", help="distribute a form by email and/or social media")
    sh.add_argument("form_id", type=int)
    sh.add_argument("--base-url", default="https://feedback.example.com")
    sh.add_argument("--emails", help="comma-separated customer emails")
    sh.add_argument("--outbox", default="outbox", help="folder for .eml files (nothing is sent)")
    sh.add_argument("--social", action="store_true", help="print social media share links")

    sb = sub.add_parser("submit", help="submit a response")
    sb.add_argument("form_id", type=int)
    sb.add_argument("--answers", required=True, help='JSON, e.g. \'{"1": 5, "2": "Great"}\'')
    sb.add_argument("--channel", default="web", choices=CHANNELS)
    sb.add_argument("--email")

    i = sub.add_parser("import-csv", help="import responses from CSV")
    i.add_argument("form_id", type=int)
    i.add_argument("csv_file")
    e = sub.add_parser("export-csv", help="export responses to CSV")
    e.add_argument("form_id", type=int)
    e.add_argument("csv_file")

    r = sub.add_parser("report", help="analytics report with insights")
    r.add_argument("form_id", type=int)
    r.add_argument("--period", default="week", choices=analytics.PERIODS)
    ch = sub.add_parser("charts", help="export SVG charts")
    ch.add_argument("form_id", type=int)
    ch.add_argument("--out", default="charts")
    ch.add_argument("--period", default="week", choices=analytics.PERIODS)
    return p


def _report(store: FeedbackStore, form_id: int, period: str) -> str:
    """Build the text report for a form."""
    form = store.get_form(form_id)
    responses = store.list_responses(form_id)
    data = analytics.analyze(form, responses, period)
    lines = [f"Report for form {form.id}: {form.title}", f"Responses: {data['response_count']}"]
    for summary in data["ratings"].values():
        lines.append(f"\n{summary['question']} (average {summary['average']})")
        lines.append(visualization.text_bar_chart({str(k): v for k, v in summary["distribution"].items()}))
    lines.append(f"\nNPS: {data['nps'] if data['nps'] is not None else 'n/a'}")
    if data["channels"]:
        lines.append("\nResponses by channel:\n" + visualization.text_bar_chart(data["channels"]))
    s = data["sentiment"]
    lines.append(f"\nSentiment: {s['positive']}% positive, {s['neutral']}% neutral, {s['negative']}% negative")
    if data["keywords"]:
        lines.append("Top keywords: " + ", ".join(f"{w} ({n})" for w, n in data["keywords"]))
    if data["trend"]:
        lines.append(f"\nTrend by {period}:")
        lines += [f"  {t['period']}: {t['count']} responses, avg rating {t['average_rating']}" for t in data["trend"]]
    lines.append("\nInsights:")
    lines += [f"  - {text}" for text in analytics.generate_insights(form, responses, period)]
    return "\n".join(lines)


def run(args: argparse.Namespace, store: FeedbackStore) -> str:
    """Execute one parsed command and return the text to print."""
    cmd = args.command
    if cmd == "create-form":
        data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        form = store.create_form(FeedbackForm.from_dict(data))
        return f"Created form {form.id}: {form.title} ({len(form.questions)} questions)"
    if cmd == "list-forms":
        forms = store.list_forms()
        if not forms:
            return "No forms yet."
        return "\n".join(f"{f.id}: {f.title} [{'active' if f.active else 'inactive'}] "
                         f"{len(store.list_responses(f.id))} responses" for f in forms)
    if cmd == "show-form":
        return json.dumps(store.get_form(args.form_id).to_dict(), indent=2)
    if cmd == "update-form":
        active = None if args.active is None else args.active == "yes"
        form = store.update_form(args.form_id, args.title, args.description, active)
        return f"Updated form {form.id}: {form.title} (active={form.active})"
    if cmd == "delete-form":
        store.delete_form(args.form_id)
        return f"Deleted form {args.form_id}"
    if cmd == "share":
        form = store.get_form(args.form_id)
        out = []
        if args.emails:
            emails = [e.strip() for e in args.emails.split(",") if e.strip()]
            paths = distribution.save_to_outbox(distribution.build_invitations(form, emails, args.base_url), args.outbox)
            out.append(f"Saved {len(paths)} invitation(s) to {args.outbox}/")
        if args.social or not args.emails:
            out += [f"{name}: {url}" for name, url in distribution.social_share_links(form, args.base_url).items()]
        return "\n".join(out)
    if cmd == "submit":
        response = store.submit_response(args.form_id, json.loads(args.answers), args.channel, args.email)
        return f"Recorded response {response.id} for form {args.form_id}"
    if cmd == "import-csv":
        return f"Imported {store.import_csv(args.form_id, args.csv_file)} responses"
    if cmd == "export-csv":
        return f"Exported {store.export_csv(args.form_id, args.csv_file)} responses to {args.csv_file}"
    if cmd == "report":
        return _report(store, args.form_id, args.period)
    if cmd == "charts":
        form = store.get_form(args.form_id)
        data = analytics.analyze(form, store.list_responses(args.form_id), args.period)
        out_dir, saved = Path(args.out), []
        for qid, summary in data["ratings"].items():
            if summary["count"]:
                svg = visualization.bar_chart_svg({str(k): v for k, v in summary["distribution"].items()},
                                                  f"Ratings: {summary['question']}")
                saved.append(visualization.save_svg(svg, out_dir / f"form{form.id}_q{qid}_ratings.svg"))
        if data["channels"]:
            saved.append(visualization.save_svg(visualization.bar_chart_svg(data["channels"], "Responses by channel"),
                                                out_dir / f"form{form.id}_channels.svg"))
        points = [(t["period"], t["average_rating"]) for t in data["trend"] if t["average_rating"] is not None]
        if points:
            saved.append(visualization.save_svg(
                visualization.line_chart_svg(points, f"Average rating by {args.period}", y_max=5),
                out_dir / f"form{form.id}_trend.svg"))
        return "\n".join(f"Saved {p}" for p in saved) or "No data to chart yet."
    raise ValueError(f"Unknown command {cmd}")


def main(argv: list[str] | None = None) -> int:
    """Entry point: returns a process exit code, printing friendly errors."""
    args = build_parser().parse_args(argv)
    store = FeedbackStore(args.db)
    try:
        print(run(args, store))
        return 0
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"Error: {message}", file=sys.stderr)
        return 1
    finally:
        store.close()
