# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# Reference implementation used as canned output by the offline mock LLM.
"""Dependency-free charts: SVG files and terminal bar charts."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

BAR_COLOR = "#2f6fdf"
LINE_COLOR = "#d9480f"


def _frame(width: int, height: int, title: str) -> list[str]:
    """Opening SVG tags with a white background and a title."""
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" font-family="sans-serif">',
            f'<rect width="{width}" height="{height}" fill="white"/>',
            f'<text x="{width / 2}" y="24" text-anchor="middle" font-size="16">{escape(title)}</text>']


def bar_chart_svg(data: dict[str, float], title: str, width: int = 600, height: int = 360) -> str:
    """Vertical bar chart as an SVG string."""
    if not data:
        raise ValueError("No data to plot")
    parts = _frame(width, height, title)
    left, bottom, top = 50, height - 40, 40
    plot_w, plot_h = width - left - 20, bottom - top
    peak = max(max(data.values()), 1)
    slot = plot_w / len(data)
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{width - 20}" y2="{bottom}" stroke="#444"/>')
    for i, (label, value) in enumerate(data.items()):
        h = plot_h * value / peak
        x = left + i * slot + slot * 0.15
        parts.append(f'<rect x="{x:.1f}" y="{bottom - h:.1f}" width="{slot * 0.7:.1f}" height="{h:.1f}" fill="{BAR_COLOR}"/>')
        parts.append(f'<text x="{x + slot * 0.35:.1f}" y="{bottom - h - 4:.1f}" text-anchor="middle" font-size="12">{value:g}</text>')
        parts.append(f'<text x="{x + slot * 0.35:.1f}" y="{bottom + 16}" text-anchor="middle" font-size="12">{escape(str(label))}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def line_chart_svg(points: list[tuple[str, float]], title: str, width: int = 600, height: int = 360,
                   y_max: float | None = None) -> str:
    """Line chart (e.g. average rating per period) as an SVG string."""
    points = [(label, value) for label, value in points if value is not None]
    if not points:
        raise ValueError("No data to plot")
    parts = _frame(width, height, title)
    left, bottom, top = 50, height - 40, 40
    plot_w, plot_h = width - left - 30, bottom - top
    peak = y_max or max(max(v for _, v in points), 1)
    step = plot_w / max(len(points) - 1, 1)
    coords = [(left + i * step, bottom - plot_h * v / peak) for i, (_, v) in enumerate(points)]
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{width - 20}" y2="{bottom}" stroke="#444"/>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#444"/>')
    parts.append('<polyline fill="none" stroke="{}" stroke-width="2" points="{}"/>'.format(
        LINE_COLOR, " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)))
    for (x, y), (label, value) in zip(coords, points):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{LINE_COLOR}"/>')
        parts.append(f'<text x="{x:.1f}" y="{y - 8:.1f}" text-anchor="middle" font-size="12">{value:g}</text>')
        parts.append(f'<text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle" font-size="11">{escape(str(label))}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def save_svg(svg: str, path: str | Path) -> Path:
    """Write an SVG string to `path`, creating folders as needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg, encoding="utf-8")
    return target


def text_bar_chart(data: dict[str, float], width: int = 30) -> str:
    """Horizontal bar chart drawn with characters, for the terminal."""
    if not data:
        return "(no data)"
    peak = max(max(data.values()), 1)
    label_w = max(len(str(k)) for k in data)
    return "\n".join(f"{str(k).rjust(label_w)} | {'#' * round(width * v / peak)} {v:g}" for k, v in data.items())
