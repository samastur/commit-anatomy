import json
import tomllib
from datetime import date

from typer.testing import CliRunner

from commit_anatomy.analysis import Analyzer
from commit_anatomy.cache import MemoryCache
from commit_anatomy.cli import app
from commit_anatomy.config import EffectiveConfig, config_home
from commit_anatomy.git import GitRepo
from commit_anatomy.init import Suggestion, render_config, suggest_splits, suggest_unclassified
from commit_anatomy.lexers.pygments_backend import PygmentsLabeler
from commit_anatomy.plugins.base import GitRepoView, active_scopes, load_plugins
from commit_anatomy.taxonomy import Taxonomy

runner = CliRunner()


def results_for(repo):
    git = GitRepo(repo.path)
    active = active_scopes(load_plugins(), GitRepoView(git))
    analyzer = Analyzer(git, MemoryCache(), EffectiveConfig.default(), active, PygmentsLabeler())
    return analyzer.analyze(git.list_commits(["HEAD"], history=True))


def test_suggestions_for_unclassified_directories_and_rule_splits(repo):
    repo.commit(
        "feat: start",
        {
            "README.md": "# R\n",
            "docs/manual/a.md": "a\nb\nc\nd\n",
            "docs/dev/b.md": "a\nb\nc\n",
            "tools/build.sh": "echo 1\necho 2\n",
            "notes.txt": "n\n",
            "weird/blob.qqq": "q\n",
        },
    )
    results = results_for(repo)
    unclassified = suggest_unclassified(results, Taxonomy())
    assert [(s.paths, s.category) for s in unclassified] == [
        (("notes.txt",), "docs/dev"),
        (("tools/**",), "code/tooling"),
        (("weird/**",), "unclassified"),
    ]
    assert "1 unclassified file(s), 2 non-blank lines" in unclassified[1].why
    assert "directory name 'tools'" in unclassified[1].why
    splits = suggest_splits(results, Taxonomy())
    assert [(s.paths, s.category) for s in splits] == [(("docs/manual/**",), "docs/user")]
    assert "core:docs" in splits[0].why and "57%" in splits[0].why


def test_mixed_directory_gets_file_level_suggestions(repo):
    repo.commit("feat: lib", {"lib/README.md": "r\n", "lib/x.go": "package x\n"})
    (only,) = suggest_unclassified(results_for(repo), Taxonomy())
    assert (only.paths, only.category) == (("lib/x.go",), "code/app")


def test_render_config_is_toml_that_config_loading_accepts():
    text = render_config(
        unclassified=[Suggestion(("tools/**",), "code/tooling", "why one")],
        splits=[Suggestion(("docs/manual/**",), "docs/user", "why two")],
        active=[],
        commits=3,
        match={"remote": "github.com/me/x"},
        today=date(2026, 10, 15),
    )
    data = tomllib.loads(text)
    assert data["match"] == {"remote": "github.com/me/x"}
    assert [r["paths"] for r in data["rules"]] == [["docs/manual/**"], ["tools/**"]]
    assert "# why: why one" in text and "2026-10-15" in text and "3 commit(s)" in text


def test_render_config_without_suggestions():
    text = render_config(
        unclassified=[], splits=[], active=[], commits=1, match=None, today=date(2026, 10, 15)
    )
    assert tomllib.loads(text) == {}
    assert "No suggestions" in text


def test_init_writes_user_config_that_leaves_nothing_unclassified(repo):
    repo.commit(
        "feat: start",
        {
            "tools/build.sh": "echo\n",
            "notes.txt": "n\n",
            "docs/manual/a.md": "a\nb\n",
            "docs/dev/b.md": "c\nd\n",
        },
    )
    path = str(repo.path)
    first = runner.invoke(app, ["init", "-C", path])
    assert first.exit_code == 0, first.output
    target = config_home() / "repos" / "repo.toml"
    assert "path = " in target.read_text()  # no remote, so it matches by path
    again = runner.invoke(app, ["init", "-C", path])
    assert again.exit_code == 2 and "--force" in again.stderr
    assert runner.invoke(app, ["init", "-C", path, "--force"]).exit_code == 0
    after = runner.invoke(app, ["analyze", "-C", path, "--history", "-f", "json"])
    rows = json.loads(after.stdout)
    assert rows and all(row["category"] != "unclassified" for row in rows)
    assert {r["subcategory"] for r in rows if r["path"] == "docs/manual/a.md"} == {"user"}


def test_init_repo_file_has_no_match_table(repo):
    repo.commit("feat: start", {"tools/build.sh": "echo\n"})
    r = runner.invoke(app, ["init", "-C", str(repo.path), "--repo"])
    assert r.exit_code == 0, r.output
    text = (repo.path / ".commit-anatomy.toml").read_text()
    assert "[match]" not in text and '"tools/**"' in text


def test_init_force_still_suggests_paths_only_the_replaced_file_classified(repo):
    repo.commit("feat: start", {"tools/build.sh": "echo\n", "notes.txt": "n\n"})
    path = str(repo.path)
    assert runner.invoke(app, ["init", "-C", path]).exit_code == 0
    target = config_home() / "repos" / "repo.toml"
    assert runner.invoke(app, ["init", "-C", path, "--force"]).exit_code == 0
    text = target.read_text()
    assert "No suggestions" not in text and '"tools/**"' in text
    assert runner.invoke(app, ["init", "-C", path, "--force"]).exit_code == 0
    assert '"tools/**"' in target.read_text()


