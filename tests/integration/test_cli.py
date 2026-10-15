import json

import pytest
from helpers import seed_project
from typer.testing import CliRunner

from commit_anatomy.cli import app

runner = CliRunner()


def run(*args: str):
    return runner.invoke(app, list(args))


@pytest.fixture
def project(repo):
    return str(seed_project(repo).path)


def test_analyze_defaults_to_the_head_commit(project):
    r = run("analyze", "-C", project)
    assert r.exit_code == 0, r.output
    assert "fix: tweak" in r.stdout and "feat: start" not in r.stdout
    assert "+1 −0" in r.stdout


def test_analyze_history_as_json(project):
    r = run("analyze", "-C", project, "--history", "-f", "json")
    assert r.exit_code == 0, r.output
    assert {d["subject"] for d in json.loads(r.stdout)} == {"feat: start", "fix: tweak"}


def test_analyze_csv(project):
    r = run("analyze", "-C", project, "--history", "-f", "csv")
    assert r.stdout.splitlines()[0].startswith("sha,date,author,intent")


def test_analyze_total_by_subcategory(project):
    r = run("analyze", "-C", project, "--history", "--by", "total", "--level", "sub")
    assert r.exit_code == 0, r.output
    assert "code/app" in r.stdout and "docs/dev" in r.stdout


def test_a_selection_without_commits_is_not_an_error(project):
    r = run("analyze", "-C", project, "HEAD..HEAD")
    assert r.exit_code == 0
    assert "No commits matched." in r.stderr and r.stdout.strip() == ""
    assert run("analyze", "-C", project, "HEAD..HEAD", "-f", "json").stdout.strip() == "[]"


def test_exit_codes(project, tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    assert run("analyze", "-C", str(outside)).exit_code == 1
    bad = run("analyze", "-C", project, "nope")
    assert bad.exit_code == 1 and "nope" in bad.stderr
    assert run("analyze", "-C", project, "--by", "year").exit_code == 2
    (tmp_path / "repo" / ".commit-anatomy.toml").write_text("surprise = 1\n")
    config = run("analyze", "-C", project)
    assert config.exit_code == 2 and "surprise" in config.stderr


def test_explain(project):
    r = run("explain", "HEAD", "-C", project)
    assert r.exit_code == 0, r.output
    assert "src/app.py" in r.stdout and "django:app" in r.stdout


def test_detect_and_rules(project):
    detected = run("detect", "-C", project)
    assert "django" in detected.stdout and "core" in detected.stdout
    assert "Config files: none" in detected.stdout
    rules = run("rules", "-C", project).stdout
    assert rules.index("core:generated") < rules.index("django:app") < rules.index("core:docs")


def test_cache_commands_and_no_cache(project, isolated_env):
    cache_file = isolated_env / "cache" / "cache.sqlite"
    assert run("analyze", "-C", project, "--no-cache").exit_code == 0
    assert not cache_file.exists()
    assert run("analyze", "-C", project).exit_code == 0
    assert cache_file.exists()
    info = run("cache", "info")
    assert info.exit_code == 0 and "commit_facts" in info.stdout
    assert run("cache", "clear").exit_code == 0
    assert run("cache", "clear", "--blobs").exit_code == 0


@pytest.fixture
def unlexed_project(repo):
    repo.commit(
        "feat: odd language",
        {
            ".commit-anatomy.toml": '[[rules]]\npaths = ["**/*.zzz"]\ncategory = "code/app"\n',
            "a.zzz": "one\ntwo\n",
            "b.zzz": "three\n",
        },
    )
    return str(repo.path)


@pytest.mark.parametrize("fmt", ["table", "json", "csv"])
def test_unlexed_extensions_warn_once_on_stderr(unlexed_project, fmt):
    r = run("analyze", "-C", unlexed_project, "-f", fmt)
    assert r.exit_code == 0, r.output
    warning = "warning: lines in .zzz files could not be lexed; counted as unlexed"
    assert r.stderr.count(warning) == 1
    assert warning not in r.stdout
    if fmt == "json":
        assert json.loads(r.stdout)


def test_explain_warns_about_unlexed_extensions(unlexed_project):
    r = run("explain", "HEAD", "-C", unlexed_project)
    assert r.exit_code == 0, r.output
    assert r.stderr.count("lines in .zzz files could not be lexed") == 1
