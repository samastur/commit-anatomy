"""First-match rule engine with scopes, content rules and time windows."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from wcmatch import glob

from .model import Commit, Match, Rule, Scope
from .taxonomy import UNCLASSIFIED, Taxonomy

GLOB_FLAGS = glob.GLOBSTAR | glob.BRACE | glob.DOTGLOB
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")
CORE = "core"

_regex = cache(re.compile)


@dataclass(frozen=True)
class RuleEntry:
    rule: Rule
    origin: str
    root: str = ""


def relative(path: str, root: str) -> str | None:
    """Path relative to a scope root, or None when the path is outside it."""
    if not root:
        return path
    prefix = root + "/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def order_rules(
    config_rules: Sequence[tuple[Rule, str]],
    plugin_rules: Sequence[tuple[Scope, Sequence[Rule]]],
) -> list[RuleEntry]:
    entries = [RuleEntry(rule, origin) for rule, origin in config_rules]
    scopes = sorted(plugin_rules, key=lambda sr: (sr[0].plugin == CORE, -sr[0].depth))
    for stage in ("pre", "normal"):
        for scope, rules in scopes:
            origin = f"plugin:{scope.plugin}" + (f"@{scope.root}" if scope.root else "")
            entries += [RuleEntry(r, origin, scope.root) for r in rules if r.stage == stage]
    return entries


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Windows:
    """Decides whether a rule's since/until window contains a commit."""

    def __init__(self, is_ancestor: Callable[[str, str], bool]):
        self._is_ancestor = is_ancestor
        self._cache: dict[tuple[str, str], bool] = {}

    def applies(self, rule: Rule, commit: Commit) -> bool:
        if rule.since is not None and not self._reached(rule.since, commit):
            return False
        if rule.until is not None and self._reached(rule.until, commit):
            return False
        return True

    def _reached(self, bound: str, commit: Commit) -> bool:
        """True when the commit is at or after the bound (a date, or a commit it descends from)."""
        if DATE_RE.match(bound):
            return commit.committer_date >= _parse_date(bound)
        key = (bound, commit.sha)
        if key not in self._cache:
            self._cache[key] = self._is_ancestor(bound, commit.sha)
        return self._cache[key]


class Classifier:
    def __init__(
        self,
        entries: list[RuleEntry],
        taxonomy: Taxonomy,
        windows: Windows,
        head: Callable[[str], list[str]],
    ):
        self.entries = entries
        self.taxonomy = taxonomy
        self.windows = windows
        self._head = head

    def classify(self, path: str, blob: str | None, commit: Commit) -> Match:
        for entry in self.entries:
            rel = relative(path, entry.root)
            if rel is None:
                continue
            rule = entry.rule
            matched: list[str] = []
            if rule.paths:
                pattern = next(
                    (p for p in rule.paths if glob.globmatch(rel, p, flags=GLOB_FLAGS)), None
                )
                if pattern is None:
                    continue
                matched.append(pattern)
            if not self.windows.applies(rule, commit):
                continue
            if rule.content is not None:
                if blob is None:
                    continue
                text = "\n".join(self._head(blob)[: rule.content_lines])
                if not _regex(rule.content).search(text):
                    continue
                matched.append(f"content:{rule.content}")
            return Match(
                self.taxonomy.remap(rule.category),
                rule.id,
                entry.origin,
                " + ".join(matched),
                entry.root,
            )
        return Match(UNCLASSIFIED, "fallback", "none", "", "")
