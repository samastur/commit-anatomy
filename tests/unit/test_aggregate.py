from collections import Counter

from result_helpers import EMPTY, R1, R2, commit, file

from commit_anatomy.aggregate import Counts, aggregate, merge_groups
from commit_anatomy.analysis import CommitResult


def test_counts_values():
    c = Counts(Counter({"code": 6, "comment": 2, "blank": 2}), Counter({"code": 1}))
    assert c.value("added", "non_blank") == 8
    assert c.value("added", "total") == 10
    assert c.value("churn", "non_blank") == 9
    assert c.value("deleted", "total") == 1
    assert c.value("churn", "non_blank", ("code", "unlexed")) == 7
    assert c.value("added", "non_blank", ("comment", "docstring")) == 2


def test_by_commit_keeps_every_commit_in_order_including_empty_ones():
    groups = aggregate([R1, R2, EMPTY], "commit", "top")
    assert [(g.key, g.intent, g.title) for g in groups] == [
        ("c1", "feat", "feat: x"),
        ("c2", "fix", "fix: y"),
        ("c3", "chore", "chore: nothing"),
    ]
    assert set(groups[0].categories) == {"code", "docs"}
    assert groups[2].categories == {}
    assert groups[0].total("churn", "non_blank") == 12


def test_sub_level_and_intent_grouping():
    groups = {g.key: g for g in aggregate([R1, R2], "intent", "sub")}
    assert set(groups) == {"feat", "fix"}
    assert set(groups["feat"].categories) == {"code/app", "docs/dev"}


def test_week_month_and_total_buckets():
    assert [g.key for g in aggregate([R1, R2], "week", "top")] == ["2026-W02", "2026-W03"]
    assert [g.key for g in aggregate([R1, R2], "month", "top")] == ["2026-01"]
    (total,) = aggregate([R1, R2], "total", "top")
    assert total.categories["tests"].value("added", "non_blank") == 4


def test_moved_files_are_counted_once():
    moved = CommitResult(
        commit("c4", 15), "refactor", (file("b.txt", "unclassified", flags=("moved", "rename")),)
    )
    assert aggregate([moved], "commit", "top")[0].moved == 1


def test_merge_groups():
    total = merge_groups(aggregate([R1, R2], "commit", "top"))
    assert total.key == "total"
    assert total.categories["code"].value("added", "total") == 10
    assert total.total("churn", "non_blank") == 16
