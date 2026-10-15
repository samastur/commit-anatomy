from result_helpers import R1, R2

from commit_anatomy.aggregate import aggregate
from commit_anatomy.report.trend import render_trend
from commit_anatomy.taxonomy import Taxonomy


def html_for(results, title="repo: change composition per week"):
    groups = aggregate(results, "week", "top")
    return render_trend(groups, Taxonomy(), metric="churn", basis="non_blank", title=title)


def test_trend_page():
    html = html_for([R1, R2])
    assert html.startswith("<!doctype html>")
    assert "<title>repo: change composition per week</title>" in html
    assert html.count("<svg") == 2
    assert "2026-W02" in html and "2026-W03" in html
    assert "2026-W02 · code: 9 lines (75%)" in html
    assert "prefers-color-scheme: dark" in html
    assert "<script" not in html and "http" not in html


def test_trend_escapes_text():
    assert "&lt;x&gt;" in html_for([R1], title="<x>")


def test_trend_without_data():
    assert "No commits" in html_for([])
