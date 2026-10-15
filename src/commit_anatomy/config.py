"""Load and validate the user's per-repo config and the in-repo config."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from wcmatch import glob

from .classify import DATE_RE, GLOB_FLAGS
from .intent import DEFAULT_KEYWORDS, INTENTS
from .model import Rule
from .taxonomy import Taxonomy, TaxonomyError

REPO_FILE = ".commit-anatomy.toml"
MAX_CONTENT_LINES = 20
TOP_FIELDS = {"match", "rules", "taxonomy", "intent"}
RULE_FIELDS = {"id", "paths", "content", "content_lines", "category", "since", "until", "stage"}


class ConfigError(Exception):
    def __init__(self, file: Path | str, field: str, reason: str):
        super().__init__(f"{file}: {field}: {reason}")


@dataclass(frozen=True)
class IntentConfig:
    keywords: dict[str, tuple[str, ...]]
    overrides: dict[str, str]


@dataclass(frozen=True)
class EffectiveConfig:
    rules: tuple[tuple[Rule, str], ...]  # (rule, origin) in precedence order
    taxonomy: Taxonomy
    intent: IntentConfig
    files: tuple[Path, ...]

    @classmethod
    def default(cls) -> EffectiveConfig:
        return cls((), Taxonomy(), IntentConfig(dict(DEFAULT_KEYWORDS), {}), ())


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "commit-anatomy"


def normalize_remote(url: str) -> str:
    """'git@host:a/b.git', 'https://host/a/b' and 'ssh://git@host:22/a/b' all become 'host/a/b'."""
    u = url.strip()
    u = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", u)
    u = re.sub(r"^[^@/]+@", "", u)
    host, sep, rest = u.partition("/")
    if ":" in host:
        host, _, tail = host.partition(":")
        if not tail.isdigit():  # scp-like host:path, not a port
            rest = tail + (sep + rest if sep else "")
    u = host + ("/" + rest if rest else "")
    return u.rstrip("/").removesuffix(".git").lower()


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(path, "toml", str(e)) from None
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(path, "file", f"cannot read: {e}") from None


def _matches(path: Path, data: dict, repo_root: Path, remote: str | None) -> bool:
    match = data.get("match")
    if not isinstance(match, dict) or len(match) != 1 or not set(match) <= {"path", "remote"}:
        raise ConfigError(
            path,
            "match",
            'a user config needs match = { path = "..." } or match = { remote = "..." }',
        )
    for key in match:
        if not isinstance(match[key], str) or not match[key]:
            raise ConfigError(path, f"match.{key}", "must be a non-empty string")
    if "path" in match:
        return Path(match["path"]).expanduser().resolve() == repo_root.resolve()
    return remote is not None and normalize_remote(match["remote"]) == normalize_remote(remote)


def _intent(path: Path, table) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    if not isinstance(table, dict):
        raise ConfigError(path, "intent", "must be a table")
    unknown = set(table) - {"keywords", "overrides"}
    if unknown:
        raise ConfigError(path, f"intent.{sorted(unknown)[0]}", "unknown field")
    for key in ("keywords", "overrides"):
        if not isinstance(table.get(key, {}), dict):
            raise ConfigError(path, f"intent.{key}", "must be a table")
    keywords: dict[str, tuple[str, ...]] = {}
    for intent, words in table.get("keywords", {}).items():
        if intent not in INTENTS or intent == "unknown":
            raise ConfigError(path, f"intent.keywords.{intent}", "unknown intent")
        if not isinstance(words, list) or not all(isinstance(w, str) and w for w in words):
            raise ConfigError(path, f"intent.keywords.{intent}", "must be a list of words")
        keywords[intent] = tuple(w.lower() for w in words)
    overrides: dict[str, str] = {}
    for prefix, intent in table.get("overrides", {}).items():
        if not re.fullmatch(r"[0-9a-f]{4,64}", prefix):
            raise ConfigError(
                path,
                f"intent.overrides.{prefix}",
                "must be a commit sha prefix of at least 4 hex digits",
            )
        if intent not in INTENTS:
            raise ConfigError(path, f"intent.overrides.{prefix}", f"unknown intent {intent!r}")
        overrides[prefix] = intent
    return keywords, overrides


def _rule(
    path: Path,
    index: int,
    item,
    taxonomy: Taxonomy,
    resolve_commit: Callable[[str], str | None],
) -> Rule:
    where = f"rules[{index}]"
    if not isinstance(item, dict):
        raise ConfigError(path, where, "must be a table")
    unknown = set(item) - RULE_FIELDS
    if unknown:
        raise ConfigError(path, f"{where}.{sorted(unknown)[0]}", "unknown field")
    category = item.get("category")
    if not isinstance(category, str) or not taxonomy.is_valid(category):
        raise ConfigError(path, f"{where}.category", f"unknown category {category!r}")
    paths = item.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
        raise ConfigError(path, f"{where}.paths", "must be a list of glob strings")
    for pattern in paths:
        try:
            glob.translate(pattern, flags=GLOB_FLAGS)
        except Exception as e:
            raise ConfigError(path, f"{where}.paths", f"bad glob {pattern!r}: {e}") from None
    content = item.get("content")
    if content is not None:
        if not isinstance(content, str):
            raise ConfigError(path, f"{where}.content", "must be a regular expression string")
        try:
            re.compile(content)
        except re.error as e:
            raise ConfigError(path, f"{where}.content", f"bad regex: {e}") from None
    if not paths and content is None:
        raise ConfigError(path, where, "needs `paths`, `content` or both")
    lines = item.get("content_lines", 5)
    if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= MAX_CONTENT_LINES:
        raise ConfigError(
            path, f"{where}.content_lines", f"must be an integer from 1 to {MAX_CONTENT_LINES}"
        )
    stage = item.get("stage", "normal")
    if stage not in ("pre", "normal"):
        raise ConfigError(path, f"{where}.stage", 'must be "pre" or "normal"')
    bounds: dict[str, str] = {}
    for key in ("since", "until"):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, date | datetime):  # unquoted TOML dates
            value = value.isoformat()
        if not isinstance(value, str):
            raise ConfigError(path, f"{where}.{key}", "must be a date or a commit")
        if DATE_RE.match(value):
            bounds[key] = value
            continue
        sha = resolve_commit(value)
        if sha is None:
            raise ConfigError(
                path, f"{where}.{key}", f"{value!r} is neither a date nor a commit in this repo"
            )
        bounds[key] = sha
    return Rule(
        id=str(item.get("id", f"{path.name}#{index + 1}")),
        category=category,
        paths=tuple(paths),
        content=content,
        content_lines=lines,
        since=bounds.get("since"),
        until=bounds.get("until"),
        stage=stage,
    )


def load_config(
    repo_root: Path,
    remote: str | None,
    resolve_commit: Callable[[str], str | None],
) -> EffectiveConfig:
    sources: list[tuple[Path, dict, str]] = []  # highest precedence first
    repos_dir = config_home() / "repos"
    if repos_dir.is_dir():
        for path in sorted(repos_dir.glob("*.toml")):
            data = _read(path)
            if _matches(path, data, repo_root, remote):
                sources.append((path, data, f"user:{path.name}"))
    repo_file = repo_root / REPO_FILE
    if repo_file.is_file():
        data = _read(repo_file)
        if "match" in data:
            raise ConfigError(repo_file, "match", "only allowed in user config files")
        sources.append((repo_file, data, REPO_FILE))

    for path, data, _ in sources:
        unknown = set(data) - TOP_FIELDS
        if unknown:
            raise ConfigError(path, sorted(unknown)[0], "unknown field")

    taxonomy = Taxonomy()
    keywords = dict(DEFAULT_KEYWORDS)
    overrides: dict[str, str] = {}
    for path, data, _ in reversed(sources):  # lowest precedence first, so the user file wins
        if "taxonomy" in data:
            if not isinstance(data["taxonomy"], dict):
                raise ConfigError(path, "taxonomy", "must be a table")
            try:
                taxonomy = taxonomy.extended(data["taxonomy"])
            except TaxonomyError as e:
                raise ConfigError(path, "taxonomy", str(e)) from None
        more_keywords, more_overrides = _intent(path, data.get("intent", {}))
        keywords.update(more_keywords)
        overrides.update(more_overrides)

    rules: list[tuple[Rule, str]] = []
    for path, data, origin in sources:
        raw = data.get("rules", [])
        if not isinstance(raw, list):
            raise ConfigError(path, "rules", "must be an array of tables ([[rules]])")
        for index, item in enumerate(raw):
            rules.append((_rule(path, index, item, taxonomy, resolve_commit), origin))

    return EffectiveConfig(
        tuple(rules),
        taxonomy,
        IntentConfig(keywords, overrides),
        tuple(path for path, _, _ in sources),
    )
