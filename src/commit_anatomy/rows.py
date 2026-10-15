"""Tidy rows: one per (commit, file, category side, line label). The canonical output."""

from __future__ import annotations

from dataclasses import dataclass, fields

from .analysis import CommitResult
from .model import LABELS, Match


@dataclass(frozen=True)
class Row:
    sha: str
    date: str
    author: str
    intent: str
    subject: str
    path: str
    old_path: str
    status: str
    category: str
    subcategory: str
    rule_id: str
    rule_origin: str
    label: str
    added: int
    deleted: int
    flags: tuple[str, ...]


FIELDS = [f.name for f in fields(Row)]


def rows_from(results: list[CommitResult]) -> list[Row]:
    rows: list[Row] = []
    for result in results:
        commit = result.commit
        for f in result.files:
            ch = f.change
            base = {
                "sha": commit.sha,
                "date": commit.author_date.isoformat(),
                "author": commit.author_name,
                "intent": result.intent,
                "subject": commit.subject,
                "path": ch.path,
                "old_path": ch.old_path if ch.old_path and ch.old_path != ch.path else "",
                "status": ch.status,
                "flags": tuple(sorted(f.flags)),
            }
            # Added lines come from the new side, deleted lines from the old side.
            cells: dict[tuple[Match, str], list[int]] = {}
            for side, index in ((f.new, 0), (f.old, 1)):
                if side is None:
                    continue
                for label, count in side.counts.items():
                    if count:
                        cells.setdefault((side.match, label), [0, 0])[index] += count
            if not cells:  # moves, binaries, mode changes, empty files: one zero row
                side = f.new or f.old
                assert side is not None
                cells[(side.match, "raw")] = [0, 0]
            ordered = sorted(
                cells.items(), key=lambda kv: (kv[0][0].category, LABELS.index(kv[0][1]))
            )
            for (match, label), (added, deleted) in ordered:
                top, _, sub = match.category.partition("/")
                rows.append(
                    Row(
                        **base,
                        category=top,
                        subcategory=sub,
                        rule_id=match.rule_id,
                        rule_origin=match.origin,
                        label=label,
                        added=added,
                        deleted=deleted,
                    )
                )
    return rows
