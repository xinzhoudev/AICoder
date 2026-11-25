# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# Reference implementation used as canned output by the offline mock LLM.
"""Distribute forms by email (outbox or SMTP) and social media share links."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, urlencode

from .models import CHANNELS, FeedbackForm, is_valid_email


def form_link(base_url: str, form_id: int, channel: str = "web") -> str:
    """Public link to a form, tagged with the channel it was shared on."""
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel '{channel}'")
    return f"{base_url.rstrip('/')}/forms/{form_id}?{urlencode({'channel': channel})}"


def build_invitations(form: FeedbackForm, emails: list[str], base_url: str,
                      sender: str = "feedback@example.com") -> list[EmailMessage]:
    """Create one personalised invitation email per address."""
    bad = [e for e in emails if not is_valid_email(e)]
    if bad:
        raise ValueError(f"Invalid email address(es): {', '.join(bad)}")
    link = form_link(base_url, form.id, "email")
    messages = []
    for address in emails:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = address.strip()
        msg["Subject"] = f"We'd love your feedback: {form.title}"
        name = address.split("@")[0]
        msg.set_content(f"Hi {name},\n\n{form.description or 'Please tell us about your experience.'}\n\n"
                        f"Share your feedback here: {link}\n\nThank you!")
        messages.append(msg)
    return messages


def save_to_outbox(messages: list[EmailMessage], outbox_dir: str | Path) -> list[Path]:
    """Save messages as .eml files instead of sending them (the default)."""
    folder = Path(outbox_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, msg in enumerate(messages, start=1):
        safe = msg["To"].replace("@", "_at_").replace("/", "_")
        path = folder / f"{index:03d}_{safe}.eml"
        path.write_bytes(bytes(msg))
        paths.append(path)
    return paths


def send_via_smtp(messages: list[EmailMessage], host: str, port: int = 587, username: str | None = None,
                  password: str | None = None, use_tls: bool = True, smtp_factory=smtplib.SMTP) -> int:
    """Send messages through an SMTP server; `smtp_factory` can be replaced in tests."""
    with smtp_factory(host, port) as server:
        if use_tls:
            server.starttls()
        if username:
            server.login(username, password or "")
        for msg in messages:
            server.send_message(msg)
    return len(messages)


def social_share_links(form: FeedbackForm, base_url: str) -> dict[str, str]:
    """Share URLs for Twitter/X, Facebook and LinkedIn, each with its channel tag."""
    text = quote(f"Tell us what you think: {form.title}")
    links = {}
    for channel, template in (
        ("twitter", "https://twitter.com/intent/tweet?text={text}&url={url}"),
        ("facebook", "https://www.facebook.com/sharer/sharer.php?u={url}"),
        ("linkedin", "https://www.linkedin.com/sharing/share-offsite/?url={url}"),
    ):
        url = quote(form_link(base_url, form.id, channel), safe="")
        links[channel] = template.format(text=text, url=url)
    return links
