"""Terminal table: one row per group, one column per category."""

from __future__ import annotations

from rich import box
from rich.table import Table
from rich.text import Text

from ..aggregate import CODE_LABELS, COMMENT_LABELS, Group, merge_groups
from ..taxonomy import UNCLASSIFIED, Taxonomy


def _title(group: Group, by: str) -> Text:
    if by == "commit":
        return Text(f"{group.key[:8]} [{group.intent}]\n{group.title[:60]}")
    return Text(group.key)


def _cell(group: Group, category: str, taxonomy: Taxonomy, metric: str, basis: str) -> Text:
    counts = group.categories.get(category)
    if counts is None:
        return Text("")
    total = group.total(metric, basis)
    share = counts.value(metric, basis) / total if total else 0.0
    added, deleted = counts.value("added", basis), counts.value("deleted", basis)
    head = {"churn": f"+{added} −{deleted}", "added": f"+{added}", "deleted": f"−{deleted}"}
    text = f"{head[metric]}  {share:.0%}"
    if taxonomy.split_lines(category):
        code = counts.value(metric, basis, CODE_LABELS)
        comments = counts.value(metric, basis, COMMENT_LABELS)
        text += f"\ncode {code} · comments {comments}"
    return Text(text)


def render_table(
    groups: list[Group], taxonomy: Taxonomy, *, metric: str, basis: str, by: str
) -> Table:
    total = merge_groups(groups)
    categories = sorted(total.categories, key=taxonomy.order_key)
    table = Table(box=box.SIMPLE_HEAD)
    table.add_column(by)
    for category in categories:
        table.add_column(category, justify="right")
    for group in groups:
        table.add_row(
            _title(group, by), *(_cell(group, c, taxonomy, metric, basis) for c in categories)
        )
    if len(groups) > 1:
        table.add_section()
        table.add_row(
            Text("total"), *(_cell(total, c, taxonomy, metric, basis) for c in categories)
        )
    unclassified = sum(
        counts.value(metric, basis)
        for category, counts in total.categories.items()
        if category.partition("/")[0] == UNCLASSIFIED
    )
    table.caption = Text(
        f"basis: {basis} · metric: {metric} · moved files: {total.moved}"
        f" · unclassified: {unclassified} lines"
    )
    table.caption_style = "yellow" if unclassified else "dim"
    # Rich wraps the caption to the table width; keep the footer on one line.
    table.min_width = len(table.caption) + 2
    return table
