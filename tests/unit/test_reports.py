import csv
import io
import json
from collections import Counter

from result_helpers import R1, R2, commit, file
from rich.console import Console

from commit_anatomy.aggregate import aggregate
from commit_anatomy.analysis import CommitResult, FileResult, SideResult
from commit_anatomy.model import FileChange, Match
from commit_anatomy.report.explain import render_explain
from commit_anatomy.report.table import render_table
from commit_anatomy.report.tabular import to_csv, to_json
from commit_anatomy.rows import FIELDS, rows_from
from commit_anatomy.taxonomy import Taxonomy


def text_of(renderable) -> str:
    console = Console(record=True, width=200)
    console.print(renderable)
    return console.export_text()


def table_for(results, by="commit"):
    return text_of(
        render_table(
            aggregate(results, by, "top"), Taxonomy(), metric="churn", basis="non_blank", by=by
        )
    )


def test_table_shows_counts_shares_and_code_split():
    out = table_for([R1, R2])
    assert "c1" in out and "[feat]" in out and "feat: x" in out  # brackets survive: no markup
    assert "+8 −1" in out and "75%" in out  # code is 9 of the commit's 12 lines
    assert "code 7 · comments 2" in out
    assert "total" in out
    assert "unclassified: 0 lines" in out
    header = next(line for line in out.splitlines() if "docs" in line and "tests" in line)
    assert header.index("docs") < header.index("tests") < header.index("code")


def test_table_reports_unclassified_lines():
    r = CommitResult(commit("c9", 6), "unknown", (file("x.bin", "unclassified", {"raw": 5}),))
    assert "unclassified: 5 lines" in table_for([r])


def test_json_and_csv_rows():
    rows = rows_from([R1])
    data = json.loads(to_json(rows))
    assert {d["path"] for d in data} == {"src/a.py", "README.md"}
    assert set(data[0]) == set(FIELDS)
    assert isinstance(data[0]["flags"], list)
    parsed = list(csv.DictReader(io.StringIO(to_csv(rows))))
    assert len(parsed) == len(rows) and list(parsed[0]) == FIELDS
    assert to_json([]) == "[]"


def test_explain_shows_rule_origin_pattern_counts_and_flags():
    old = Match("docs/dev", "core:docs", "plugin:core", "**/*.{md,rst}")
    new = Match("docs/user", "django:user-docs", "plugin:django", "docs/manual/**")
    change = FileChange(
        "README.md", "docs/manual/README.md", "R", False, 75, "a", "b", ((4, 1),), ((4, 1),)
    )
    result = CommitResult(
        commit("c5", 7, "docs: move [manual]"),
        "docs",
        (
            FileResult(
                change,
                SideResult(old, Counter({"raw": 1})),
                SideResult(new, Counter({"raw": 1})),
                frozenset({"rename", "reclassified"}),
            ),
        ),
    )
    out = text_of(render_explain(result))
    for fragment in (
        "README.md → docs/manual/README.md",
        "docs/dev",
        "docs/user",
        "core:docs",
        "plugin:django",
        "docs/manual/**",
        "−raw 1",
        "+raw 1",
        "reclassified",
        "[manual]",
    ):
        assert fragment in out, fragment
    assert "core:docs" not in text_of(render_explain(result, "src/**"))
