"""Hand-built analysis results for aggregation and report tests."""

from collections import Counter
from datetime import UTC, datetime

from commit_anatomy.analysis import CommitResult, FileResult, SideResult
from commit_anatomy.model import Commit, FileChange, Match


def commit(sha: str, day: int, subject: str = "feat: x") -> Commit:
    when = datetime(2026, 1, day, 12, tzinfo=UTC)
    return Commit(sha, (), "T", "t@example.com", when, when, subject)


def file(path, category, added=None, deleted=None, flags=()) -> FileResult:
    match = Match(category, "r", "o", "")
    change = FileChange(path, path, "M", False, None, "a", "b", (), ())
    return FileResult(
        change,
        SideResult(match, Counter(deleted or {})),
        SideResult(match, Counter(added or {})),
        frozenset(flags),
    )


# 2026-01-05 is in ISO week 2, 2026-01-13 in week 3.
R1 = CommitResult(
    commit("c1", 5),
    "feat",
    (
        file("src/a.py", "code/app", {"code": 6, "comment": 2, "blank": 2}, {"code": 1}),
        file("README.md", "docs/dev", {"raw": 3, "blank": 1}),
    ),
)
R2 = CommitResult(
    commit("c2", 13, "fix: y"), "fix", (file("tests/t.py", "tests/unit", {"code": 4}),)
)
EMPTY = CommitResult(commit("c3", 14, "chore: nothing"), "chore", ())
