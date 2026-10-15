"""Classify paths with plugins against an in-memory repository, as the analyzer does."""

from datetime import UTC, datetime

from commit_anatomy.classify import Classifier, Windows, order_rules
from commit_anatomy.lines import split_lines
from commit_anatomy.model import Commit
from commit_anatomy.plugins.base import StaticRepoView, active_scopes
from commit_anatomy.taxonomy import Taxonomy

NOW = datetime(2026, 1, 5, tzinfo=UTC)


def classify_paths(plugins, files: dict[str, str | bytes]) -> dict[str, str]:
    view = StaticRepoView(files)
    active = active_scopes(plugins, view)
    entries = order_rules([], [(scope, plugin.rules()) for plugin, scope in active])

    def head(path: str) -> list[str]:
        return split_lines((view.read(path) or b"").decode("utf-8", "replace"))[:20]

    classifier = Classifier(entries, Taxonomy(), Windows(lambda a, b: False), head=head)
    commit = Commit("c", (), "T", "t@example.com", NOW, NOW, "m")
    # The path doubles as the blob id, so content rules read that file's head.
    return {path: classifier.classify(path, path, commit).category for path in files}
