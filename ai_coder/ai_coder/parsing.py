# Author: Xin Zhou (Student ID: 42742822)
# SWE 270P Final Project - AI Coder: parsing helpers for LLM output.
"""Turn free-form LLM replies into JSON objects and file maps.

Small open-source models do not always follow the requested format, so the
parsers here accept the main format plus the common variations.
"""

from __future__ import annotations

import json
import re

FILE_BLOCK_RE = re.compile(
    r"^[ \t]*={3,}[ \t]*FILE:[ \t]*(?P<path>[^\n=]+?)[ \t]*={3,}[ \t]*\n"
    r"(?P<body>.*?)"
    r"^[ \t]*={3,}[ \t]*END[ \t]*FILE[ \t]*={3,}",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)
# Fallback: "### path/to/file.py" or "**path/to/file.py**" followed by a fenced block.
HEADING_BLOCK_RE = re.compile(
    r"(?:^#{1,6}[ \t]*|^\*\*|^File:[ \t]*|^`)"
    r"(?P<path>[\w./-]+\.(?:py|txt|md|json|cfg|toml|ini))`?\**[ \t]*\n+"
    r"```[\w+-]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.MULTILINE,
)
# Fallback: fenced block whose first line is "# file: path".
FENCE_COMMENT_RE = re.compile(
    r"```[\w+-]*\n#[ \t]*(?:file(?:name)?|path)[ \t]*:[ \t]*(?P<path>[\w./-]+)[ \t]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)
FENCE_RE = re.compile(r"^\s*```[\w+-]*[ \t]*\n(?P<body>.*?)\n?```\s*$", re.DOTALL)


def strip_fences(text: str) -> str:
    """Remove one surrounding markdown code fence, if present."""
    match = FENCE_RE.match(text)
    return match.group("body") if match else text


def _clean_path(path: str) -> str:
    """Normalise a file path written by the model (quotes, ./, backslashes)."""
    path = path.strip().strip("`'\"*").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def extract_files(text: str) -> dict[str, str]:
    """Extract {path: content} from an LLM reply.

    Primary format::

        === FILE: package/module.py ===
        <code>
        === END FILE ===
    """
    files: dict[str, str] = {}
    for pattern in (FILE_BLOCK_RE, HEADING_BLOCK_RE, FENCE_COMMENT_RE):
        for match in pattern.finditer(text):
            path = _clean_path(match.group("path"))
            body = strip_fences(match.group("body").strip("\n"))
            if path and ".." not in path.split("/"):
                files.setdefault(path, body.rstrip() + "\n")
        if files:
            break
    return files


def extract_json(text: str):
    """Return the first JSON object or array found in `text`.

    Handles code fences, leading prose and trailing commentary. Raises
    ValueError if nothing parseable is found.
    """
    candidate = strip_fences(text.strip())
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "{[":
            try:
                value, _ = decoder.raw_decode(text[index:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("No JSON object found in model output")


def compact_json(value) -> str:
    """Serialise JSON without whitespace to save prompt tokens."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def truncate(text: str, limit: int) -> str:
    """Keep the head and tail of long text (the tail of pytest output holds
    the summary, the head holds the first failure)."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]


def split_spec(text: str) -> tuple[str, str]:
    """Split a spec markdown file into (description, requirements).

    The description is the '## Description' section; everything after it is
    treated as requirements. Without headings, the first paragraph is the
    description.
    """
    match = re.search(r"##\s*Description\s*\n(.*?)(?=\n##\s)", text, re.DOTALL | re.IGNORECASE)
    if match:
        description = match.group(1).strip()
        requirements = text[match.end():].strip()
        return description, requirements
    parts = text.strip().split("\n\n", 1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
