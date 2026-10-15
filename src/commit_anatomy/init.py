"""Suggest a starter config from what the analysis could not place or placed too coarsely."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

from .analysis import CommitResult, SideResult
from .taxonomy import UNCLASSIFIED, Taxonomy

SPLIT_SHARE = 0.10

NAME_HINTS: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"test", "tests", "spec", "specs", "__tests__"}), "tests/unit"),
    (frozenset({"doc", "docs", "documentation"}), "docs/dev"),
    (frozenset({"script", "scripts", "tool", "tools", "bin"}), "code/tooling"),
    (
        frozenset({"locale", "locales", "i18n", "l10n", "translations", "lang"}),
        "supporting/translations",
    ),
    (frozenset({"migrations"}), "supporting/migrations"),
    (frozenset({"assets", "static", "images", "img", "fonts", "media"}), "supporting/assets"),
    (frozenset({"config", "conf", ".config"}), "supporting/build"),
    (frozenset({"ci", ".circleci", ".buildkite"}), "supporting/ci"),
)
CODE_EXT = frozenset(
    "py js ts tsx jsx mjs swift go kt kts java rb rs c h cc cpp m mm cs php scala sh".split()
)
DOC_EXT = frozenset("md rst txt adoc".split())
DATA_EXT = frozenset("json yaml yml toml ini cfg xml plist".split())
SPLIT_HINTS = {
    "docs": {
        **dict.fromkeys(("manual", "user", "guide", "guides", "help", "handbook"), "docs/user"),
        **dict.fromkeys(("dev", "developer", "development", "internal", "adr"), "docs/dev"),
        "api": "docs/api",
    },
    "tests": {
        "e2e": "tests/e2e",
        "integration": "tests/integration",
        **dict.fromkeys(("fixtures", "support", "helpers"), "tests/support"),
    },
}


@dataclass(frozen=True)
class Suggestion:
    paths: tuple[str, ...]
    category: str
    why: str


def _non_blank(side: SideResult) -> int:
    return sum(side.counts.values()) - side.counts["blank"]


def _sides(results: list[CommitResult]) -> Iterator[tuple[str, SideResult]]:
    for result in results:
        for f in result.files:
            if f.old is not None and f.change.old_path is not None:
                yield f.change.old_path, f.old
            if f.new is not None and f.change.new_path is not None:
                yield f.change.new_path, f.new


def _parent(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def guess_category(directory: str, filenames: list[str], taxonomy: Taxonomy) -> tuple[str, str]:
    for segment in (s.lower() for s in PurePosixPath(directory).parts):
        for names, category in NAME_HINTS:
            if segment in names and taxonomy.is_valid(category):
                return category, f"directory name {segment!r}"
    extensions = Counter(PurePosixPath(n).suffix.lower().lstrip(".") for n in filenames)
    if extensions:
        ext = extensions.most_common(1)[0][0]
        for group, category in (
            (CODE_EXT, "code/app"),
            (DOC_EXT, "docs/dev"),
            (DATA_EXT, "supporting/build"),
        ):
            if ext in group and taxonomy.is_valid(category):
                return category, f"mostly .{ext} files"
    return UNCLASSIFIED, "no recognisable directory name or extension"


def suggest_unclassified(results: list[CommitResult], taxonomy: Taxonomy) -> list[Suggestion]:
    by_dir: dict[str, Counter[str]] = defaultdict(Counter)
    classified_dirs: set[str] = set()
    for path, side in _sides(results):
        if side.match.category == UNCLASSIFIED:
            by_dir[_parent(path)][path] += _non_blank(side)
        else:
            parts = PurePosixPath(path).parts[:-1]
            classified_dirs.add("")
            classified_dirs.update("/".join(parts[: i + 1]) for i in range(len(parts)))
    suggestions = []
    for directory in sorted(by_dir):
        files = by_dir[directory]
        churn = sum(files.values())
        category, reason = guess_category(directory, list(files), taxonomy)
        if directory in classified_dirs:
            # A directory-wide glob would override the files that are already classified.
            for path in sorted(files):
                cat, why = guess_category(directory, [path], taxonomy)
                suggestions.append(
                    Suggestion(
                        (path,),
                        cat,
                        f"unclassified file, {files[path]} non-blank lines changed; "
                        f"guessed from {why}",
                    )
                )
        else:
            suggestions.append(
                Suggestion(
                    (f"{directory}/**",),
                    category,
                    f"{len(files)} unclassified file(s), {churn} non-blank lines changed; "
                    f"guessed from {reason}",
                )
            )
    return suggestions


def _split_category(child: str, category: str, taxonomy: Taxonomy) -> str:
    guess = SPLIT_HINTS.get(category.partition("/")[0], {}).get(child.lower())
    return guess if guess and taxonomy.is_valid(guess) else category


def suggest_splits(
    results: list[CommitResult], taxonomy: Taxonomy, share: float = SPLIT_SHARE
) -> list[Suggestion]:
    rules: dict[tuple[str, str], dict] = {}
    for path, side in _sides(results):
        m = side.match
        pattern = m.matched.split(" + ")[0]
        if m.category == UNCLASSIFIED or "**" not in pattern:
            continue
        prefix = pattern.split("**", 1)[0].rstrip("/")
        if not prefix or any(ch in prefix for ch in "*?[{"):
            continue
        base = f"{m.root}/{prefix}" if m.root else prefix
        info = rules.setdefault(
            (m.origin, m.rule_id),
            {"category": m.category, "base": base, "total": 0, "children": Counter()},
        )
        n = _non_blank(side)
        info["total"] += n
        rest = path[len(base) + 1 :] if path.startswith(base + "/") else ""
        if "/" in rest:
            info["children"][rest.split("/", 1)[0]] += n
    suggestions = []
    for (origin, rule_id), info in sorted(rules.items()):
        total = info["total"]
        big = [(c, n) for c, n in sorted(info["children"].items()) if total and n / total >= share]
        if len(big) < 2:
            continue
        for child, n in big:
            suggestions.append(
                Suggestion(
                    (f"{info['base']}/{child}/**",),
                    _split_category(child, info["category"], taxonomy),
                    f"splits {rule_id} ({origin}): {info['base']}/{child}/ carries "
                    f"{n / total:.0%} of its {total} non-blank changed lines",
                )
            )
    return suggestions


def _toml(value) -> str:
    """A TOML value for the strings, booleans, integers, lists and tables a config holds."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return f"[{', '.join(_toml(v) for v in value)}]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_toml(str(k))} = {_toml(v)}" for k, v in value.items()) + "}"
    return json.dumps(str(value), ensure_ascii=False)  # a JSON string is a TOML basic string


