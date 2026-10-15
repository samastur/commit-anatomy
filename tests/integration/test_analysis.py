import os
from collections import Counter

from helpers import APP, ScriptedRepo

from commit_anatomy.analysis import Analyzer
from commit_anatomy.cache import MemoryCache, SqliteCache
from commit_anatomy.config import EffectiveConfig, load_config
from commit_anatomy.git import GitRepo
from commit_anatomy.lexers.pygments_backend import PygmentsLabeler
from commit_anatomy.plugins.base import GitRepoView, active_scopes, load_plugins
from commit_anatomy.rows import rows_from


class SpyGit:
    """Counts every call the analyzer makes into git."""

    def __init__(self, git):
        self._git = git
        self.calls = Counter()

    def commits(self, shas):
        self.calls["commits"] += 1
        return self._git.commits(shas)

    def diff(self, commit):
        self.calls["diff"] += 1
        return self._git.diff(commit)

    def read_blob(self, oid):
        self.calls["read_blob"] += 1
        return self._git.read_blob(oid)

    def is_ancestor(self, ancestor, descendant):
        self.calls["is_ancestor"] += 1
        return self._git.is_ancestor(ancestor, descendant)


class SpyLabeler(PygmentsLabeler):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def label(self, content, alias):
        self.calls += 1
        return super().label(content, alias)


def make_analyzer(repo, cache=None, config=None, labeler=None, git=None):
    active = active_scopes(load_plugins(), GitRepoView(GitRepo(repo.path)))
    return Analyzer(
        git or GitRepo(repo.path),
        cache or MemoryCache(),
        config or EffectiveConfig.default(),
        active,
        labeler or PygmentsLabeler(),
    )


def analyze(repo, *shas, **kwargs):
    return make_analyzer(repo, **kwargs).analyze(list(shas))


def summary(results):
    return sorted(
        (
            r.path,
            f"{r.category}/{r.subcategory}".rstrip("/"),
            r.label,
            r.added,
            r.deleted,
            r.flags,
        )
        for r in rows_from(results)
    )


def test_rows_for_a_root_commit(repo):
    sha = repo.commit(
        "feat: start", {"manage.py": "", "src/app.py": APP, "README.md": "# T\n\nText\n"}
    )
    (result,) = analyze(repo, sha)
    assert result.intent == "feat"
    assert summary([result]) == sorted(
        [
            ("README.md", "docs/dev", "blank", 1, 0, ()),
            ("README.md", "docs/dev", "raw", 2, 0, ()),
            ("manage.py", "code/app", "raw", 0, 0, ()),  # empty file: one zero row
            ("src/app.py", "code/app", "blank", 1, 0, ()),
            ("src/app.py", "code/app", "code", 2, 0, ()),
            ("src/app.py", "code/app", "comment", 1, 0, ()),
            ("src/app.py", "code/app", "docstring", 1, 0, ()),
        ]
    )
    code = next(r for r in rows_from([result]) if r.path == "src/app.py" and r.label == "code")
    assert (code.rule_id, code.rule_origin, code.subject) == (
        "django:app",
        "plugin:django",
        "feat: start",
    )


def test_rename_into_another_category_is_reclassified(repo):
    repo.commit("root", {"manage.py": "", "README.md": "a\nb\nc\nd\n"})
    (repo.path / "docs" / "manual").mkdir(parents=True)
    repo.git("mv", "README.md", "docs/manual/README.md")
    sha = repo.commit("docs: move", {"docs/manual/README.md": "a\nb\nc\nD\n"})
    (result,) = analyze(repo, sha)
    (f,) = result.files
    assert f.flags == {"rename", "reclassified"}
    assert (f.old.match.category, f.new.match.category) == ("docs/dev", "docs/user")
    flags = ("reclassified", "rename")
    assert summary([result]) == [
        ("docs/manual/README.md", "docs/dev", "raw", 0, 1, flags),
        ("docs/manual/README.md", "docs/user", "raw", 1, 0, flags),
    ]
    assert rows_from([result])[0].old_path == "README.md"


def test_moves_binaries_mode_changes_and_empty_commits_have_zero_rows(repo):
    repo.commit("root", {"a.txt": "x\n", "logo.png": b"\x89PNG\x00\x01", "run.sh": "echo\n"})
    repo.git("mv", "a.txt", "b.txt")
    os.chmod(repo.path / "run.sh", 0o755)
    repo.write({"logo.png": b"\x89PNG\x00\x02"})
    shuffled = repo.commit("chore: shuffle")
    empty = repo.commit("chore: nothing")
    moved, nothing = analyze(repo, shuffled, empty)
    assert nothing.files == ()
    assert summary([moved]) == sorted(
        [
            ("b.txt", "unclassified", "raw", 0, 0, ("moved", "rename")),
            ("logo.png", "supporting/assets", "raw", 0, 0, ("binary",)),
            ("run.sh", "unclassified", "raw", 0, 0, ()),
        ]
    )