def test_init_repo_force_ignores_the_in_repo_file_it_replaces(repo):
    repo.commit("feat: start", {"tools/build.sh": "echo\n"})
    path = str(repo.path)
    assert runner.invoke(app, ["init", "-C", path, "--repo"]).exit_code == 0
    assert runner.invoke(app, ["init", "-C", path, "--repo", "--force"]).exit_code == 0
    assert '"tools/**"' in (repo.path / ".commit-anatomy.toml").read_text()


def test_init_force_keeps_the_replaced_files_taxonomy_and_intent(repo):
    repo.commit(
        "feat: start",
        {"tools/build.sh": "echo\n", "docs/a/x.md": "a\nb\n", "docs/b/y.md": "c\nd\n"},
    )
    path = str(repo.path)
    target = config_home() / "repos" / "repo.toml"
    target.parent.mkdir(parents=True)
    target.write_text(
        f"[match]\npath = {json.dumps(path)}\n\n"
        "[taxonomy.data]\nsubcategories = []\n\n"
        '[taxonomy.remap]\n"docs/dev" = "data"\n\n'
        '[intent.keywords]\nfeat = ["add"]\n\n'
        '[[rules]]\npaths = ["tools/**"]\ncategory = "data"\n'
    )
    old = tomllib.loads(target.read_text())
    assert runner.invoke(app, ["init", "-C", path, "--force"]).exit_code == 0
    new = tomllib.loads(target.read_text())
    assert new["taxonomy"] == old["taxonomy"] and new["intent"] == old["intent"]
    after = runner.invoke(app, ["analyze", "-C", path, "--history", "-f", "json"])
    assert after.exit_code == 0, after.output


def history_rows(path: str) -> list[dict]:
    r = runner.invoke(app, ["analyze", "-C", path, "--history", "-f", "json"])
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout)


def unclassified_after_init(repo) -> list[str]:
    path = str(repo.path)
    r = runner.invoke(app, ["init", "-C", path])
    assert r.exit_code == 0, r.output
    return sorted({row["path"] for row in history_rows(path) if row["category"] == "unclassified"})


def test_init_escapes_glob_characters_in_suggested_paths(repo):
    repo.commit(
        "feat: start",
        {
            "README.md": "# R\n",
            "app/[locale]/page.tsx": "export {}\n",
            "lib/README.md": "# L\n",
            "lib/a{b,c}.go": "package lib\n",
            "lib/x*.go": "package lib\n",
            "lib/q?.go": "package lib\n",
        },
    )
    assert unclassified_after_init(repo) == []


def test_init_suggests_root_files_one_by_one(repo):
    repo.commit("feat: start", {"Makefile": "all:\n\tgo build\n", "main.go": "package main\n"})
    assert unclassified_after_init(repo) == []
    text = (config_home() / "repos" / "repo.toml").read_text()
    assert '"/**"' not in text


def categories_by_file(rows: list[dict]) -> dict[tuple[str, str], set[str]]:
    seen: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        seen.setdefault((row["sha"], row["path"]), set()).add(
            f"{row['category']}/{row['subcategory']}"
        )
    return seen


def test_applying_init_never_reclassifies_a_classified_file(repo):
    # Modelled on hrcek-extension: colocated tests and generated files under src/, whose
    # directories each carry a large share of the coarse src/** rule.
    body = "".join(f"export const v{i} = {i}\n" for i in range(10))
    repo.commit(
        "feat: start",
        {
            "package.json": '{"devDependencies": {"wxt": "1"}}\n',
            "src/a/x.ts": body,
            "src/a/x.test.ts": body,
            "src/b/y.ts": body,
            "src/b/y.test.ts": body,
            "src/b/types.gen.ts": "// @generated by a tool\n" + body,
        },
    )
    path = str(repo.path)
    before = categories_by_file(history_rows(path))
    assert before[next(k for k in before if k[1] == "src/a/x.test.ts")] == {"tests/unit"}
    assert runner.invoke(app, ["init", "-C", path]).exit_code == 0
    after = categories_by_file(history_rows(path))
    changed = {
        key: (before[key], after[key])
        for key in before
        if "unclassified/" not in before[key] and before[key] != after[key]
    }
    assert changed == {}


def test_no_op_splits_are_not_suggested(repo):
    body = "".join(f"export const v{i} = {i}\n" for i in range(10))
    repo.commit(
        "feat: start",
        {
            "package.json": '{"devDependencies": {"wxt": "1"}}\n',
            "src/a/x.ts": body,
            "src/b/y.ts": body,
        },
    )
    assert suggest_splits(results_for(repo), Taxonomy()) == []


def test_splits_that_would_capture_another_rules_files_are_not_suggested(repo):
    repo.commit(
        "feat: start",
        {
            "docs/manual/a.md": "a\nb\nc\nd\n",
            "docs/manual/CLAUDE.md": "agents\n",  # core:llm-docs, not core:docs
            "docs/guide/b.md": "a\nb\nc\n",
            "docs/dev/c.md": "a\nb\nc\n",
        },
    )
    splits = suggest_splits(results_for(repo), Taxonomy())
    assert [(s.paths, s.category) for s in splits] == [(("docs/guide/**",), "docs/user")]
