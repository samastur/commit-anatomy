"""Group results by commit, intent, period or in total, per category."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .analysis import CommitResult
from .rows import Row, rows_from

CODE_LABELS = ("code", "unlexed")
COMMENT_LABELS = ("comment", "docstring")


@dataclass
class Counts:
    added: Counter = field(default_factory=Counter)
    deleted: Counter = field(default_factory=Counter)

    def add(self, row: Row) -> None:
        self.added[row.label] += row.added
        self.deleted[row.label] += row.deleted

    def value(
        self,
        metric: str = "churn",
        basis: str = "non_blank",
        labels: tuple[str, ...] | None = None,
    ) -> int:
        def pick(counter: Counter) -> int:
            if labels is not None:
                return sum(counter[label] for label in labels)
            total = sum(counter.values())
            return total - counter["blank"] if basis == "non_blank" else total

        added, deleted = pick(self.added), pick(self.deleted)
        return {"churn": added + deleted, "added": added, "deleted": deleted}[metric]


@dataclass
class Group:
    key: str
    title: str = ""
    intent: str = ""
    categories: dict[str, Counts] = field(default_factory=dict)
    moved: int = 0

    def total(self, metric: str, basis: str) -> int:
        return sum(c.value(metric, basis) for c in self.categories.values())


def _bucket(row: Row, by: str) -> str:
    if by == "commit":
        return row.sha
    if by == "intent":
        return row.intent
    if by == "total":
        return "total"
    when = datetime.fromisoformat(row.date)
    if by == "week":
        year, week, _ = when.isocalendar()
        return f"{year}-W{week:02d}"
    if by == "month":
        return f"{when.year}-{when.month:02d}"
    raise ValueError(f"unknown grouping {by!r}")


def aggregate(results: list[CommitResult], by: str, level: str) -> list[Group]:
    groups: dict[str, Group] = {}
    if by == "commit":
        # Seeded from the results: commits without rows must still appear, with zeros.
        for r in results:
            groups[r.commit.sha] = Group(r.commit.sha, r.commit.subject, r.intent)
    seen_moves: set[tuple[str, str, str]] = set()
    for row in rows_from(results):
        key = _bucket(row, by)
        group = groups.setdefault(key, Group(key))
        category = (
            row.category
            if level == "top" or not row.subcategory
            else f"{row.category}/{row.subcategory}"
        )
        group.categories.setdefault(category, Counts()).add(row)
        if "moved" in row.flags and (key, row.sha, row.path) not in seen_moves:
            seen_moves.add((key, row.sha, row.path))
            group.moved += 1
    if by == "commit":
        return list(groups.values())
    return sorted(groups.values(), key=lambda g: g.key)


def merge_groups(groups: list[Group], key: str = "total") -> Group:
    merged = Group(key)
    for group in groups:
        merged.moved += group.moved
        for category, counts in group.categories.items():
            target = merged.categories.setdefault(category, Counts())
            target.added.update(counts.added)
            target.deleted.update(counts.deleted)
    return merged
