"""Commit intent from the commit message: override, conventional prefix, keyword, unknown."""

from __future__ import annotations

import re

INTENTS = ("feat", "fix", "refactor", "test", "docs", "chore", "release", "unknown")

CONVENTIONAL = {
    "feat": "feat",
    "feature": "feat",
    "fix": "fix",
    "bugfix": "fix",
    "hotfix": "fix",
    "refactor": "refactor",
    "perf": "refactor",
    "style": "refactor",
    "test": "test",
    "tests": "test",
    "docs": "docs",
    "doc": "docs",
    "chore": "chore",
    "build": "chore",
    "ci": "chore",
    "deps": "chore",
    "release": "release",
}

# Order matters: the first intent with a matching pattern wins.
DEFAULT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "release": ("release*", "bump"),
    "fix": ("fix*", "bug*", "crash*", "regress*", "correct*", "repair*", "revert*"),
    "refactor": ("refactor*", "rename*", "move*", "extract*", "tidy*", "simplify*", "clean*"),
    "test": ("test*",),
    "docs": ("document*", "docs", "doc", "manual", "translate*", "readme"),
    "chore": ("chore",),
    "feat": (
        "add*",
        "support*",
        "introduce*",
        "allow*",
        "let",
        "lets",
        "offer*",
        "show*",
        "enable*",
        "implement*",
    ),
}

_CONVENTIONAL_RE = re.compile(r"^(?P<type>[A-Za-z]+)(?:\([^)]*\))?!?:\s")
_WORD_RE = re.compile(r"[\w'-]+")


def _matches(word: str, pattern: str) -> bool:
    if pattern.endswith("*"):
        return word.startswith(pattern[:-1])
    return word == pattern


def classify_intent(
    sha: str,
    message: str,
    keywords: dict[str, tuple[str, ...]] | None = None,
    overrides: dict[str, str] | None = None,
) -> str:
    for prefix, intent in (overrides or {}).items():
        if sha.startswith(prefix):
            return intent
    subject = message.strip().split("\n", 1)[0].strip()
    conventional = _CONVENTIONAL_RE.match(subject)
    if conventional and conventional["type"].lower() in CONVENTIONAL:
        return CONVENTIONAL[conventional["type"].lower()]
    first = _WORD_RE.search(subject)
    if first is None:
        return "unknown"
    word = first.group(0).lower()
    for intent, patterns in (keywords or DEFAULT_KEYWORDS).items():
        if any(_matches(word, p) for p in patterns):
            return intent
    return "unknown"
