from commit_anatomy.model import Scope
from commit_anatomy.plugins.base import (
    StaticRepoView,
    active_scopes,
    language_hint,
    load_plugins,
    rule,
)
from commit_anatomy.plugins.core import plugin as core


class FakePlugin:
    name = "fake"

    def detect(self, repo):
        return [Scope("fake", "web")] if "web/manage.py" in repo.files() else []

    def rules(self):
        return []

    def languages(self):
        return {"**/*.html": "html+django"}


def test_static_repo_view():
    view = StaticRepoView({"b.txt": "x", "a.txt": b"y"})
    assert view.files() == ["a.txt", "b.txt"]
    assert view.read("a.txt") == b"y" and view.read("b.txt") == b"x"
    assert view.read("zz") is None


def test_core_is_always_active_at_the_root():
    assert active_scopes([core], StaticRepoView({})) == [(core, Scope("core", ""))]


def test_detection_decides_which_scopes_are_active():
    view = StaticRepoView({"web/manage.py": ""})
    assert [s for _, s in active_scopes([core, FakePlugin()], view)] == [
        Scope("core", ""),
        Scope("fake", "web"),
    ]
    assert [s for _, s in active_scopes([FakePlugin()], StaticRepoView({}))] == []


def test_load_plugins_finds_core_through_entry_points():
    assert "core" in [p.name for p in load_plugins()]


def test_language_hint_is_scoped():
    active = active_scopes([core, FakePlugin()], StaticRepoView({"web/manage.py": ""}))
    assert language_hint(active, "web/templates/a.html") == "html+django"
    assert language_hint(active, "other/a.html") is None


def test_rule_helper_and_namespaced_ids():
    r = rule("x:y", "docs/dev", "a", "b", stage="pre")
    assert (r.paths, r.stage, r.content_lines) == (("a", "b"), "pre", 5)
    assert all(r.id.startswith("core:") for r in core.rules())