def test_submodule_counts_as_a_dependency(repo, tmp_path):
    sub = ScriptedRepo(tmp_path / "sub")
    sub.commit("s", {"s.txt": "s\n"})
    repo.commit("root", {"a.txt": "1\n"})
    repo.git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub.path), "vendor/sm"
    )
    rows = {r.path: r for r in rows_from(analyze(repo, repo.commit("chore: add submodule")))}
    sm = rows["vendor/sm"]
    assert (sm.category, sm.subcategory, sm.rule_id, sm.rule_origin, sm.added) == (
        "supporting",
        "deps",
        "core:submodule",
        "plugin:core",
        0,
    )


def test_generated_file_detected_by_content(repo):
    sha = repo.commit("feat: gen", {"manage.py": "", "src/api.py": "# @generated by tool\nx = 1\n"})
    rows = [r for r in rows_from(analyze(repo, sha)) if r.path == "src/api.py"]
    assert {(r.category, r.subcategory, r.label, r.added) for r in rows} == {
        ("supporting", "generated", "raw", 2)
    }
    assert all("generated" in r.flags for r in rows)


def test_unknown_language_in_code_is_unlexed(repo):
    sha = repo.commit("feat: data", {"manage.py": "", "src/data.zzz": "a\n\nb\n"})
    rows = {r.label: r for r in rows_from(analyze(repo, sha)) if r.path == "src/data.zzz"}
    assert (rows["unlexed"].added, rows["blank"].added) == (2, 1)
    assert "unlexed" in rows["unlexed"].flags


def test_second_run_with_new_config_needs_no_diffing_reading_or_lexing(repo, tmp_path):
    repo.commit("Add start", {"manage.py": "", "src/app.py": APP, "docs/manual/a.md": "# A\n"})
    repo.commit("Keep going", {"src/app.py": APP + "y = 2\n", "src/gen.py": "# @generated\n"})
    shas = GitRepo(repo.path).list_commits(["HEAD"], history=True)
    cache_path = tmp_path / "c.sqlite"

    first_cache = SqliteCache(cache_path)
    first = analyze(repo, *shas, cache=first_cache)
    first_cache.close()
    assert [r.intent for r in first] == ["feat", "unknown"]

    (repo.path / ".commit-anatomy.toml").write_text(
        '[[rules]]\npaths = ["docs/manual/**"]\ncategory = "docs/dev"\n'
        '[intent.keywords]\nfeat = ["add", "keep"]\n'
    )
    config = load_config(repo.path, None, lambda rev: None)
    git, labeler = SpyGit(GitRepo(repo.path)), SpyLabeler()
    second_cache = SqliteCache(cache_path)
    second = make_analyzer(
        repo, cache=second_cache, config=config, labeler=labeler, git=git
    ).analyze(shas)
    second_cache.close()

    assert git.calls == Counter() and labeler.calls == 0
    assert [r.intent for r in second] == ["feat", "feat"]
    docs = [r for r in rows_from(second) if r.path == "docs/manual/a.md"]
    assert {(r.category, r.subcategory) for r in docs} == {("docs", "dev")}


def test_labeler_version_bump_relexes(repo, tmp_path):
    sha = repo.commit("feat: start", {"manage.py": "", "src/app.py": APP})
    cache = SqliteCache(tmp_path / "c.sqlite")
    analyze(repo, sha, cache=cache)
    labeler = SpyLabeler()
    labeler.version = "a-different-labeler"
    make_analyzer(repo, cache=cache, labeler=labeler).analyze([sha])
    assert labeler.calls == 1
    cache.close()


def test_unlexed_extensions_are_collected_for_split_categories_only(repo):
    sha = repo.commit(
        "feat: data",
        {
            "manage.py": "",
            "src/app.py": APP,
            "src/data.zzz": "a\n\nb\n",
            "Makefile.zzz": "x\n",
            "docs/manual/notes.qqq": "text\n",
            "logo.png": b"\x89PNG\x00\x01",
        },
    )
    analyzer = make_analyzer(repo)
    analyzer.analyze([sha])
    assert ".py" not in analyzer.unlexed_exts
    assert ".zzz" in analyzer.unlexed_exts
    assert ".qqq" not in analyzer.unlexed_exts  # docs/user is not line-split
    assert ".png" not in analyzer.unlexed_exts


def test_unlexed_bare_filename_and_cached_labels(repo, tmp_path):
    sha = repo.commit("feat: x", {"manage.py": "", "src/LICENSEZ": "a\nb\n"})
    cache = SqliteCache(tmp_path / "c.sqlite")
    analyze(repo, sha, cache=cache)
    again = make_analyzer(repo, cache=cache)
    again.analyze([sha])  # labels now come from the cache
    cache.close()
    assert again.unlexed_exts == {"LICENSEZ"}
