"""Plain data shared by every layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Label = Literal["code", "comment", "docstring", "blank", "unlexed", "raw"]
LABELS: tuple[Label, ...] = ("code", "comment", "docstring", "blank", "unlexed", "raw")

# (first line, number of lines), 1-based, as in a unified diff hunk header.
LineRanges = tuple[tuple[int, int], ...]

Stage = Literal["pre", "normal"]


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    author_date: datetime
    committer_date: datetime
    message: str

    @property
    def subject(self) -> str:
        return self.message.strip().split("\n", 1)[0].strip()

    @property
    def body(self) -> str:
        parts = self.message.strip().split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""


@dataclass(frozen=True)
class FileChange:
    old_path: str | None  # None for an added file
    new_path: str | None  # None for a deleted file
    status: str  # A, D, M, R or T
    binary: bool
    similarity: int | None  # renames only
    old_blob: str | None
    new_blob: str | None
    removed_lines: LineRanges
    added_lines: LineRanges
    submodule: bool = False

    @property
    def path(self) -> str:
        return self.new_path if self.new_path is not None else self.old_path or ""


@dataclass(frozen=True)
class CommitFacts:
    commit: Commit
    changes: tuple[FileChange, ...]


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    paths: tuple[str, ...] = ()
    content: str | None = None
    content_lines: int = 5
    since: str | None = None
    until: str | None = None
    stage: Stage = "normal"


@dataclass(frozen=True)
class Scope:
    plugin: str
    root: str  # "" is the repository root; never a trailing slash

    @property
    def depth(self) -> int:
        return 0 if not self.root else self.root.count("/") + 1


@dataclass(frozen=True)
class Match:
    category: str
    rule_id: str
    origin: str
    matched: str  # the glob and/or "content:<regex>" that matched; "" for the fallback
    root: str = ""  # scope root the rule's paths are relative to
