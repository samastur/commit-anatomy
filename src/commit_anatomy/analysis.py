"""From cached facts to classified, line-counted file sides. Nothing derived is cached."""

from __future__ import annotations

import posixpath
from collections import Counter
from dataclasses import dataclass

from .cache import FACTS_KEY
from .classify import Classifier, Windows, order_rules
from .config import EffectiveConfig
from .intent import classify_intent
from .lines import LineLabeler, blank_labels, count_ranges, decode, split_lines
from .model import Commit, CommitFacts, FileChange, Label, LineRanges, Match, Scope
from .plugins.base import Plugin, language_hint

HEAD_LINES = 20


@dataclass(frozen=True)
class SideResult:
    match: Match
    counts: Counter[str]


@dataclass(frozen=True)
class FileResult:
    change: FileChange
    old: SideResult | None
    new: SideResult | None
    flags: frozenset[str]


@dataclass(frozen=True)
class CommitResult:
    commit: Commit
    intent: str
    files: tuple[FileResult, ...]


class Analyzer:
    def __init__(
        self,
        repo,
        cache,
        config: EffectiveConfig,
        active: list[tuple[Plugin, Scope]],
        labeler: LineLabeler,
    ):
        self.repo = repo
        self.cache = cache
        self.config = config
        self.active = active
        self.labeler = labeler
        self.unlexed_exts: set[str] = set()
        entries = order_rules(config.rules, [(scope, plugin.rules()) for plugin, scope in active])
        self.classifier = Classifier(
            entries, config.taxonomy, Windows(repo.is_ancestor), head=self._head
        )

    def facts(self, shas: list[str]) -> list[CommitFacts]:
        found = {sha: self.cache.get_commit(sha, FACTS_KEY) for sha in shas}
        missing = [sha for sha, facts in found.items() if facts is None]
        for commit in self.repo.commits(missing) if missing else []:
            facts = CommitFacts(commit, tuple(self.repo.diff(commit)))
            self.cache.put_commit(commit.sha, FACTS_KEY, facts)
            found[commit.sha] = facts
        return [found[sha] for sha in shas]

    def analyze(self, shas: list[str]) -> list[CommitResult]:
        results = []
        for facts in self.facts(shas):
            commit = facts.commit
            intent = classify_intent(
                commit.sha,
                commit.message,
                self.config.intent.keywords,
                self.config.intent.overrides,
            )
            files = tuple(self._file(commit, change) for change in facts.changes)
            results.append(CommitResult(commit, intent, files))
        self.cache.flush()
        return results

    def _file(self, commit: Commit, ch: FileChange) -> FileResult:
        old = (
            self._side(commit, ch, ch.old_path, ch.old_blob, ch.removed_lines)
            if ch.old_path is not None
            else None
        )
        new = (
            self._side(commit, ch, ch.new_path, ch.new_blob, ch.added_lines)
            if ch.new_path is not None
            else None
        )
        sides = [s for s in (old, new) if s is not None]
        flags = set()
        if ch.binary:
            flags.add("binary")
        if ch.status == "R":
            flags.add("rename")
            if ch.similarity == 100:
                flags.add("moved")
        if old and new and old.match.category != new.match.category:
            flags.add("reclassified")
        if any(s.match.category.partition("/")[2] == "generated" for s in sides):
            flags.add("generated")
        if any(s.counts["unlexed"] for s in sides):
            flags.add("unlexed")
        return FileResult(ch, old, new, frozenset(flags))

    def _side(
        self, commit: Commit, ch: FileChange, path: str, blob: str | None, ranges: LineRanges
    ) -> SideResult:
        if ch.submodule:
            category = self.config.taxonomy.remap("supporting/deps")
            return SideResult(Match(category, "core:submodule", "core", "submodule"), Counter())
        match = self.classifier.classify(path, None if ch.binary else blob, commit)
        if ch.binary or not ranges or blob is None:
            return SideResult(match, Counter())
        return SideResult(match, count_ranges(self._labels(path, blob, match.category), ranges))

    def _labels(self, path: str, blob: str, category: str) -> list[Label]:
        split = self.config.taxonomy.split_lines(category)
        if split:
            alias = self.labeler.resolve(path, language_hint(self.active, path))
            key = alias or "none"
        else:
            alias, key = None, "blank"
        labels = self.cache.get_labels(blob, key, self.labeler.version)
        if labels is None:
            content = self.repo.read_blob(blob)
            if content is None:
                return []
            if key == "blank":
                labels = blank_labels(decode(content))
            else:
                labels = self.labeler.label(content, alias)
            self.cache.put_labels(blob, key, self.labeler.version, labels)
        if split and "unlexed" in labels:
            base = posixpath.basename(path)
            self.unlexed_exts.add(posixpath.splitext(base)[1] or base)
        return labels

    def _head(self, blob: str) -> list[str]:
        head = self.cache.get_head(blob)
        if head is None:
            content = self.repo.read_blob(blob)
            head = split_lines(decode(content))[:HEAD_LINES] if content is not None else []
            self.cache.put_head(blob, head)
        return head