def render_config(
    *,
    unclassified: list[Suggestion],
    splits: list[Suggestion],
    active,
    commits: int,
    match: dict | None,
    today: date,
    carried: dict | None = None,
) -> str:
    plugins = ", ".join(f"{p.name} ({s.root or '/'})" for p, s in active) or "none"
    lines = [
        f"# commit-anatomy configuration written by `commit-anatomy init` on {today.isoformat()}",
        f"# Analysed {commits} commit(s). Active plugins: {plugins}.",
        "# Every rule below is a suggestion: edit or delete it, then check the effect",
        "# with `commit-anatomy explain <commit>`.",
        "",
    ]
    if match:
        lines += ["[match]", *(f"{k} = {_toml(v)}" for k, v in match.items()), ""]
    for name, table in (carried or {}).items():
        lines += [
            f"# [{name}] carried over from the file this one replaced",
            f"[{name}]",
            *(f"{_toml(str(k))} = {_toml(v)}" for k, v in table.items()),
            "",
        ]
    if not unclassified and not splits:
        lines.append("# No suggestions: every file is classified and no rule needs splitting.")
    for heading, group in (
        ("Rules that cover distinct directories", splits),
        ("Files no rule classified", unclassified),
    ):
        if not group:
            continue
        lines += [f"# --- {heading} ---", ""]
        for s in group:
            lines += [
                f"# why: {s.why}",
                "[[rules]]",
                f"paths = [{', '.join(_toml(p) for p in s.paths)}]",
                f"category = {_toml(s.category)}",
                "",
            ]
    return "\n".join(lines)
