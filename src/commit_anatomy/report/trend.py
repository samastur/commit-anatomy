"""Self-contained HTML trend: stacked bars per week or month, drawn as inline SVG."""

from __future__ import annotations

from html import escape

from ..aggregate import Group
from ..taxonomy import Taxonomy

# Categorical slots, validated with the dataviz skill's validate_palette.js against the
# light (#fcfcfb) and dark (#1a1a19) surfaces. Built-in categories keep a fixed slot.
SLOTS = {"docs": 0, "tests": 1, "code": 2, "supporting": 3, "unclassified": 4}
EXTRA_SLOTS = (5, 6, 7)
LIGHT = ("#2a78d6", "#1baf7a", "#eb6834", "#6250d6", "#e87ba4", "#eda100", "#e34948", "#008300")
DARK = ("#3987e5", "#199e70", "#d95926", "#9085e9", "#d55181", "#c98500", "#e66767", "#008300")

BAR, GAP, HEIGHT, LEFT, BOTTOM, TOP = 28, 10, 220, 56, 70, 12


def _vars(colors: tuple[str, ...]) -> str:
    return " ".join(f"--c{i}: {c};" for i, c in enumerate(colors))


STYLE = f"""
:root {{ --bg: #fcfcfb; --fg: #0b0b0b; --muted: #52514e; --grid: #e1e0d9; {_vars(LIGHT)} }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #1a1a19; --fg: #f0efec; --muted: #c3c2b7; --grid: #2c2c2a; {_vars(DARK)} }}
}}
body {{ background: var(--bg); color: var(--fg); margin: 24px 16px;
       font: 14px/1.4 system-ui, -apple-system, sans-serif; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
h2 {{ font-size: 15px; margin: 24px 0 8px; }}
.sub {{ color: var(--muted); margin: 0 0 16px; }}
.chart {{ overflow-x: auto; }}
.grid {{ stroke: var(--grid); }}
.tick {{ fill: var(--muted); font-size: 11px; }}
rect {{ stroke: var(--bg); stroke-width: 2; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 8px 16px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
.legend i {{ width: 12px; height: 12px; border-radius: 2px; display: inline-block; }}
{" ".join(f".c{i} {{ fill: var(--c{i}); background: var(--c{i}); }}" for i in range(len(LIGHT)))}
"""


def _slots(categories: list[str]) -> dict[str, int]:
    slots: dict[str, int] = {}
    extra = 0
    for category in categories:
        if category in SLOTS:
            slots[category] = SLOTS[category]
        else:
            slots[category] = EXTRA_SLOTS[extra % len(EXTRA_SLOTS)]
            extra += 1
    return slots


def _chart(groups, categories, slots, metric, basis, share: bool, label: str) -> str:
    totals = [g.total(metric, basis) for g in groups]
    scale = 1 if share else max([*totals, 1])
    width = LEFT + len(groups) * (BAR + GAP) + 16
    height = TOP + HEIGHT + BOTTOM
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="{escape(label)}">'
    ]
    for fraction in (0, 0.5, 1):
        y = TOP + HEIGHT * (1 - fraction)
        tick = f"{fraction:.0%}" if share else str(round(scale * fraction))
        parts.append(
            f'<line class="grid" x1="{LEFT}" x2="{width - 8}" y1="{y}" y2="{y}"/>'
            f'<text class="tick" x="{LEFT - 6}" y="{y + 4}" text-anchor="end">{tick}</text>'
        )
    for i, group in enumerate(groups):
        x = LEFT + i * (BAR + GAP) + GAP / 2
        total = totals[i]
        y = TOP + HEIGHT
        for category in categories:
            counts = group.categories.get(category)
            value = counts.value(metric, basis) if counts else 0
            if not value or not total:
                continue
            h = HEIGHT * (value / total if share else value / scale)
            y -= h
            parts.append(
                f'<rect class="c{slots[category]}" x="{x}" y="{y:.1f}" width="{BAR}" '
                f'height="{h:.1f}"><title>{escape(group.key)} · {escape(category)}: '
                f"{value} lines ({value / total:.0%})</title></rect>"
            )
        parts.append(
            f'<text class="tick" transform="translate({x + BAR / 2},{TOP + HEIGHT + 12}) '
            f'rotate(45)">{escape(group.key)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_trend(
    groups: list[Group], taxonomy: Taxonomy, *, metric: str, basis: str, title: str
) -> str:
    categories = [t for t in taxonomy.tops if any(t in g.categories for g in groups)]
    slots = _slots(categories)
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head><body>"
        f"<h1>{escape(title)}</h1>"
        f'<p class="sub">metric: {escape(metric)} · basis: {escape(basis)}</p>'
    )
    if not groups:
        return head + "<p>No commits in this range.</p></body></html>"
    legend = "".join(f'<span><i class="c{slots[c]}"></i>{escape(c)}</span>' for c in categories)
    share = _chart(groups, categories, slots, metric, basis, True, "Share of changed lines")
    size = _chart(groups, categories, slots, metric, basis, False, "Changed lines")
    return (
        head
        + f'<div class="legend">{legend}</div>'
        + f'<h2>Share of changed lines</h2><div class="chart">{share}</div>'
        + f'<h2>Changed lines</h2><div class="chart">{size}</div>'
        + "</body></html>"
    )
