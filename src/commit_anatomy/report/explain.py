"""Per-file explanation of a commit: which rule placed each side, and why."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from wcmatch import glob

from ..analysis import CommitResult, SideResult
from ..classify import GLOB_FLAGS
from ..model import LABELS


def _lines(side: SideResult, sign: str) -> str:
    parts = [f"{sign}{label} {side.counts[label]}" for label in LABELS if side.counts[label]]
    return " ".join(parts) or "0"


def render_explain(result: CommitResult, path_glob: str | None = None) -> Table:
    commit = result.commit
    table = Table(title=Text(f"{commit.sha[:12]} [{result.intent}] {commit.subject}"))
    for column in ("file", "status", "side", "category", "rule", "origin", "matched", "lines"):
        table.add_column(column)
    table.add_column("flags")
    for f in result.files:
        ch = f.change
        paths = [p for p in (ch.old_path, ch.new_path) if p]
        if path_glob and not any(glob.globmatch(p, path_glob, flags=GLOB_FLAGS) for p in paths):
            continue
        if ch.old_path and ch.new_path and ch.old_path != ch.new_path:
            name = f"{ch.old_path} → {ch.new_path}"
        else:
            name = ch.path
        first = True
        for side_name, side, sign in (("old", f.old, "−"), ("new", f.new, "+")):
            if side is None:
                continue
            m = side.match
            table.add_row(
                Text(name if first else ""),
                Text(ch.status if first else ""),
                Text(side_name),
                Text(m.category),
                Text(m.rule_id),
                Text(m.origin),
                Text(m.matched),
                Text(_lines(side, sign)),
                Text(", ".join(sorted(f.flags)) if first else ""),
            )
            first = False
    return table
