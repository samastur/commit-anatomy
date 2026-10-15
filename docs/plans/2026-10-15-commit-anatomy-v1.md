# commit-anatomy v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI that breaks commits down into docs / tests / code / supporting changes (code split into code, comment, docstring and blank lines), tags each commit's intent, and reports per commit, per period or as a trend.

**Architecture:** A git layer turns revspecs into immutable *facts* (commit metadata plus changed line ranges per file), cached in SQLite. At report time a first-match rule engine (rules from user config, in-repo config and ecosystem plugins) classifies each file side. A Pygments-based labeller (also cached per blob) labels lines, and an intent classifier reads the commit message. Reporters (table, JSON/CSV, explain, trend HTML, init) consume the results.

**Tech Stack:** Python ≥ 3.12, uv, Pygments, wcmatch, Typer, Rich, SQLite (stdlib), pytest, ruff.

**Spec:** `docs/specs/2026-10-15-commit-anatomy-design.md` — read it before starting any task.

## Global Constraints

- Python `>=3.12`; managed with `uv`; src layout (`src/commit_anatomy/`).
- Runtime dependencies are exactly: `pygments`, `wcmatch`, `typer`, `rich`. Dev: `pytest`, `ruff`.
- Console script `commit-anatomy`; plugin entry-point group `commit_anatomy.plugins`; built-ins register through it too.
- Config files: user `$XDG_CONFIG_HOME/commit-anatomy/repos/*.toml` (default `~/.config/...`), in-repo `.commit-anatomy.toml`.
- Cache: `$COMMIT_ANATOMY_CACHE_DIR` if set, else `$XDG_CACHE_HOME/commit-anatomy/` (default `~/.cache/commit-anatomy/`), single file `cache.sqlite`.
- Exit codes: 0 success, 1 runtime error (git / plugin), 2 usage or config error.
- Every git call goes through `git -c core.quotePath=false …`.
- `content_lines` ≤ 20 (`HEAD_LINES`); blobs > 1 000 000 bytes are not lexed.
- No test may depend on the hrcek repositories; tests build throwaway repos in `tmp_path`.
- Run tests with `uv run pytest`, lint with `uv run ruff check . && uv run ruff format --check .`.
- Commit messages use conventional prefixes (`feat:`, `fix:`, `test:`, `docs:`, `chore:`) — the tool reads them. **Never add `Co-Authored-By` or any other attribution trailer** to commit messages, PR descriptions or any other message.

## Review Focus

1. **Non-ASCII and quoted paths** (`Hrček.md`, `with space.txt`, `q"uote.txt`, a tab in a name) must be classified and counted like any other file, not dropped or mis-keyed → pinned in Task 7.
2. **Line endings**: CRLF, lone CR, no trailing newline and leading blank lines must yield exactly one label per line as git numbers them (no off-by-one into the wrong label) → pinned in Task 3.
3. **Commits with no line changes** (empty commit, mode-only change, pure rename, binary-only) must still appear in a per-commit table, with zeros → pinned in Tasks 11 and 12.
4. **A revspec that selects nothing** (`HEAD..HEAD`) must print "No commits matched." on stderr and exit 0 (`[]` for JSON), not crash → pinned in Task 14.
5. **Monorepo scoping**: plugin rules apply only inside their scope directory; files outside every scope fall through to `core` / `unclassified` → pinned in Task 9.

---

## File map

```
pyproject.toml, README.md, .gitignore
src/commit_anatomy/
  __init__.py
  model.py                  # Commit, FileChange, CommitFacts, Rule, Scope, Match, Label
  taxonomy.py               # Taxonomy (two-level categories, split_lines, remap)
  intent.py                 # classify_intent
  lines.py                  # split_lines, blank_labels, RLE, count_ranges, LineLabeler protocol
  lexers/__init__.py
  lexers/pygments_backend.py
  classify.py               # RuleEntry, order_rules, Windows, Classifier, GLOB_FLAGS
  config.py                 # load_config, ConfigError, normalize_remote, config_home
  git.py                    # GitRepo, parse_diff_tree, BlobReader, GitError
  plugins/__init__.py
  plugins/base.py           # Plugin protocol, RepoViews, load_plugins, active_scopes, language_hint, rule()
  plugins/core.py, django.py, wxt.py, xcode.py
  cache.py                  # MemoryCache, SqliteCache, facts JSON, cache_home
  analysis.py               # Analyzer, CommitResult, FileResult, SideResult
  rows.py                   # Row, rows_from, FIELDS
  aggregate.py              # Counts, Group, aggregate, merge_groups
  report/__init__.py
  report/table.py, tabular.py, explain.py, trend.py
  init.py                   # suggest_unclassified, suggest_splits, render_config
  cli.py
tests/
  conftest.py, helpers.py, plugin_helpers.py
  unit/…, integration/…, golden/…
```

---

### Task 1: Project scaffold, data model and taxonomy

**Files:**
- Create: `pyproject.toml`, `README.md`, `.gitignore`, `src/commit_anatomy/__init__.py`, `src/commit_anatomy/model.py`, `src/commit_anatomy/taxonomy.py`
- Test: `tests/unit/test_taxonomy.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `model.Label` (Literal of `"code" "comment" "docstring" "blank" "unlexed" "raw"`), `model.LABELS: tuple[Label, ...]` in that order, `model.LineRanges = tuple[tuple[int, int], ...]` (1-based start, count).
  - `model.Commit(sha, parents: tuple[str,...], author_name, author_email, author_date: datetime, committer_date: datetime, message)` with properties `subject`, `body`.
  - `model.FileChange(old_path: str|None, new_path: str|None, status: str, binary: bool, similarity: int|None, old_blob: str|None, new_blob: str|None, removed_lines: LineRanges, added_lines: LineRanges, submodule: bool = False)` with property `path` (new path, or old path for deletions).
  - `model.CommitFacts(commit: Commit, changes: tuple[FileChange, ...])`.
  - `model.Rule(id, category, paths: tuple[str,...] = (), content: str|None = None, content_lines: int = 5, since: str|None = None, until: str|None = None, stage: "pre"|"normal" = "normal")`.
  - `model.Scope(plugin: str, root: str)` with property `depth` (0 for `""`).
  - `model.Match(category, rule_id, origin, matched, root="")`.
  - `taxonomy.UNCLASSIFIED = "unclassified"`, `taxonomy.TaxonomyError(ValueError)`, `taxonomy.Taxonomy` with `tops`, `is_valid(category)`, `split_lines(category)`, `remap(category)`, `order_key(category)`, `extended(table) -> Taxonomy`.

- [ ] **Step 1: Create the project files**

`pyproject.toml`:

```toml
[project]
name = "commit-anatomy"
version = "0.1.0"
description = "Break commits down into docs, tests, code and supporting changes"
readme = "README.md"
requires-python = ">=3.12"
dependencies = ["pygments>=2.18", "wcmatch>=10", "typer>=0.12", "rich>=13"]

[project.scripts]
commit-anatomy = "commit_anatomy.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`README.md`:

```markdown
# commit-anatomy

Break commits down into documentation, tests, code (with and without comments)
and supporting changes. See `docs/specs/2026-10-15-commit-anatomy-design.md`.
```

`.gitignore`:

```
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
dist/
```

`src/commit_anatomy/__init__.py`:

```python
"""Break commits down into docs, tests, code and supporting changes."""

__version__ = "0.1.0"
```

`src/commit_anatomy/model.py`:

```python
"""Plain data shared by every layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Label = Literal["code", "comment", "docstring", "blank", "unlexed", "raw"]
LABELS: tuple[Label, ...] = ("code", "comment", "docstring", "blank", "unlexed", "raw")

# (first line, number of lines), 1-based, as in a unified diff hunk header.
LineRanges = tuple[tuple[int, int], ...]

Stage = Literal["pre", "normal"]


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    author_date: datetime
    committer_date: datetime
    message: str

    @property
    def subject(self) -> str:
        return self.message.strip().split("\n", 1)[0].strip()

    @property
    def body(self) -> str:
        parts = self.message.strip().split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""


@dataclass(frozen=True)
class FileChange:
    old_path: str | None  # None for an added file
    new_path: str | None  # None for a deleted file
    status: str  # A, D, M, R or T
    binary: bool
    similarity: int | None  # renames only
    old_blob: str | None
    new_blob: str | None
    removed_lines: LineRanges
    added_lines: LineRanges
    submodule: bool = False

    @property
    def path(self) -> str:
        return self.new_path if self.new_path is not None else self.old_path or ""


@dataclass(frozen=True)
class CommitFacts:
    commit: Commit
    changes: tuple[FileChange, ...]


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    paths: tuple[str, ...] = ()
    content: str | None = None
    content_lines: int = 5
    since: str | None = None
    until: str | None = None
    stage: Stage = "normal"


@dataclass(frozen=True)
class Scope:
    plugin: str
    root: str  # "" is the repository root; never a trailing slash

    @property
    def depth(self) -> int:
        return 0 if not self.root else self.root.count("/") + 1


@dataclass(frozen=True)
class Match:
    category: str
    rule_id: str
    origin: str
    matched: str  # the glob and/or "content:<regex>" that matched; "" for the fallback
    root: str = ""  # scope root the rule's paths are relative to
```

- [ ] **Step 2: Write the failing taxonomy tests**

`tests/unit/test_taxonomy.py`:

```python
import pytest

from commit_anatomy.taxonomy import Taxonomy, TaxonomyError


def test_default_categories_are_valid():
    t = Taxonomy()
    assert t.is_valid("docs/llm")
    assert t.is_valid("supporting/translations")
    assert t.is_valid("unclassified")
    assert not t.is_valid("docs")  # docs has subcategories, so a bare top is not a category
    assert not t.is_valid("docs/nope")
    assert not t.is_valid("nope/x")


def test_split_lines_only_for_tests_and_code():
    t = Taxonomy()
    assert t.split_lines("tests/unit") and t.split_lines("code/app") and t.split_lines("code")
    assert not t.split_lines("docs/dev")
    assert not t.split_lines("supporting/deps")
    assert not t.split_lines("unclassified")


def test_extend_adds_subcategory_and_top_before_unclassified():
    t = Taxonomy().extended(
        {
            "docs": {"subcategories": ["adr"]},
            "infra": {"subcategories": ["terraform"], "split_lines": True},
        }
    )
    assert t.is_valid("docs/adr") and t.is_valid("docs/user")
    assert t.is_valid("infra/terraform") and t.split_lines("infra/terraform")
    assert t.tops[-1] == "unclassified"
    assert t.tops.index("infra") == t.tops.index("unclassified") - 1


def test_remap_promotes_translations():
    t = Taxonomy().extended(
        {"translations": {}, "remap": {"supporting/translations": "translations"}}
    )
    assert t.remap("supporting/translations") == "translations"
    assert t.remap("docs/dev") == "docs/dev"


def test_remap_target_must_exist():
    with pytest.raises(TaxonomyError, match="unknown target"):
        Taxonomy().extended({"remap": {"supporting/translations": "translations"}})


def test_remap_source_must_exist():
    with pytest.raises(TaxonomyError, match="remap.docs/nope"):
        Taxonomy().extended({"remap": {"docs/nope": "docs/dev"}})


def test_unknown_field_rejected():
    with pytest.raises(TaxonomyError, match="docs.colour"):
        Taxonomy().extended({"docs": {"colour": "red"}})


def test_unclassified_cannot_be_changed():
    with pytest.raises(TaxonomyError, match="unclassified"):
        Taxonomy().extended({"unclassified": {"subcategories": ["x"]}})


def test_order_key_follows_taxonomy_order():
    t = Taxonomy()
    cats = ["supporting/deps", "docs/llm", "unclassified", "code/app", "docs/user"]
    assert sorted(cats, key=t.order_key) == [
        "docs/user",
        "docs/llm",
        "code/app",
        "supporting/deps",
        "unclassified",
    ]
```

- [ ] **Step 3: Install and run the tests to see them fail**

Run: `uv sync && uv run pytest tests/unit/test_taxonomy.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.taxonomy'`.

- [ ] **Step 4: Implement the taxonomy**

`src/commit_anatomy/taxonomy.py`:

```python
"""Two-level category taxonomy. It is data: config can extend it and remap categories."""

from __future__ import annotations

from dataclasses import dataclass, replace

UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class TopCategory:
    name: str
    subcategories: tuple[str, ...]
    split_lines: bool


DEFAULT_TOPS: tuple[TopCategory, ...] = (
    TopCategory("docs", ("user", "dev", "api", "llm", "changelog"), False),
    TopCategory("tests", ("unit", "integration", "e2e", "support"), True),
    TopCategory("code", ("app", "tooling"), True),
    TopCategory(
        "supporting",
        ("deps", "migrations", "translations", "build", "ci", "generated", "assets"),
        False,
    ),
    TopCategory(UNCLASSIFIED, (), False),
)


class TaxonomyError(ValueError):
    pass


class Taxonomy:
    def __init__(
        self,
        tops: tuple[TopCategory, ...] = DEFAULT_TOPS,
        remap: dict[str, str] | None = None,
    ):
        self._tops = {t.name: t for t in tops}
        self._order = tuple(t.name for t in tops)
        self._remap = dict(remap or {})

    @property
    def tops(self) -> tuple[str, ...]:
        return self._order

    def is_valid(self, category: str) -> bool:
        top, _, sub = category.partition("/")
        t = self._tops.get(top)
        if t is None:
            return False
        if sub:
            return sub in t.subcategories
        return not t.subcategories

    def split_lines(self, category: str) -> bool:
        t = self._tops.get(category.partition("/")[0])
        return bool(t and t.split_lines)

    def remap(self, category: str) -> str:
        return self._remap.get(category, category)

    def order_key(self, category: str) -> tuple[int, int]:
        top, _, sub = category.partition("/")
        t = self._tops.get(top)
        if t is None:
            return (len(self._order), 0)
        sub_index = t.subcategories.index(sub) if sub in t.subcategories else -1
        return (self._order.index(top), sub_index)

    def extended(self, table: dict) -> Taxonomy:
        """Return a copy with `[taxonomy]` config applied (see the spec, section 3.3)."""
        tops = dict(self._tops)
        order = list(self._order)
        remap = dict(self._remap)
        for name, value in table.items():
            if name == "remap":
                if not isinstance(value, dict):
                    raise TaxonomyError("remap: must be a table")
                remap.update(value)
                continue
            if name == UNCLASSIFIED:
                raise TaxonomyError(f"{UNCLASSIFIED}: cannot be changed")
            if not isinstance(value, dict):
                raise TaxonomyError(f"{name}: must be a table")
            unknown = set(value) - {"subcategories", "split_lines"}
            if unknown:
                raise TaxonomyError(f"{name}.{sorted(unknown)[0]}: unknown field")
            subs = value.get("subcategories", [])
            if not isinstance(subs, list) or not all(
                isinstance(s, str) and s and "/" not in s for s in subs
            ):
                raise TaxonomyError(f"{name}.subcategories: must be a list of names without '/'")
            split = value.get("split_lines")
            if split is not None and not isinstance(split, bool):
                raise TaxonomyError(f"{name}.split_lines: must be true or false")
            old = tops.get(name)
            if old is None:
                tops[name] = TopCategory(name, tuple(subs), bool(split))
                order.insert(order.index(UNCLASSIFIED), name)
            else:
                merged = old.subcategories + tuple(s for s in subs if s not in old.subcategories)
                tops[name] = replace(
                    old,
                    subcategories=merged,
                    split_lines=old.split_lines if split is None else split,
                )
        result = Taxonomy(tuple(tops[n] for n in order), remap)
        for source, target in remap.items():
            if not result.is_valid(source):
                raise TaxonomyError(f"remap.{source}: unknown category")
            if not isinstance(target, str) or not result.is_valid(target):
                raise TaxonomyError(f"remap.{source}: unknown target category {target!r}")
        return result
```

- [ ] **Step 5: Run the tests to see them pass, then lint**

Run: `uv run pytest tests/unit/test_taxonomy.py -q && uv run ruff check . && uv run ruff format --check .`
Expected: `9 passed`, no lint errors (run `uv run ruff format .` first if formatting differs).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock README.md .gitignore src tests
git commit -m "feat: scaffold project with data model and taxonomy"
```

---

### Task 2: Intent classifier

**Files:**
- Create: `src/commit_anatomy/intent.py`
- Test: `tests/unit/test_intent.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `intent.INTENTS: tuple[str, ...]`, `intent.DEFAULT_KEYWORDS: dict[str, tuple[str, ...]]` (ordered; first matching intent wins; a pattern ending in `*` is a word prefix, otherwise an exact word), `intent.classify_intent(sha: str, message: str, keywords: dict[str, tuple[str, ...]] | None = None, overrides: dict[str, str] | None = None) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_intent.py`:

```python
import pytest

from commit_anatomy.intent import DEFAULT_KEYWORDS, INTENTS, classify_intent


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("feat: offer pictures a lazy loader has not swapped in yet", "feat"),
        ("fix(popup): spend only the queued copy", "fix"),
        ("refactor!: split the module", "refactor"),
        ("perf: faster lookups", "refactor"),
        ("ci: boot the simulator", "chore"),
        ("docs: explain setup", "docs"),
        ("Release 0.2.0", "release"),
        ("Fix crash when offline", "fix"),
        ("Let the entry picture take an API token", "feat"),
        ("Letter templates get a margin", "unknown"),  # "let" is an exact word, not a prefix
        ("Translate the user manual into Slovenian", "docs"),
        ("Keep saves Hrček could not take, and send them later", "unknown"),
        ("Show entries as a list or as cards\n\nBody text mentions fix.", "feat"),
        ("", "unknown"),
    ],
)
def test_intent_from_message(message, expected):
    assert classify_intent("abc123", message) == expected


def test_override_by_sha_prefix_wins():
    assert classify_intent("abcdef1234", "feat: x", overrides={"abcd": "fix"}) == "fix"
    assert classify_intent("ffff", "feat: x", overrides={"abcd": "fix"}) == "feat"


def test_custom_keywords_replace_a_default_list():
    keywords = {**DEFAULT_KEYWORDS, "feat": ("keep",)}
    assert classify_intent("a", "Keep saves for later", keywords=keywords) == "feat"
    assert classify_intent("a", "Add a button", keywords=keywords) == "unknown"


def test_every_default_keyword_intent_is_known():
    assert set(DEFAULT_KEYWORDS) <= set(INTENTS) - {"unknown"}
```

- [ ] **Step 2: Run to see failure**

Run: `uv run pytest tests/unit/test_intent.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.intent'`.

- [ ] **Step 3: Implement**

`src/commit_anatomy/intent.py`:

```python
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
```

- [ ] **Step 4: Run to see pass**

Run: `uv run pytest tests/unit/test_intent.py -q`
Expected: `17 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/commit_anatomy/intent.py tests/unit/test_intent.py
git commit -m "feat: classify commit intent from the message"
```

---

### Task 3: Line labels and the Pygments labeller

**Files:**
- Create: `src/commit_anatomy/lines.py`, `src/commit_anatomy/lexers/__init__.py` (empty), `src/commit_anatomy/lexers/pygments_backend.py`
- Test: `tests/unit/test_lines.py`, `tests/unit/test_pygments_labeler.py`

**Interfaces:**
- Consumes: `model.Label`, `model.LineRanges`.
- Produces:
  - `lines.decode(content: bytes) -> str` (UTF-8, errors replaced).
  - `lines.split_lines(text: str) -> list[str]` — lines as git numbers them.
  - `lines.blank_labels(text: str) -> list[Label]` — `"blank"` or `"raw"` per line.
  - `lines.rle_encode(labels) -> list[list]`, `lines.rle_decode(runs) -> list[Label]`.
  - `lines.count_ranges(labels: list[Label], ranges: LineRanges) -> Counter[str]` — lines outside `labels` count as `"unlexed"`.
  - `lines.LineLabeler` Protocol: `version: str`, `resolve(path, hint) -> str | None`, `label(content: bytes, alias: str | None) -> list[Label]`.
  - `lexers.pygments_backend.PygmentsLabeler(max_bytes: int = 1_000_000)` implementing `LineLabeler`.

- [ ] **Step 1: Write failing tests for `lines.py`**

`tests/unit/test_lines.py`:

```python
from collections import Counter

from commit_anatomy.lines import blank_labels, count_ranges, rle_decode, rle_encode, split_lines


def test_split_lines_matches_git_line_numbering():
    assert split_lines("") == []
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("\n\n") == ["", ""]


def test_blank_labels():
    assert blank_labels("a\n  \n\tb\n\r\n") == ["raw", "blank", "raw", "blank"]


def test_rle_roundtrip():
    labels = ["code", "code", "blank", "comment", "comment", "code"]
    runs = rle_encode(labels)
    assert runs == [["code", 2], ["blank", 1], ["comment", 2], ["code", 1]]
    assert rle_decode(runs) == labels
    assert rle_encode([]) == []


def test_count_ranges_reads_labels_and_marks_missing_lines_unlexed():
    labels = ["code", "comment", "blank"]
    assert count_ranges(labels, ((2, 2), (5, 1))) == Counter(
        {"comment": 1, "blank": 1, "unlexed": 1}
    )
    assert count_ranges(labels, ()) == Counter()
```

- [ ] **Step 2: Run to see failure**

Run: `uv run pytest tests/unit/test_lines.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.lines'`.

- [ ] **Step 3: Implement `lines.py`**

```python
"""Line labels: splitting like git, blank detection, run-length encoding, range counting."""

from __future__ import annotations

from collections import Counter
from typing import Protocol

from .model import Label, LineRanges


def decode(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def split_lines(text: str) -> list[str]:
    """Split on \\n the way git numbers lines: no phantom line after a final newline."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def blank_labels(text: str) -> list[Label]:
    return ["blank" if not line.strip() else "raw" for line in split_lines(text)]


def rle_encode(labels: list[Label]) -> list[list]:
    runs: list[list] = []
    for label in labels:
        if runs and runs[-1][0] == label:
            runs[-1][1] += 1
        else:
            runs.append([label, 1])
    return runs


def rle_decode(runs: list[list]) -> list[Label]:
    return [label for label, count in runs for _ in range(count)]


def count_ranges(labels: list[Label], ranges: LineRanges) -> Counter[str]:
    counts: Counter[str] = Counter()
    for start, length in ranges:
        for line in range(start, start + length):
            counts[labels[line - 1] if 0 < line <= len(labels) else "unlexed"] += 1
    return counts


class LineLabeler(Protocol):
    version: str

    def resolve(self, path: str, hint: str | None) -> str | None:
        """Lexer alias for this file (plugin hint first, then filename), None if unknown."""
        ...

    def label(self, content: bytes, alias: str | None) -> list[Label]:
        """One label per line of the whole blob."""
        ...
```

- [ ] **Step 4: Run to see pass**

Run: `uv run pytest tests/unit/test_lines.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Write failing tests for the Pygments labeller**

`tests/unit/test_pygments_labeler.py`:

```python
from commit_anatomy.lexers.pygments_backend import PygmentsLabeler

L = PygmentsLabeler()


def lab(text: str, path: str, hint: str | None = None):
    return L.label(text.encode(), L.resolve(path, hint))


def test_python_docstrings_comments_and_plain_strings():
    src = (
        '"""Module."""\n'
        "import os  # why\n"
        "\n"
        "def f():\n"
        '    """Doc\n'
        '    more."""\n'
        "    # note\n"
        '    x = """not a docstring"""\n'
    )
    assert lab(src, "a.py") == [
        "docstring",
        "code",  # code with a trailing comment is code
        "blank",
        "code",
        "docstring",
        "docstring",
        "comment",
        "code",
    ]


def test_django_template_comments_and_delimiters():
    src = (
        "{# c #}\n"
        "{% comment %}\n"
        "hidden\n"
        "{% endcomment %}\n"
        "<!-- h -->\n"
        "<p>{{ x }}</p>\n"
    )
    assert lab(src, "t.html", "html+django") == [
        "comment",
        "code",  # {% %} delimiters are Comment.Preproc in Pygments: code
        "comment",
        "code",
        "comment",
        "code",
    ]


def test_typescript_jsdoc():
    src = "/**\n * Doc\n */\nconst x = 1; // trailing\n// c\n"
    assert lab(src, "a.ts") == ["comment", "comment", "comment", "code", "comment"]


def test_swift_doc_comments():
    assert lab("/// doc\n/* a\n b */\nlet x = 1\n", "a.swift") == [
        "comment",
        "comment",
        "comment",
        "code",
    ]


def test_go_and_kotlin():
    assert lab("// Package p.\npackage p\n\n/* m */\n", "a.go") == [
        "comment",
        "code",
        "blank",
        "comment",
    ]
    assert lab("/**\n * KDoc\n */\nval x = 1\n", "a.kt") == [
        "comment",
        "comment",
        "comment",
        "code",
    ]


def test_line_endings_keep_one_label_per_git_line():
    assert lab("a = 1\r\n# c\r\n\r\nb = 2\r\n", "a.py") == ["code", "comment", "blank", "code"]
    assert lab("a = 1\n# c", "a.py") == ["code", "comment"]  # no trailing newline
    assert lab("a = 1\r# c\n", "a.py") == ["code"]  # a lone CR is not a line break for git
    assert lab("\n\nx = 1\n", "a.py") == ["blank", "blank", "code"]


def test_unknown_language_is_unlexed_but_blank_lines_stay_blank():
    assert L.resolve("x.unknownext", None) is None
    assert L.label(b"a\n\nb\n", None) == ["unlexed", "blank", "unlexed"]


def test_oversize_blob_is_unlexed():
    assert PygmentsLabeler(max_bytes=5).label(b"x = 1\n# c\n", "python") == [
        "unlexed",
        "unlexed",
    ]


def test_non_utf8_is_decoded_with_replacement():
    assert L.label(b"x = '\xff'\n", "python") == ["code"]


def test_version_names_the_backend():
    assert "pygments" in L.version
```

- [ ] **Step 6: Run to see failure**

Run: `uv run pytest tests/unit/test_pygments_labeler.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.lexers'`.

- [ ] **Step 7: Implement the backend**

Create empty `src/commit_anatomy/lexers/__init__.py`. Then `src/commit_anatomy/lexers/pygments_backend.py`:

```python
"""Pygments-based line labeller: lex the whole blob, then label each line."""

from __future__ import annotations

import posixpath
from functools import lru_cache

import pygments
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.token import Comment, String
from pygments.util import ClassNotFound

from ..lines import decode, split_lines
from ..model import Label

MAX_BYTES = 1_000_000


@lru_cache(maxsize=None)
def _lexer(alias: str) -> Lexer:
    # stripnl=False keeps leading blank lines, so token lines stay aligned with git's lines.
    return get_lexer_by_name(alias, stripnl=False, stripall=False, ensurenl=True)


@lru_cache(maxsize=4096)
def _alias(basename: str, hint: str | None) -> str | None:
    try:
        lexer = get_lexer_by_name(hint) if hint else get_lexer_for_filename(basename)
    except ClassNotFound:
        return None
    return lexer.aliases[0] if lexer.aliases else None


def _kind(token) -> str:
    if token in Comment.Preproc or token in Comment.PreprocFile:
        return "code"  # C #include, Django {% %} and {{ }} delimiters
    if token in Comment:
        return "comment"
    if token in String.Doc:
        return "docstring"
    return "code"


def _label(kinds: set[str]) -> Label:
    if not kinds:
        return "blank"
    if "code" in kinds:
        return "code"
    if "docstring" in kinds:
        return "docstring"
    return "comment"


def _unlexed(lines: list[str]) -> list[Label]:
    return ["blank" if not line.strip() else "unlexed" for line in lines]


class PygmentsLabeler:
    version = f"pygments-labeler-1/pygments-{pygments.__version__}"

    def __init__(self, max_bytes: int = MAX_BYTES):
        self.max_bytes = max_bytes

    def resolve(self, path: str, hint: str | None) -> str | None:
        return _alias(posixpath.basename(path), hint)

    def label(self, content: bytes, alias: str | None) -> list[Label]:
        text = decode(content)
        lines = split_lines(text)
        if alias is None or len(content) > self.max_bytes:
            return _unlexed(lines)
        # CRLF -> LF keeps the line count; a lone CR is not a line break for git, so blank it.
        lexable = text.replace("\r\n", "\n").replace("\r", " ")
        kinds: list[set[str]] = [set() for _ in lines]
        line = 0
        try:
            for token, value in _lexer(alias).get_tokens(lexable):
                for i, part in enumerate(value.split("\n")):
                    if i:
                        line += 1
                    if line < len(kinds) and part.strip():
                        kinds[line].add(_kind(token))
        except Exception:  # a lexer bug must not abort the analysis
            return _unlexed(lines)
        return [_label(k) for k in kinds]
```

- [ ] **Step 8: Run to see pass**

Run: `uv run pytest tests/unit/test_lines.py tests/unit/test_pygments_labeler.py -q`
Expected: `14 passed`.

- [ ] **Step 9: Commit**

```bash
git add src/commit_anatomy/lines.py src/commit_anatomy/lexers tests/unit/test_lines.py tests/unit/test_pygments_labeler.py
git commit -m "feat: label lines as code, comment, docstring or blank with Pygments"
```

---

### Task 4: Rule engine

**Files:**
- Create: `src/commit_anatomy/classify.py`
- Test: `tests/unit/test_classify.py`

**Interfaces:**
- Consumes: `model.Rule`, `model.Scope`, `model.Match`, `model.Commit`, `taxonomy.Taxonomy`, `taxonomy.UNCLASSIFIED`.
- Produces:
  - `classify.GLOB_FLAGS` (wcmatch `GLOBSTAR | BRACE | DOTGLOB`), `classify.DATE_RE`.
  - `classify.relative(path: str, root: str) -> str | None`.
  - `classify.RuleEntry(rule: Rule, origin: str, root: str = "")`.
  - `classify.order_rules(config_rules: Sequence[tuple[Rule, str]], plugin_rules: Sequence[tuple[Scope, Sequence[Rule]]]) -> list[RuleEntry]` — config rules first (in the given order), then every plugin `pre` rule, then plugin `normal` rules; plugins ordered deepest scope first, `core` last; origin `plugin:<name>` or `plugin:<name>@<root>`.
  - `classify.Windows(is_ancestor: Callable[[str, str], bool])` with `applies(rule, commit) -> bool`.
  - `classify.Classifier(entries, taxonomy, windows, head: Callable[[str], list[str]])` with attribute `entries` and `classify(path: str, blob: str | None, commit: Commit) -> Match`. The fallback is `Match("unclassified", "fallback", "none", "", "")`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_classify.py`:

```python
from datetime import datetime

from commit_anatomy.classify import Classifier, RuleEntry, Windows, order_rules
from commit_anatomy.model import Commit, Rule, Scope
from commit_anatomy.taxonomy import Taxonomy


def commit(sha="c1", date="2026-05-01T00:00:00+00:00"):
    when = datetime.fromisoformat(date)
    return Commit(sha, (), "T", "t@example.com", when, when, "msg")


def make(entries, heads=None, ancestors=frozenset(), taxonomy=None):
    windows = Windows(lambda a, b: (a, b) in ancestors)
    return Classifier(
        entries, taxonomy or Taxonomy(), windows, head=lambda blob: (heads or {}).get(blob, [])
    )


def test_first_match_wins_and_reports_the_pattern():
    c = make(
        [
            RuleEntry(Rule("a", "docs/user", ("docs/manual/**",)), "cfg"),
            RuleEntry(Rule("b", "docs/dev", ("docs/**",)), "cfg"),
        ]
    )
    m = c.classify("docs/manual/en/x.md", None, commit())
    assert (m.category, m.rule_id, m.origin, m.matched) == (
        "docs/user",
        "a",
        "cfg",
        "docs/manual/**",
    )
    assert c.classify("docs/dev/y.md", None, commit()).category == "docs/dev"


def test_fallback_is_unclassified():
    m = make([]).classify("x.bin", None, commit())
    assert (m.category, m.rule_id, m.origin) == ("unclassified", "fallback", "none")


def test_globstar_matches_zero_directories_and_dotfiles():
    c = make([RuleEntry(Rule("md", "docs/dev", ("**/*.md", ".*rc")), "cfg")])
    assert c.classify("README.md", None, commit()).category == "docs/dev"
    assert c.classify("a/b/c.md", None, commit()).category == "docs/dev"
    assert c.classify(".prettierrc", None, commit()).category == "docs/dev"


def test_scoped_rule_only_matches_inside_its_root():
    c = make([RuleEntry(Rule("t", "tests/unit", ("Tests/**",)), "plugin:xcode@Kit", "Kit")])
    m = c.classify("Kit/Tests/A.swift", None, commit())
    assert (m.category, m.root) == ("tests/unit", "Kit")
    assert c.classify("Tests/A.swift", None, commit()).category == "unclassified"
    assert c.classify("KitX/Tests/A.swift", None, commit()).category == "unclassified"


def test_content_rule_reads_only_content_lines_of_the_head():
    gen = Rule("g", "supporting/generated", content=r"@generated", content_lines=2)
    c = make(
        [RuleEntry(gen, "core")],
        heads={"b1": ["x", "// @generated"], "b2": ["x", "y", "// @generated"]},
    )
    m = c.classify("a.ts", "b1", commit())
    assert (m.category, m.matched) == ("supporting/generated", "content:@generated")
    assert c.classify("a.ts", "b2", commit()).category == "unclassified"
    assert c.classify("a.ts", None, commit()).category == "unclassified"  # no blob: binary


def test_paths_and_content_must_both_match():
    r = Rule("g", "supporting/generated", ("src/**",), content="DO NOT EDIT")
    c = make([RuleEntry(r, "cfg")], heads={"b": ["// DO NOT EDIT"]})
    assert c.classify("src/a.ts", "b", commit()).matched == "src/** + content:DO NOT EDIT"
    assert c.classify("lib/a.ts", "b", commit()).category == "unclassified"


def test_date_window_since_inclusive_until_exclusive():
    r = Rule("w", "docs/user", ("docs/**",), since="2026-03-01", until="2026-06-01")
    c = make([RuleEntry(r, "cfg")])
    at = lambda d: c.classify("docs/a.md", None, commit(date=d)).category  # noqa: E731
    assert at("2026-02-28T23:59:59+00:00") == "unclassified"
    assert at("2026-03-01T00:00:00+00:00") == "docs/user"
    assert at("2026-06-01T00:00:00+00:00") == "unclassified"


def test_commit_window_uses_ancestry():
    r = Rule("w", "docs/user", ("docs/**",), until="X")
    c = make([RuleEntry(r, "cfg")], ancestors={("X", "after")})
    assert c.classify("docs/a.md", None, commit(sha="before")).category == "docs/user"
    assert c.classify("docs/a.md", None, commit(sha="after")).category == "unclassified"


def test_remap_applies_to_every_match():
    taxonomy = Taxonomy().extended(
        {"translations": {}, "remap": {"supporting/translations": "translations"}}
    )
    c = make(
        [RuleEntry(Rule("l", "supporting/translations", ("locale/**",)), "p")],
        taxonomy=taxonomy,
    )
    assert c.classify("locale/sl/x.po", None, commit()).category == "translations"


def test_order_rules_config_then_pre_then_deepest_scope_then_core():
    core, dj, kit = Scope("core", ""), Scope("django", ""), Scope("xcode", "Kit")
    pre = Rule("core:gen", "supporting/generated", content="x", stage="pre")
    entries = order_rules(
        [(Rule("u", "docs/dev", ("a",)), "user:x.toml")],
        [
            (core, [pre, Rule("core:docs", "docs/dev", ("docs/**",))]),
            (dj, [Rule("dj", "code/app", ("src/**",))]),
            (kit, [Rule("kit", "tests/unit", ("Tests/**",))]),
        ],
    )
    assert [e.rule.id for e in entries] == ["u", "core:gen", "kit", "dj", "core:docs"]
    assert (entries[2].origin, entries[2].root) == ("plugin:xcode@Kit", "Kit")
    assert entries[0].origin == "user:x.toml"
```

- [ ] **Step 2: Run to see failure**

Run: `uv run pytest tests/unit/test_classify.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.classify'`.

- [ ] **Step 3: Implement**

`src/commit_anatomy/classify.py`:

```python
"""First-match rule engine with scopes, content rules and time windows."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from wcmatch import glob

from .model import Commit, Match, Rule, Scope
from .taxonomy import UNCLASSIFIED, Taxonomy

GLOB_FLAGS = glob.GLOBSTAR | glob.BRACE | glob.DOTGLOB
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")
CORE = "core"

_regex = lru_cache(maxsize=None)(re.compile)


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
```

- [ ] **Step 4: Run to see pass**

Run: `uv run pytest tests/unit/test_classify.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/commit_anatomy/classify.py tests/unit/test_classify.py
git commit -m "feat: first-match rule engine with scopes, content rules and windows"
```

---

### Task 5: Configuration loading

**Files:**
- Create: `src/commit_anatomy/config.py`, `tests/conftest.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `model.Rule`, `taxonomy.Taxonomy`, `taxonomy.TaxonomyError`, `intent.DEFAULT_KEYWORDS`, `intent.INTENTS`, `classify.DATE_RE`, `classify.GLOB_FLAGS`.
- Produces:
  - `config.REPO_FILE = ".commit-anatomy.toml"`, `config.MAX_CONTENT_LINES = 20`.
  - `config.ConfigError(file, field, reason)`; `str()` is `"<file>: <field>: <reason>"`.
  - `config.IntentConfig(keywords: dict[str, tuple[str, ...]], overrides: dict[str, str])`.
  - `config.EffectiveConfig(rules: tuple[tuple[Rule, str], ...], taxonomy, intent, files: tuple[Path, ...])` with `EffectiveConfig.default()`.
  - `config.config_home() -> Path`, `config.normalize_remote(url) -> str`.
  - `config.load_config(repo_root: Path, remote: str | None, resolve_commit: Callable[[str], str | None]) -> EffectiveConfig`.
- Also produces the shared test fixture file `tests/conftest.py` (isolated env vars), used by every later task.

- [ ] **Step 1: Create the shared test fixtures**

`tests/conftest.py`:

```python
import os

import pytest

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
}


@pytest.fixture(autouse=True)
def isolated_env(tmp_path_factory, monkeypatch):
    """Keep tests away from the developer's git config, user config and cache."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("COMMIT_ANATOMY_CACHE_DIR", str(home / "cache"))
    monkeypatch.setenv("COLUMNS", "200")
    for key, value in GIT_IDENTITY.items():
        monkeypatch.setenv(key, value)
    return home
```

- [ ] **Step 2: Write failing config tests**

`tests/unit/test_config.py`:

```python
from pathlib import Path

import pytest

from commit_anatomy.config import ConfigError, config_home, load_config, normalize_remote

FULL = "a1b2c3d" + "0" * 33


def resolve(rev):
    return {"a1b2c3d": FULL}.get(rev)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def user_repos():
    return config_home() / "repos"


def test_no_config_gives_defaults(tmp_path):
    cfg = load_config(tmp_path, None, resolve)
    assert cfg.rules == () and cfg.files == ()
    assert cfg.taxonomy.is_valid("docs/llm")
    assert cfg.intent.overrides == {}


def test_repo_file_rule_gets_generated_id_and_origin(tmp_path):
    write(
        tmp_path / ".commit-anatomy.toml",
        '[[rules]]\npaths = ["docs/manual/**"]\ncategory = "docs/user"\n',
    )
    cfg = load_config(tmp_path, None, resolve)
    ((rule, origin),) = cfg.rules
    assert rule.paths == ("docs/manual/**",) and rule.category == "docs/user"
    assert origin == ".commit-anatomy.toml" and rule.id == ".commit-anatomy.toml#1"
    assert cfg.files == (tmp_path / ".commit-anatomy.toml",)


def test_user_file_matched_by_path_comes_before_repo_file(tmp_path, user_repos):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(
        repo / ".commit-anatomy.toml",
        '[[rules]]\nid = "team"\npaths = ["docs/**"]\ncategory = "docs/dev"\n',
    )
    write(
        user_repos / "mine.toml",
        f'match = {{ path = "{repo}" }}\n'
        '[[rules]]\nid = "mine"\npaths = ["docs/**"]\ncategory = "docs/user"\n',
    )
    write(
        user_repos / "other.toml",
        'match = { path = "/elsewhere" }\n'
        '[[rules]]\nid = "other"\npaths = ["x"]\ncategory = "docs/user"\n',
    )
    cfg = load_config(repo, None, resolve)
    assert [r.id for r, _ in cfg.rules] == ["mine", "team"]
    assert cfg.rules[0][1] == "user:mine.toml"


def test_user_file_matched_by_remote(tmp_path, user_repos):
    write(
        user_repos / "ios.toml",
        'match = { remote = "github.com/Hrcek-app/hrcek-ios" }\n'
        '[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\n',
    )
    assert len(load_config(tmp_path, "git@github.com:Hrcek-app/hrcek-ios.git", resolve).rules) == 1
    assert load_config(tmp_path, None, resolve).rules == ()


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:Hrcek-app/hrcek-ios.git",
        "https://github.com/Hrcek-app/hrcek-ios.git",
        "ssh://git@github.com:22/Hrcek-app/hrcek-ios",
        "https://github.com/hrcek-app/hrcek-ios/",
        "github.com/Hrcek-app/hrcek-ios",
    ],
)
def test_normalize_remote(url):
    assert normalize_remote(url) == "github.com/hrcek-app/hrcek-ios"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ('[[rules]]\npaths = ["x"]\ncategory = "docs/nope"\n', "rules[0].category"),
        ('[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\ncolour = "red"\n', "rules[0].colour"),
        ('[[rules]]\ncontent = "("\ncategory = "docs/dev"\n', "rules[0].content"),
        ('[[rules]]\ncategory = "docs/dev"\n', "rules[0]"),
        (
            '[[rules]]\ncontent = "a"\ncategory = "docs/dev"\ncontent_lines = 21\n',
            "rules[0].content_lines",
        ),
        ('[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\nuntil = "nope"\n', "rules[0].until"),
        ('[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\nstage = "late"\n', "rules[0].stage"),
        ("surprise = 1\n", "surprise"),
        ('[intent.overrides]\nxyz = "fix"\n', "intent.overrides.xyz"),
        ('[intent.keywords]\nmagic = ["x"]\n', "intent.keywords.magic"),
        ('[taxonomy.docs]\ncolour = "red"\n', "taxonomy"),
        ('match = { path = "/x" }\n', "match"),
        ("[[rules]\n", "toml"),
    ],
)
def test_invalid_repo_config_names_file_and_field(tmp_path, body, field):
    write(tmp_path / ".commit-anatomy.toml", body)
    with pytest.raises(ConfigError) as error:
        load_config(tmp_path, None, resolve)
    assert str(error.value).startswith(f"{tmp_path / '.commit-anatomy.toml'}: {field}:")


def test_user_file_without_match_is_an_error(tmp_path, user_repos):
    write(user_repos / "x.toml", '[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\n')
    with pytest.raises(ConfigError, match="match"):
        load_config(tmp_path, None, resolve)


def test_since_date_and_until_commit_are_resolved(tmp_path):
    write(
        tmp_path / ".commit-anatomy.toml",
        '[[rules]]\npaths = ["docs/**"]\ncategory = "docs/user"\n'
        'since = 2026-03-01\nuntil = "a1b2c3d"\n',
    )
    rule = load_config(tmp_path, None, resolve).rules[0][0]
    assert rule.since == "2026-03-01"
    assert rule.until == FULL


def test_taxonomy_and_intent_merge_with_user_winning(tmp_path, user_repos):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(
        repo / ".commit-anatomy.toml",
        '[taxonomy.docs]\nsubcategories = ["adr"]\n'
        '[intent.keywords]\nfeat = ["keep"]\n'
        '[intent.overrides]\nabcd = "fix"\n',
    )
    write(
        user_repos / "mine.toml",
        f'match = {{ path = "{repo}" }}\n'
        '[intent.keywords]\nfeat = ["save"]\n'
        '[[rules]]\npaths = ["docs/adr/**"]\ncategory = "docs/adr"\n',
    )
    cfg = load_config(repo, None, resolve)
    assert cfg.taxonomy.is_valid("docs/adr")
    assert cfg.intent.keywords["feat"] == ("save",)
    assert cfg.intent.keywords["fix"][0] == "fix*"  # untouched defaults stay
    assert cfg.intent.overrides == {"abcd": "fix"}
    assert cfg.rules[0][0].category == "docs/adr"
```

- [ ] **Step 3: Run to see failure**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.config'`.

- [ ] **Step 4: Implement**

`src/commit_anatomy/config.py`:

```python
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


def _matches(path: Path, data: dict, repo_root: Path, remote: str | None) -> bool:
    match = data.get("match")
    if not isinstance(match, dict) or len(match) != 1 or not set(match) <= {"path", "remote"}:
        raise ConfigError(
            path,
            "match",
            'a user config needs match = { path = "..." } or match = { remote = "..." }',
        )
    if "path" in match:
        return Path(match["path"]).expanduser().resolve() == repo_root.resolve()
    return remote is not None and normalize_remote(match["remote"]) == normalize_remote(remote)


def _intent(path: Path, table) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    if not isinstance(table, dict):
        raise ConfigError(path, "intent", "must be a table")
    unknown = set(table) - {"keywords", "overrides"}
    if unknown:
        raise ConfigError(path, f"intent.{sorted(unknown)[0]}", "unknown field")
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
```

- [ ] **Step 5: Run to see pass**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: `25 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/commit_anatomy/config.py tests/conftest.py tests/unit/test_config.py
git commit -m "feat: load and validate user and in-repo configuration"
```

---

### Task 6: Git repository wrapper and test repositories

**Files:**
- Create: `src/commit_anatomy/git.py`, `tests/helpers.py`
- Modify: `tests/conftest.py` (add the `repo` fixture)
- Test: `tests/integration/test_git_repo.py`

**Interfaces:**
- Consumes: `model.Commit`.
- Produces:
  - `git.GitError(Exception)`.
  - `git.BlobReader(cwd)` with `read(name: str) -> bytes | None` (an object id or `HEAD:<path>`) and `close()`.
  - `git.GitRepo(path)` with attributes `cwd`, `root: Path` and methods
    `list_commits(revspecs: list[str], *, history=False, since=None, until=None, merges=False) -> list[str]` (oldest first when walking; given order otherwise),
    `commits(shas: list[str]) -> list[Commit]` (same order as `shas`),
    `head_files() -> list[str]`, `read_head_file(path) -> bytes | None`, `read_blob(oid) -> bytes | None`,
    `remote_url() -> str | None`, `is_shallow() -> bool`, `is_ancestor(ancestor, descendant) -> bool`,
    `resolve_commit(rev) -> str | None`, `empty_tree() -> str`, `close()`.
  - `tests/helpers.ScriptedRepo(path)` with `git(*args, env=None) -> str`, `write(files: dict[str, str | bytes | None])` (None deletes), `commit(message, files=None, when=None) -> str` (commit *n* is dated `2026-01-05T12:00Z + n days`; `--allow-empty`).
  - Fixture `repo` → a fresh `ScriptedRepo` at `tmp_path / "repo"`.

- [ ] **Step 1: Add the test helpers**

`tests/helpers.py`:

```python
"""Throwaway git repositories for tests."""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

START = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)  # a Monday


class ScriptedRepo:
    def __init__(self, path: Path):
        path.mkdir(parents=True)
        self.path = path
        self._commits = 0
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, env: dict | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
        )
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
        return result.stdout

    def write(self, files: dict[str, str | bytes | None]) -> None:
        for rel, content in files.items():
            target = self.path / rel
            if content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def commit(self, message: str, files=None, when: datetime | None = None) -> str:
        if files:
            self.write(files)
        when = when or START + timedelta(days=self._commits)
        self._commits += 1
        stamp = when.isoformat()
        self.git("add", "-A")
        self.git(
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
            env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
        )
        return self.git("rev-parse", "HEAD").strip()
```

Append to `tests/conftest.py`:

```python
from helpers import ScriptedRepo  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    return ScriptedRepo(tmp_path / "repo")
```

and add to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
pythonpath = ["tests"]
```

- [ ] **Step 2: Write failing tests**

`tests/integration/test_git_repo.py`:

```python
import pytest

from commit_anatomy.git import GitError, GitRepo


def test_not_a_git_repository(tmp_path):
    with pytest.raises(GitError, match="not a git repository"):
        GitRepo(tmp_path)


def test_list_commits_single_walk_range_and_since(repo):
    a = repo.commit("one", {"a.txt": "1\n"})
    b = repo.commit("two", {"a.txt": "2\n"})
    c = repo.commit("three", {"a.txt": "3\n"})
    g = GitRepo(repo.path)
    assert g.list_commits(["HEAD"]) == [c]
    assert g.list_commits(["HEAD"], history=True) == [a, b, c]
    assert g.list_commits([f"{a}..HEAD"]) == [b, c]
    assert g.list_commits([c, a]) == [c, a]
    assert g.list_commits(["HEAD..HEAD"]) == []
    assert g.list_commits(["HEAD"], since="2026-01-06T00:00:00+00:00") == [b, c]


def test_bad_revspec_is_a_git_error(repo):
    repo.commit("one", {"a.txt": "1\n"})
    with pytest.raises(GitError, match="nope"):
        GitRepo(repo.path).list_commits(["nope"])


def test_merges_skipped_when_walking_but_kept_when_named(repo):
    base = repo.commit("base", {"a.txt": "1\n"})
    repo.git("checkout", "-q", "-b", "side")
    side = repo.commit("side", {"b.txt": "1\n"})
    repo.git("checkout", "-q", "main")
    main = repo.commit("main", {"c.txt": "1\n"})
    repo.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = repo.git("rev-parse", "HEAD").strip()
    g = GitRepo(repo.path)
    assert set(g.list_commits(["HEAD"], history=True)) == {base, side, main}
    assert merge in g.list_commits(["HEAD"], history=True, merges=True)
    assert g.list_commits([merge]) == [merge]


def test_commit_metadata(repo):
    sha = repo.commit("Add the thing\n\nWhy it matters.\n", {"a.txt": "1\n"})
    g = GitRepo(repo.path)
    (c,) = g.commits([sha])
    assert c.sha == sha and c.parents == ()
    assert c.subject == "Add the thing" and c.body == "Why it matters."
    assert (c.author_name, c.author_email) == ("Test Author", "author@example.com")
    assert c.author_date.isoformat() == "2026-01-05T12:00:00+00:00"
    assert g.commits([]) == []


def test_head_files_and_reading_them(repo):
    repo.commit("one", {"a.txt": "1\n", "dir/with space.md": "x\n"})
    g = GitRepo(repo.path)
    assert g.head_files() == ["a.txt", "dir/with space.md"]
    assert g.read_head_file("dir/with space.md") == b"x\n"
    assert g.read_head_file("missing file.txt") is None
    g.close()


def test_empty_repository_has_no_head_files(repo):
    assert GitRepo(repo.path).head_files() == []


def test_remote_shallow_ancestry_and_resolution(repo):
    a = repo.commit("one", {"a.txt": "1\n"})
    b = repo.commit("two", {"a.txt": "2\n"})
    g = GitRepo(repo.path)
    assert g.remote_url() is None
    repo.git("remote", "add", "origin", "git@github.com:me/x.git")
    assert g.remote_url() == "git@github.com:me/x.git"
    assert g.is_shallow() is False
    assert g.is_ancestor(a, b) and g.is_ancestor(a, a) and not g.is_ancestor(b, a)
    assert g.resolve_commit(a[:7]) == a
    assert g.resolve_commit("nope") is None
    assert len(g.empty_tree()) in (40, 64)


def test_root_is_the_toplevel_even_from_a_subdirectory(repo):
    repo.commit("one", {"sub/a.txt": "1\n"})
    assert GitRepo(repo.path / "sub").root == repo.path.resolve()
```

- [ ] **Step 3: Run to see failure**

Run: `uv run pytest tests/integration/test_git_repo.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.git'`.

- [ ] **Step 4: Implement**

`src/commit_anatomy/git.py`:

```python
"""Thin wrapper around the git CLI: commits, metadata, diffs and blobs."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from .model import Commit

_LOG_FORMAT = "%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%B"


class GitError(Exception):
    pass


class BlobReader:
    """One long-lived `git cat-file --batch` process."""

    def __init__(self, cwd: Path):
        self._cwd = cwd
        self._proc: subprocess.Popen[bytes] | None = None

    def read(self, name: str) -> bytes | None:
        if self._proc is None:
            self._proc = subprocess.Popen(
                ["git", "cat-file", "--batch"],
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(name.encode("utf-8") + b"\n")
        self._proc.stdin.flush()
        header = self._proc.stdout.readline()
        if not header or header.rstrip().endswith((b" missing", b" ambiguous")):
            return None
        size = int(header.split()[-1])
        data = self._proc.stdout.read(size)
        self._proc.stdout.read(1)  # the LF after the content
        return data

    def close(self) -> None:
        if self._proc is not None:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            self._proc.wait()
            self._proc = None


class GitRepo:
    def __init__(self, path: Path | str = "."):
        self.cwd = Path(path)
        if not self.cwd.is_dir():
            raise GitError(f"{self.cwd}: not a directory")
        self.root = Path(self._run("rev-parse", "--show-toplevel").strip())
        self._reader = BlobReader(self.root)
        self._empty_tree: str | None = None

    def _call(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=self.cwd,
            input=input,
            capture_output=True,
        )

    def _run(self, *args: str, input: bytes | None = None) -> str:
        result = self._call(*args, input=input)
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()
            raise GitError(message or f"git {args[0]} failed with exit code {result.returncode}")
        return result.stdout.decode("utf-8", "replace")

    def list_commits(
        self,
        revspecs: list[str],
        *,
        history: bool = False,
        since: str | None = None,
        until: str | None = None,
        merges: bool = False,
    ) -> list[str]:
        walk = (
            history
            or since is not None
            or until is not None
            or any(".." in r or r.startswith("^") for r in revspecs)
        )
        if walk:
            args = ["rev-list", "--reverse"]
            if not merges:
                args.append("--no-merges")
            if since:
                args.append(f"--since={since}")
            if until:
                args.append(f"--until={until}")
        else:
            args = ["rev-list", "--no-walk=unsorted"]
        return self._run(*args, *revspecs, "--").split()

    def commits(self, shas: list[str]) -> list[Commit]:
        if not shas:
            return []
        out = self._run(
            "-c",
            "log.showSignature=false",
            "log",
            "--no-walk=unsorted",
            "-z",
            f"--format={_LOG_FORMAT}",
            "--stdin",
            input=("\n".join(shas) + "\n").encode(),
        )
        found: dict[str, Commit] = {}
        for record in out.split("\x00"):
            if not record.strip():
                continue
            sha, parents, name, email, authored, committed, message = record.split("\x1f", 6)
            sha = sha.strip()
            found[sha] = Commit(
                sha=sha,
                parents=tuple(parents.split()),
                author_name=name,
                author_email=email,
                author_date=datetime.fromisoformat(authored),
                committer_date=datetime.fromisoformat(committed),
                message=message.rstrip("\n"),
            )
        return [found[s] for s in shas]

    def head_files(self) -> list[str]:
        if self._call("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
            return []
        out = self._run("ls-tree", "-r", "--name-only", "-z", "HEAD")
        return [p for p in out.split("\x00") if p]

    def read_head_file(self, path: str) -> bytes | None:
        return self._reader.read(f"HEAD:{path}")

    def read_blob(self, oid: str) -> bytes | None:
        return self._reader.read(oid)

    def remote_url(self) -> str | None:
        result = self._call("config", "--get", "remote.origin.url")
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def is_shallow(self) -> bool:
        return self._run("rev-parse", "--is-shallow-repository").strip() == "true"

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self._call("merge-base", "--is-ancestor", ancestor, descendant)
        if result.returncode in (0, 1):
            return result.returncode == 0
        raise GitError(result.stderr.decode("utf-8", "replace").strip())

    def resolve_commit(self, rev: str) -> str | None:
        result = self._call("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def empty_tree(self) -> str:
        if self._empty_tree is None:
            self._empty_tree = self._run("hash-object", "-t", "tree", "--stdin", input=b"").strip()
        return self._empty_tree

    def close(self) -> None:
        self._reader.close()
```

- [ ] **Step 5: Run to see pass**

Run: `uv run pytest tests/integration/test_git_repo.py -q`
Expected: `9 passed`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/commit_anatomy/git.py tests/helpers.py tests/conftest.py tests/integration/test_git_repo.py
git commit -m "feat: git wrapper for listing commits, metadata and blobs"
```

---

### Task 7: Diff parsing (file changes with changed line ranges)

**Files:**
- Modify: `src/commit_anatomy/git.py`
- Test: `tests/unit/test_parse_diff.py`, `tests/integration/test_git_diff.py`

**Interfaces:**
- Consumes: `model.FileChange`, `model.Commit`, `GitRepo` from Task 6.
- Produces: `git.DIFF_ARGS`, `git.DIFF_OPTIONS: str` (part of the commit-facts cache key), `git.unquote(text) -> str`, `git.parse_diff_tree(text) -> list[FileChange]` (raw-entry order), `GitRepo.diff(commit) -> list[FileChange]` (against the first parent, or the empty tree for a root commit).

- [ ] **Step 1: Write failing parser unit tests**

`tests/unit/test_parse_diff.py` (the sample mirrors real `git diff-tree --raw -p -U0 --full-index` output):

```python
from commit_anatomy.git import parse_diff_tree, unquote
from commit_anatomy.model import FileChange

Z = "0" * 40
A, B, C, D = "a" * 40, "b" * 40, "c" * 40, "d" * 40

SAMPLE = "\n".join(
    [
        f":100644 100644 {A} {B} M\tHrček.md",
        f":100644 100644 {A} {B} M\tbin.dat",
        f":100644 120000 {A} {B} T\tlink",
        f":100644 100755 {C} {C} M\tmode.sh",
        f":100644 100644 {D} {D} R100\tkeep.txt\tmoved.txt",
        f':000000 100644 {Z} {B} A\t"q\\"uote.txt"',
        f":100644 000000 {A} {Z} D\tgone.txt",
        f":000000 160000 {Z} {C} A\tsm",
        "",
        "diff --git a/Hrček.md b/Hrček.md",
        f"index {A}..{B} 100644",
        "--- a/Hrček.md",
        "+++ b/Hrček.md",
        "@@ -2 +2 @@ one",
        "-two",
        "+TWO",
        "@@ -4,0 +5 @@ four",
        "+five",
        "diff --git a/bin.dat b/bin.dat",
        f"index {A}..{B} 100644",
        "Binary files a/bin.dat and b/bin.dat differ",
        "diff --git a/link b/link",
        "deleted file mode 100644",
        f"index {A}..{Z}",
        "--- a/link",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-t",
        "diff --git a/link b/link",
        "new file mode 120000",
        f"index {Z}..{B}",
        "--- /dev/null",
        "+++ b/link",
        "@@ -0,0 +1 @@",
        "+keep.txt",
        "\\ No newline at end of file",
        "diff --git a/mode.sh b/mode.sh",
        "old mode 100644",
        "new mode 100755",
        "diff --git a/keep.txt b/moved.txt",
        "similarity index 100%",
        "rename from keep.txt",
        "rename to moved.txt",
        'diff --git "a/q\\"uote.txt" "b/q\\"uote.txt"',
        "new file mode 100644",
        f"index {Z}..{B}",
        "--- /dev/null",
        '+++ "b/q\\"uote.txt"',
        "@@ -0,0 +1,2 @@",
        "+a",
        "+b",
        "diff --git a/gone.txt b/gone.txt",
        "deleted file mode 100644",
        f"index {A}..{Z}",
        "--- a/gone.txt",
        "+++ /dev/null",
        "@@ -1,3 +0,0 @@",
        "-1",
        "-2",
        "-3",
        "diff --git a/sm b/sm",
        "new file mode 160000",
        f"index {Z}..{C}",
        "--- /dev/null",
        "+++ b/sm",
        "@@ -0,0 +1 @@",
        f"+Subproject commit {C}",
        "",
    ]
)


def test_parse_diff_tree():
    changes = parse_diff_tree(SAMPLE)
    by_path = {c.path: c for c in changes}
    assert [c.path for c in changes] == [
        "Hrček.md",
        "bin.dat",
        "link",
        "mode.sh",
        "moved.txt",
        'q"uote.txt',
        "gone.txt",
        "sm",
    ]
    assert by_path["Hrček.md"] == FileChange(
        "Hrček.md", "Hrček.md", "M", False, None, A, B, ((2, 1),), ((2, 1), (5, 1))
    )
    assert by_path["bin.dat"].binary and by_path["bin.dat"].added_lines == ()
    link = by_path["link"]
    assert (link.status, link.removed_lines, link.added_lines) == ("T", ((1, 1),), ((1, 1),))
    assert (by_path["mode.sh"].removed_lines, by_path["mode.sh"].added_lines) == ((), ())
    moved = by_path["moved.txt"]
    assert (moved.old_path, moved.new_path, moved.status, moved.similarity) == (
        "keep.txt",
        "moved.txt",
        "R",
        100,
    )
    quote = by_path['q"uote.txt']
    assert (quote.old_path, quote.old_blob, quote.added_lines) == (None, None, ((1, 2),))
    gone = by_path["gone.txt"]
    assert (gone.new_path, gone.new_blob, gone.removed_lines) == (None, None, ((1, 3),))
    assert by_path["sm"].submodule and by_path["sm"].added_lines == ()


def test_unquote():
    assert unquote('"tab\\there.txt"') == "tab\there.txt"
    assert unquote('"caf\\303\\251.txt"') == "café.txt"
    assert unquote('"q\\"uote"') == 'q"uote'
    assert unquote("plain name.txt") == "plain name.txt"


def test_empty_output_has_no_changes():
    assert parse_diff_tree("") == []
```

- [ ] **Step 2: Write failing integration tests**

`tests/integration/test_git_diff.py`:

```python
import os

from helpers import ScriptedRepo

from commit_anatomy.git import GitRepo


def changes(repo, sha):
    g = GitRepo(repo.path)
    (commit,) = g.commits([sha])
    result = {c.path: c for c in g.diff(commit)}
    g.close()
    return result


def test_root_commit_is_diffed_against_the_empty_tree(repo):
    sha = repo.commit("root", {"a.txt": "1\n2\n"})
    ch = changes(repo, sha)["a.txt"]
    assert (ch.status, ch.old_path, ch.new_path, ch.old_blob, ch.added_lines) == (
        "A",
        None,
        "a.txt",
        None,
        ((1, 2),),
    )


def test_modify_delete_and_renames(repo):
    repo.commit(
        "root",
        {
            "edit.md": "one\ntwo\nthree\nfour\n",
            "gone.txt": "x\n",
            "keep.txt": "k\n",
            "move.txt": "1\n2\n3\n4\n5\n6\n7\n8\n",
        },
    )
    repo.git("mv", "keep.txt", "kept.txt")
    repo.git("mv", "move.txt", "moved.txt")
    sha = repo.commit(
        "second",
        {
            "edit.md": "one\nTWO\nthree\nfour\nfive\n",
            "gone.txt": None,
            "moved.txt": "1\n2\n3\nFOUR\n5\n6\n7\n8\n",
        },
    )
    ch = changes(repo, sha)
    assert ch["edit.md"].removed_lines == ((2, 1),)
    assert ch["edit.md"].added_lines == ((2, 1), (5, 1))
    gone = ch["gone.txt"]
    assert (gone.status, gone.new_path, gone.removed_lines) == ("D", None, ((1, 1),))
    kept = ch["kept.txt"]
    assert (kept.status, kept.old_path, kept.similarity, kept.added_lines) == (
        "R",
        "keep.txt",
        100,
        (),
    )
    moved = ch["moved.txt"]
    assert (moved.status, moved.old_path, moved.removed_lines, moved.added_lines) == (
        "R",
        "move.txt",
        ((4, 1),),
        ((4, 1),),
    )
    assert moved.similarity < 100


def test_binary_mode_symlink_and_submodule(repo, tmp_path):
    sub = ScriptedRepo(tmp_path / "sub")
    sub.commit("s", {"s.txt": "s\n"})
    repo.commit("root", {"bin.dat": b"\x00\x01", "run.sh": "echo\n", "link": "target\n"})
    os.chmod(repo.path / "run.sh", 0o755)
    (repo.path / "link").unlink()
    os.symlink("bin.dat", repo.path / "link")
    repo.write({"bin.dat": b"\x00\x02"})
    repo.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub.path), "sm")
    ch = changes(repo, repo.commit("second"))
    assert ch["bin.dat"].binary
    assert ch["run.sh"].status == "M" and ch["run.sh"].added_lines == ()
    assert ch["link"].status == "T"
    assert (ch["link"].removed_lines, ch["link"].added_lines) == (((1, 1),), ((1, 1),))
    assert ch["sm"].submodule and ch["sm"].added_lines == ()
    assert ".gitmodules" in ch


def test_non_ascii_and_quoted_paths(repo):
    names = ["Hrček.md", "with space.txt", 'q"uote.txt', "tab\there.txt"]
    ch = changes(repo, repo.commit("root", {n: "x\n" for n in names}))
    assert sorted(ch) == sorted(names)
    assert all(c.added_lines == ((1, 1),) for c in ch.values())


def test_no_trailing_newline(repo):
    repo.commit("root", {"a.txt": "x"})
    assert changes(repo, repo.commit("two", {"a.txt": "y"}))["a.txt"].added_lines == ((1, 1),)


def test_empty_commit_has_no_changes(repo):
    repo.commit("root", {"a.txt": "1\n"})
    assert changes(repo, repo.commit("empty")) == {}


def test_merge_is_diffed_against_its_first_parent(repo):
    repo.commit("base", {"a.txt": "1\n"})
    repo.git("checkout", "-q", "-b", "side")
    repo.commit("side", {"b.txt": "1\n"})
    repo.git("checkout", "-q", "main")
    repo.commit("main", {"c.txt": "1\n"})
    repo.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = repo.git("rev-parse", "HEAD").strip()
    assert list(changes(repo, merge)) == ["b.txt"]


def test_read_blob(repo):
    sha = repo.commit("root", {"a.txt": "hello\n"})
    g = GitRepo(repo.path)
    (commit,) = g.commits([sha])
    (ch,) = g.diff(commit)
    assert g.read_blob(ch.new_blob) == b"hello\n"
    assert g.read_blob("0" * 40) is None
    g.close()
```

- [ ] **Step 3: Run to see failure**

Run: `uv run pytest tests/unit/test_parse_diff.py tests/integration/test_git_diff.py -q`
Expected: FAIL with `ImportError: cannot import name 'parse_diff_tree'`.

- [ ] **Step 4: Implement**

In `src/commit_anatomy/git.py` extend the imports to:

```python
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .model import Commit, FileChange
```

Add after `_LOG_FORMAT`:

```python
DIFF_ARGS = (
    "-r",
    "-M",
    "--raw",
    "--no-abbrev",
    "-p",
    "-U0",
    "--full-index",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)
DIFF_OPTIONS = "diff-tree " + " ".join(DIFF_ARGS)

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote(text: str) -> str:
    """Undo git's C-style path quoting (escapes and octal UTF-8 bytes)."""
    if not text.startswith('"'):
        return text
    out = bytearray()
    i, end = 1, len(text) - 1
    while i < end:
        ch = text[i]
        if ch == "\\":
            nxt = text[i + 1]
            if nxt in "01234567":
                out.append(int(text[i + 1 : i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, ord(nxt)))
            i += 2
            continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace")


def _header_path(line: str) -> str:
    rest = line[len("diff --git ") :]
    if rest.startswith('"'):
        quoted = _QUOTED_RE.match(rest)
        return unquote(quoted.group(0))[2:] if quoted else rest
    # Not a rename (renames are re-keyed by "rename to"): "a/<path> b/<path>", same path twice.
    half = (len(rest) - len("a/ b/")) // 2
    return rest[2 : 2 + half]


@dataclass
class _Block:
    key: str
    removed: list[tuple[int, int]] = field(default_factory=list)
    added: list[tuple[int, int]] = field(default_factory=list)
    binary: bool = False


def parse_diff_tree(text: str) -> list[FileChange]:
    """Parse `git diff-tree --raw -p -U0 --full-index` output into FileChanges."""
    raw: list[tuple[str, str, str, str, str, list[str]]] = []
    blocks: dict[str, list[_Block]] = defaultdict(list)
    current: _Block | None = None
    for line in text.split("\n"):
        if current is None and line.startswith(":"):
            meta, *paths = line.split("\t")
            old_mode, new_mode, old_blob, new_blob, status = meta[1:].split(" ")
            raw.append((old_mode, new_mode, old_blob, new_blob, status, [unquote(p) for p in paths]))
        elif line.startswith("diff --git "):
            current = _Block(_header_path(line))
            blocks[current.key].append(current)
        elif current is None:
            continue
        elif line.startswith("rename to "):
            blocks[current.key].remove(current)
            current.key = unquote(line[len("rename to ") :])
            blocks[current.key].append(current)
        elif line.startswith("Binary files "):
            current.binary = True
        elif line.startswith("@@ "):
            hunk = _HUNK_RE.match(line)
            if hunk is None:
                continue
            old_start, old_count, new_start, new_count = hunk.groups()
            removed = 1 if old_count is None else int(old_count)
            added = 1 if new_count is None else int(new_count)
            if removed:
                current.removed.append((int(old_start), removed))
            if added:
                current.added.append((int(new_start), added))

    changes = []
    for old_mode, new_mode, old_blob, new_blob, status, paths in raw:
        letter = status[0]
        own = blocks.get(paths[-1], [])  # a typechange has two blocks for one path
        submodule = "160000" in (old_mode, new_mode)
        changes.append(
            FileChange(
                old_path=None if letter == "A" else paths[0],
                new_path=None if letter == "D" else paths[-1],
                status=letter,
                binary=any(b.binary for b in own),
                similarity=int(status[1:]) if len(status) > 1 else None,
                old_blob=None if set(old_blob) == {"0"} else old_blob,
                new_blob=None if set(new_blob) == {"0"} else new_blob,
                removed_lines=() if submodule else tuple(r for b in own for r in b.removed),
                added_lines=() if submodule else tuple(r for b in own for r in b.added),
                submodule=submodule,
            )
        )
    return changes
```

Add to `GitRepo`:

```python
    def diff(self, commit: Commit) -> list[FileChange]:
        base = commit.parents[0] if commit.parents else self.empty_tree()
        return parse_diff_tree(self._run("diff-tree", *DIFF_ARGS, base, commit.sha))
```

- [ ] **Step 5: Run to see pass**

Run: `uv run pytest tests/unit/test_parse_diff.py tests/integration/test_git_diff.py -q`
Expected: `11 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/commit_anatomy/git.py tests/unit/test_parse_diff.py tests/integration/test_git_diff.py
git commit -m "feat: parse commit diffs into file changes with changed line ranges"
```

---

### Task 8: Plugin API, discovery and the core plugin

**Files:**
- Create: `src/commit_anatomy/plugins/__init__.py` (empty), `src/commit_anatomy/plugins/base.py`, `src/commit_anatomy/plugins/core.py`, `tests/plugin_helpers.py`
- Modify: `pyproject.toml` (entry point)
- Test: `tests/unit/test_plugins_base.py`, `tests/golden/test_core_plugin.py`

**Interfaces:**
- Consumes: `model.Rule`, `model.Scope`, `classify.GLOB_FLAGS`, `classify.relative`, `classify.order_rules`, `classify.Classifier`, `classify.Windows`, `lines.split_lines`, `GitRepo.head_files`, `GitRepo.read_head_file`.
- Produces:
  - `plugins.base.ENTRY_POINT_GROUP = "commit_anatomy.plugins"`, `PluginError(Exception)`.
  - Protocols `RepoView` (`files() -> list[str]`, `read(path) -> bytes | None`) and `Plugin` (`name`, `detect(repo) -> list[Scope]`, `rules() -> list[Rule]`, `languages() -> dict[str, str]`).
  - `StaticRepoView(files: dict[str, str | bytes])`, `GitRepoView(repo: GitRepo)`.
  - `rule(id, category, *paths, content=None, content_lines=5, stage="normal") -> Rule`.
  - `load_plugins() -> list[Plugin]` (sorted by entry-point name), `active_scopes(plugins, view) -> list[tuple[Plugin, Scope]]`, `language_hint(active, path) -> str | None`.
  - `plugins.core.plugin` (name `"core"`).
  - `tests/plugin_helpers.classify_paths(plugins, files: dict[str, str | bytes]) -> dict[str, str]` (path → category).

- [ ] **Step 1: Register the core plugin and reinstall**

Add to `pyproject.toml`:

```toml
[project.entry-points."commit_anatomy.plugins"]
core = "commit_anatomy.plugins.core:plugin"
```

Run: `uv sync` (entry points are only visible after the package is reinstalled).

- [ ] **Step 2: Add the shared classification helper for plugin tests**

`tests/plugin_helpers.py`:

```python
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
```

- [ ] **Step 3: Write failing tests**

`tests/unit/test_plugins_base.py`:

```python
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
```

`tests/golden/test_core_plugin.py`:

```python
from plugin_helpers import classify_paths

from commit_anatomy.plugins.core import plugin as core

EXPECTED = {
    "CLAUDE.md": "docs/llm",
    "sub/AGENTS.md": "docs/llm",
    "REVIEW_GUIDE.md": "docs/llm",
    "docs/superpowers/specs/x-design.md": "docs/llm",
    "CHANGELOG.md": "docs/changelog",
    "README.md": "docs/dev",
    "docs/dev/setup.md": "docs/dev",
    "docs/notes.txt": "docs/dev",
    ".github/workflows/ci.yml": "supporting/ci",
    ".pre-commit-config.yaml": "supporting/ci",
    "scripts/release.sh": "code/tooling",
    ".githooks/pre-commit": "code/tooling",
    "LICENSE": "supporting/build",
    ".gitignore": "supporting/build",
    "Dockerfile": "supporting/build",
    ".prettierrc": "supporting/build",
    "assets/logo.png": "supporting/assets",
    "docs/img/shot.avif": "supporting/assets",
    "src/gen.ts": "supporting/generated",
    "src/app.py": "unclassified",
    "app/migrations/0001_initial.py": "unclassified",
}

CONTENT = {
    "src/gen.ts": "// Code generated by protoc. DO NOT EDIT.\nexport const x = 1\n",
    # A bare "do not edit" and Django's migration header must not count as generated.
    "CLAUDE.md": "# Rules\n\nDo not edit migrations by hand.\n",
    "app/migrations/0001_initial.py": "# Generated by Django 5.1 on 2026-01-05\n",
}


def test_core_rules():
    files = {path: "" for path in EXPECTED} | CONTENT
    assert classify_paths([core], files) == EXPECTED
```

- [ ] **Step 4: Run to see failure**

Run: `uv run pytest tests/unit/test_plugins_base.py tests/golden/test_core_plugin.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.plugins'`.

- [ ] **Step 5: Implement**

Create empty `src/commit_anatomy/plugins/__init__.py`.

`src/commit_anatomy/plugins/base.py`:

```python
"""Plugin protocol, discovery and helpers shared by plugins."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Protocol

from wcmatch import glob

from ..classify import CORE, GLOB_FLAGS, relative
from ..model import Rule, Scope, Stage

if TYPE_CHECKING:
    from ..git import GitRepo

ENTRY_POINT_GROUP = "commit_anatomy.plugins"


class PluginError(Exception):
    pass


class RepoView(Protocol):
    """Read-only view of the tree at HEAD, for detection."""

    def files(self) -> list[str]: ...

    def read(self, path: str) -> bytes | None: ...


class Plugin(Protocol):
    name: str

    def detect(self, repo: RepoView) -> list[Scope]: ...

    def rules(self) -> list[Rule]: ...

    def languages(self) -> dict[str, str]: ...


class StaticRepoView:
    def __init__(self, files: dict[str, str | bytes]):
        self._files = {p: c.encode() if isinstance(c, str) else c for p, c in files.items()}

    def files(self) -> list[str]:
        return sorted(self._files)

    def read(self, path: str) -> bytes | None:
        return self._files.get(path)


class GitRepoView:
    def __init__(self, repo: GitRepo):
        self._repo = repo
        self._files: list[str] | None = None

    def files(self) -> list[str]:
        if self._files is None:
            self._files = self._repo.head_files()
        return self._files

    def read(self, path: str) -> bytes | None:
        return self._repo.read_head_file(path)


def rule(
    id: str,
    category: str,
    *paths: str,
    content: str | None = None,
    content_lines: int = 5,
    stage: Stage = "normal",
) -> Rule:
    return Rule(
        id=id,
        category=category,
        paths=paths,
        content=content,
        content_lines=content_lines,
        stage=stage,
    )


def load_plugins() -> list[Plugin]:
    plugins = []
    for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        try:
            plugins.append(ep.load())
        except Exception as e:
            raise PluginError(f"plugin {ep.name!r} failed to load: {e}") from e
    return plugins


def active_scopes(plugins: list[Plugin], view: RepoView) -> list[tuple[Plugin, Scope]]:
    return [(plugin, scope) for plugin in plugins for scope in plugin.detect(view)]


def language_hint(active: list[tuple[Plugin, Scope]], path: str) -> str | None:
    ordered = sorted(active, key=lambda ps: (ps[0].name == CORE, -ps[1].depth))
    for plugin, scope in ordered:
        rel = relative(path, scope.root)
        if rel is None:
            continue
        for pattern, alias in plugin.languages().items():
            if glob.globmatch(rel, pattern, flags=GLOB_FLAGS):
                return alias
    return None
```

`src/commit_anatomy/plugins/core.py`:

```python
"""Generic rules that apply to every repository, with the lowest precedence."""

from ..model import Rule, Scope
from .base import RepoView, rule

GENERATED = (
    r"(?i)(@generated\b|\bauto-?generated\b"
    r"|\bgenerated\b.*\bdo not edit\b|\bdo not edit\b.*\bgenerated\b)"
)

RULES = [
    rule("core:generated", "supporting/generated", content=GENERATED, stage="pre"),
    rule(
        "core:llm-docs",
        "docs/llm",
        "**/CLAUDE.md",
        "**/AGENTS.md",
        "REVIEW_GUIDE.md",
        "docs/superpowers/**",
    ),
    rule("core:changelog", "docs/changelog", "**/CHANGELOG*"),
    rule("core:ci", "supporting/ci", ".github/**", ".pre-commit-config.yaml", ".gitlab-ci.yml"),
    rule("core:tooling", "code/tooling", "scripts/**", ".githooks/**"),
    rule(
        "core:build",
        "supporting/build",
        "LICENSE*",
        "**/.gitignore",
        ".dockerignore",
        ".gitleaks.toml",
        "Dockerfile",
        "docker/**",
        "deploy/**",
        ".*rc",
        ".editorconfig",
    ),
    rule(
        "core:assets",
        "supporting/assets",
        "**/*.{png,jpg,jpeg,gif,svg,webp,avif,ico,pdf,ttf,otf,woff,woff2}",
    ),
    rule("core:docs", "docs/dev", "docs/**", "**/*.{md,rst}"),
]


class CorePlugin:
    name = "core"

    def detect(self, repo: RepoView) -> list[Scope]:
        return [Scope(self.name, "")]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {}


plugin = CorePlugin()
```

- [ ] **Step 6: Run to see pass**

Run: `uv run pytest tests/unit/test_plugins_base.py tests/golden/test_core_plugin.py -q`
Expected: `7 passed`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/commit_anatomy/plugins tests/plugin_helpers.py tests/unit/test_plugins_base.py tests/golden/test_core_plugin.py
git commit -m "feat: plugin API with entry-point discovery and the core plugin"
```

---

### Task 9: Django, WXT and Xcode plugins

**Files:**
- Create: `src/commit_anatomy/plugins/django.py`, `src/commit_anatomy/plugins/wxt.py`, `src/commit_anatomy/plugins/xcode.py`
- Modify: `pyproject.toml` (entry points)
- Test: `tests/golden/test_django_plugin.py`, `tests/golden/test_wxt_plugin.py`, `tests/golden/test_xcode_plugin.py`, `tests/golden/test_monorepo.py`

**Interfaces:**
- Consumes: `plugins.base.rule`, `RepoView`, `model.Scope`, `model.Rule`, `tests/plugin_helpers.classify_paths`, `plugins.core.plugin`.
- Produces: `plugins.django.plugin` (name `"django"`, language hint `**/templates/**/*.{html,txt}` → `html+django`), `plugins.wxt.plugin` (name `"wxt"`), `plugins.xcode.plugin` (name `"xcode"`). Every rule id is `<plugin>:<slug>`.

- [ ] **Step 1: Register entry points and reinstall**

Extend the `pyproject.toml` entry-point table:

```toml
[project.entry-points."commit_anatomy.plugins"]
core = "commit_anatomy.plugins.core:plugin"
django = "commit_anatomy.plugins.django:plugin"
wxt = "commit_anatomy.plugins.wxt:plugin"
xcode = "commit_anatomy.plugins.xcode:plugin"
```

Run: `uv sync`

- [ ] **Step 2: Write failing golden tests**

`tests/golden/test_django_plugin.py` (skeleton modelled on the `hrcek` repo):

```python
from plugin_helpers import classify_paths

from commit_anatomy.model import Scope
from commit_anatomy.plugins.base import StaticRepoView, active_scopes, language_hint
from commit_anatomy.plugins.core import plugin as core
from commit_anatomy.plugins.django import plugin as django

PYPROJECT = '[project]\nname = "x"\ndependencies = ["django>=5.1", "pillow"]\n'

EXPECTED = {
    "manage.py": "code/app",
    "pyproject.toml": "supporting/deps",
    "uv.lock": "supporting/deps",
    "src/hrcek/entries/models.py": "code/app",
    "src/hrcek/entries/migrations/0001_initial.py": "supporting/migrations",
    "src/hrcek/templates/entries/list.html": "code/app",
    "src/hrcek/static/css/site.css": "code/app",
    "locale/sl/LC_MESSAGES/django.po": "supporting/translations",
    "docs/manual/en/index.md": "docs/user",
    "docs/api/v1.md": "docs/api",
    "docs/dev/architecture.md": "docs/dev",
    "docs/superpowers/plans/p.md": "docs/llm",
    "tests/conftest.py": "tests/support",
    "tests/test_entries.py": "tests/unit",
    "tests/e2e/test_flow.py": "tests/e2e",
    "scripts/dev.py": "code/tooling",
    "CLAUDE.md": "docs/llm",
    "README.md": "docs/dev",
    ".github/workflows/ci.yml": "supporting/ci",
}


def test_django_rules():
    files = {path: "" for path in EXPECTED} | {
        "pyproject.toml": PYPROJECT,
        "src/hrcek/entries/migrations/0001_initial.py": "# Generated by Django 5.1\n",
    }
    assert classify_paths([core, django], files) == EXPECTED


def test_detected_by_manage_py_or_django_dependency():
    assert django.detect(StaticRepoView({"manage.py": ""})) == [Scope("django", "")]
    assert django.detect(StaticRepoView({"api/pyproject.toml": PYPROJECT})) == [
        Scope("django", "api")
    ]
    groups = '[dependency-groups]\nweb = ["Django~=5.1"]\n'
    assert django.detect(StaticRepoView({"pyproject.toml": groups})) == [Scope("django", "")]
    stubs_only = '[project]\ndependencies = ["django-stubs"]\n'
    assert django.detect(StaticRepoView({"pyproject.toml": stubs_only})) == []
    assert django.detect(StaticRepoView({"pyproject.toml": "not toml ["})) == []


def test_templates_are_lexed_as_django_templates():
    active = active_scopes([core, django], StaticRepoView({"manage.py": ""}))
    assert language_hint(active, "src/hrcek/templates/a.html") == "html+django"
    assert language_hint(active, "src/hrcek/views.py") is None
```

`tests/golden/test_wxt_plugin.py` (skeleton modelled on `hrcek-extension`):

```python
from plugin_helpers import classify_paths

from commit_anatomy.model import Scope
from commit_anatomy.plugins.base import StaticRepoView
from commit_anatomy.plugins.core import plugin as core
from commit_anatomy.plugins.wxt import plugin as wxt

PACKAGE = '{"name": "x", "devDependencies": {"wxt": "^0.20.0"}}'

EXPECTED = {
    "package.json": "supporting/deps",
    "pnpm-lock.yaml": "supporting/deps",
    "pnpm-workspace.yaml": "supporting/deps",
    "src/entrypoints/popup/App.tsx": "code/app",
    "src/lib/queue.ts": "code/app",
    "src/lib/queue.test.ts": "tests/unit",
    "tests/e2e/save.spec.ts": "tests/e2e",
    "tests/contract/api.test.ts": "tests/integration",
    "tests/fake-hrcek/server.ts": "tests/support",
    "tests/entrypoints/popup.test.ts": "tests/unit",
    "src/locales/sl/messages.po": "supporting/translations",
    "wxt.config.ts": "supporting/build",
    "vitest.config.ts": "supporting/build",
    "tsconfig.json": "supporting/build",
    "eslint.config.js": "supporting/build",
    ".prettierrc": "supporting/build",
    "docs/superpowers/specs/s.md": "docs/llm",
    "README.md": "docs/dev",
}


def test_wxt_rules():
    files = {path: "" for path in EXPECTED} | {"package.json": PACKAGE}
    assert classify_paths([core, wxt], files) == EXPECTED


def test_detected_by_wxt_dependency_only():
    assert wxt.detect(StaticRepoView({"package.json": PACKAGE})) == [Scope("wxt", "")]
    assert wxt.detect(StaticRepoView({"package.json": '{"dependencies": {"react": "1"}}'})) == []
    assert wxt.detect(StaticRepoView({"package.json": "[1, 2]"})) == []
    assert wxt.detect(StaticRepoView({"package.json": "{broken"})) == []
```

`tests/golden/test_xcode_plugin.py` (skeleton modelled on `hrcek-ios`):

```python
from plugin_helpers import classify_paths

from commit_anatomy.model import Scope
from commit_anatomy.plugins.base import StaticRepoView
from commit_anatomy.plugins.core import plugin as core
from commit_anatomy.plugins.xcode import plugin as xcode

EXPECTED = {
    "Hrcek.xcodeproj/project.pbxproj": "supporting/build",
    "Hrcek.xcodeproj/xcshareddata/xcschemes/Hrcek.xcscheme": "supporting/build",
    "HrcekKit/Package.swift": "supporting/deps",
    "HrcekKit/Package.resolved": "supporting/deps",
    "HrcekKit/Sources/HrcekKit/Queue.swift": "code/app",
    "HrcekKit/Tests/HrcekKitTests/QueueTests.swift": "tests/unit",
    "Hrcek/ContentView.swift": "code/app",
    "HrcekShare/ShareView.swift": "code/app",
    "HrcekTests/AppTests.swift": "tests/unit",
    "HrcekUITests/SaveTests.swift": "tests/e2e",
    "Hrcek.xctestplan": "tests/support",
    "Hrcek/Localizable.xcstrings": "supporting/translations",
    "Config/Base.xcconfig": "supporting/build",
    ".swiftlint.yml": "supporting/build",
    "Support/Info.plist": "supporting/build",
    "Brewfile": "supporting/deps",
    "Hrcek/Assets.xcassets/AppIcon.appiconset/Contents.json": "supporting/assets",
    "scripts/ci.sh": "code/tooling",
    "AGENTS.md": "docs/llm",
}


def test_xcode_rules():
    assert classify_paths([core, xcode], {path: "" for path in EXPECTED}) == EXPECTED


def test_detects_project_and_nested_package_scopes():
    view = StaticRepoView({"Hrcek.xcodeproj/project.pbxproj": "", "HrcekKit/Package.swift": ""})
    assert xcode.detect(view) == [Scope("xcode", ""), Scope("xcode", "HrcekKit")]
    assert xcode.detect(StaticRepoView({"README.md": ""})) == []
```

`tests/golden/test_monorepo.py` (Review Focus 5):

```python
from plugin_helpers import classify_paths

from commit_anatomy.plugins.core import plugin as core
from commit_anatomy.plugins.django import plugin as django
from commit_anatomy.plugins.wxt import plugin as wxt


def test_plugin_rules_stay_inside_their_scope():
    files = {
        "backend/manage.py": "",
        "backend/src/app/views.py": "",
        "backend/docs/manual/a.md": "",
        "extension/package.json": '{"devDependencies": {"wxt": "1"}}',
        "extension/src/a.test.ts": "",
        "extension/tests/e2e/x.ts": "",
        "tests/x.py": "",  # outside every plugin scope
        "docs/manual/a.md": "",  # docs/manual means docs/user only inside the django scope
    }
    assert classify_paths([core, django, wxt], files) == {
        "backend/manage.py": "code/app",
        "backend/src/app/views.py": "code/app",
        "backend/docs/manual/a.md": "docs/user",
        "extension/package.json": "supporting/deps",
        "extension/src/a.test.ts": "tests/unit",
        "extension/tests/e2e/x.ts": "tests/e2e",
        "tests/x.py": "unclassified",
        "docs/manual/a.md": "docs/dev",
    }
```

- [ ] **Step 3: Run to see failure**

Run: `uv run pytest tests/golden -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.plugins.django'`.

- [ ] **Step 4: Implement the three plugins**

`src/commit_anatomy/plugins/django.py`:

```python
"""Django projects: detected by manage.py or a django dependency in pyproject.toml."""

import posixpath
import re
import tomllib

from ..model import Rule, Scope
from .base import RepoView, rule

_DJANGO_DEP = re.compile(r"^django(?:$|[\s\[<>=!~;])", re.IGNORECASE)

RULES = [
    rule("django:migrations", "supporting/migrations", "**/migrations/*.py"),
    rule("django:translations", "supporting/translations", "**/locale/**"),
    rule(
        "django:deps",
        "supporting/deps",
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "requirements*.txt",
        ".python-version",
    ),
    rule("django:user-docs", "docs/user", "docs/manual/**"),
    rule("django:api-docs", "docs/api", "docs/api/**"),
    rule("django:dev-docs", "docs/dev", "docs/dev/**"),
    rule(
        "django:test-support",
        "tests/support",
        "**/conftest.py",
        "tests/**/factories*.py",
        "tests/**/fixtures/**",
    ),
    rule("django:integration", "tests/integration", "tests/**/integration/**"),
    rule("django:e2e", "tests/e2e", "tests/**/e2e/**", "tests/e2e/**"),
    rule("django:tests", "tests/unit", "tests/**", "**/tests/**", "**/test_*.py"),
    rule("django:tooling", "code/tooling", "scripts/**"),
    rule(
        "django:app",
        "code/app",
        "manage.py",
        "src/**",
        "**/templates/**",
        "**/static/**",
        "**/*.py",
    ),
]


def _depends_on_django(content: bytes) -> bool:
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    deps = list(data.get("project", {}).get("dependencies", []))
    for group in data.get("dependency-groups", {}).values():
        deps += group
    return any(isinstance(d, str) and _DJANGO_DEP.match(d.strip()) for d in deps)


class DjangoPlugin:
    name = "django"

    def detect(self, repo: RepoView) -> list[Scope]:
        roots = []
        for path in repo.files():
            name = posixpath.basename(path)
            if name == "manage.py" or (
                name == "pyproject.toml" and _depends_on_django(repo.read(path) or b"")
            ):
                roots.append(posixpath.dirname(path))
        return [Scope(self.name, root) for root in dict.fromkeys(roots)]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {"**/templates/**/*.{html,txt}": "html+django"}


plugin = DjangoPlugin()
```

`src/commit_anatomy/plugins/wxt.py`:

```python
"""WXT browser extensions: detected by a wxt dependency in package.json."""

import json
import posixpath

from ..model import Rule, Scope
from .base import RepoView, rule

RULES = [
    rule("wxt:e2e", "tests/e2e", "tests/e2e/**"),
    rule("wxt:integration", "tests/integration", "tests/contract/**", "tests/integration/**"),
    rule(
        "wxt:test-support",
        "tests/support",
        "tests/fake-*/**",
        "tests/**/fixtures/**",
        "tests/helpers/**",
    ),
    rule("wxt:tests", "tests/unit", "tests/**"),
    rule("wxt:colocated-tests", "tests/unit", "**/*.{test,spec}.{ts,tsx,js,jsx,mts}"),
    rule(
        "wxt:deps",
        "supporting/deps",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "package-lock.json",
        "yarn.lock",
        ".npmrc",
        ".nvmrc",
    ),
    rule("wxt:translations", "supporting/translations", "**/locales/**", "**/*.po"),
    rule(
        "wxt:build",
        "supporting/build",
        "*.config.{ts,js,mjs,cjs}",
        "tsconfig*.json",
        "eslint.config.*",
        ".prettierrc*",
        ".prettierignore",
    ),
    rule("wxt:generated", "supporting/generated", ".wxt/**", ".output/**"),
    rule("wxt:app", "code/app", "src/**", "assets/**/*.{css,ts}", "public/**/*.{css,js,html}"),
]


def _depends_on_wxt(content: bytes) -> bool:
    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return any(
        isinstance(data.get(key), dict) and "wxt" in data[key]
        for key in ("dependencies", "devDependencies")
    )


class WxtPlugin:
    name = "wxt"

    def detect(self, repo: RepoView) -> list[Scope]:
        roots = [
            posixpath.dirname(path)
            for path in repo.files()
            if posixpath.basename(path) == "package.json"
            and "node_modules/" not in path
            and _depends_on_wxt(repo.read(path) or b"")
        ]
        return [Scope(self.name, root) for root in dict.fromkeys(roots)]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {}


plugin = WxtPlugin()
```

`src/commit_anatomy/plugins/xcode.py`:

```python
"""Xcode / Swift Package Manager projects: detected by *.xcodeproj or Package.swift."""

from ..model import Rule, Scope
from .base import RepoView, rule

RULES = [
    rule("xcode:ui-tests", "tests/e2e", "**/*UITests/**"),
    rule("xcode:tests", "tests/unit", "**/Tests/**", "**/*Tests/**"),
    rule("xcode:test-plans", "tests/support", "**/*.xctestplan"),
    rule(
        "xcode:translations", "supporting/translations", "**/*.{xcstrings,strings,stringsdict}"
    ),
    rule(
        "xcode:build",
        "supporting/build",
        "**/*.xcodeproj/**",
        "**/*.xcworkspace/**",
        "**/*.xcconfig",
        "**/*.entitlements",
        "**/Info.plist",
        "Config/**",
        ".swiftlint.yml",
        ".swift-format",
    ),
    rule(
        "xcode:deps",
        "supporting/deps",
        "**/Package.swift",
        "**/Package.resolved",
        "Brewfile",
        "Brewfile.lock.json",
        "Gemfile",
        "Gemfile.lock",
        "Podfile",
        "Podfile.lock",
    ),
    rule("xcode:assets", "supporting/assets", "**/*.xcassets/**"),
    rule("xcode:app", "code/app", "**/*.swift", "**/*.{m,mm,h}", "Support/**"),
]


class XcodePlugin:
    name = "xcode"

    def detect(self, repo: RepoView) -> list[Scope]:
        roots = []
        for path in repo.files():
            parts = path.split("/")
            for i, part in enumerate(parts[:-1]):
                if part.endswith(".xcodeproj"):
                    roots.append("/".join(parts[:i]))
                    break
            if parts[-1] == "Package.swift":
                roots.append("/".join(parts[:-1]))
        return [Scope(self.name, root) for root in dict.fromkeys(roots)]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {}


plugin = XcodePlugin()
```

- [ ] **Step 5: Run to see pass**

Run: `uv run pytest tests/golden -q`
Expected: `9 passed`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/commit_anatomy/plugins tests/golden
git commit -m "feat: Django, WXT and Xcode plugins"
```

---

### Task 10: Facts cache

**Files:**
- Create: `src/commit_anatomy/cache.py`
- Test: `tests/unit/test_cache.py`

**Interfaces:**
- Consumes: `model.CommitFacts`, `model.Commit`, `model.FileChange`, `lines.rle_encode`, `lines.rle_decode`, `git.DIFF_OPTIONS`.
- Produces:
  - `cache.FACTS_KEY` (`"facts-v1;" + DIFF_OPTIONS`), `cache.cache_home() -> Path`, `cache.facts_to_json(facts) -> str`, `cache.facts_from_json(text) -> CommitFacts`.
  - `cache.MemoryCache()` and `cache.SqliteCache(path: Path)`, both with
    `get_labels(blob, alias, version) -> list[Label] | None`, `put_labels(blob, alias, version, labels)`,
    `get_head(blob) -> list[str] | None`, `put_head(blob, lines)`,
    `get_commit(sha, key) -> CommitFacts | None`, `put_commit(sha, key, facts)`, `flush()`, `close()`.
  - `SqliteCache.info() -> dict[str, int]` (keys `blob_labels`, `blob_heads`, `commit_facts`, `bytes`), `SqliteCache.clear(*, blobs: bool, commits: bool)`.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_cache.py`:

```python
from datetime import UTC, datetime

import pytest

from commit_anatomy.cache import (
    FACTS_KEY,
    MemoryCache,
    SqliteCache,
    cache_home,
    facts_from_json,
    facts_to_json,
)
from commit_anatomy.model import Commit, CommitFacts, FileChange

WHEN = datetime(2026, 1, 5, 12, tzinfo=UTC)
FACTS = CommitFacts(
    Commit("c" * 40, ("p" * 40,), "Ana", "ana@example.com", WHEN, WHEN, "feat: x\n\nbody"),
    (
        FileChange("a.py", "a.py", "M", False, None, "1" * 40, "2" * 40, ((2, 1),), ((2, 3),)),
        FileChange(None, "b.bin", "A", True, None, None, "3" * 40, (), ()),
        FileChange("old.md", "new.md", "R", False, 100, "4" * 40, "4" * 40, (), ()),
    ),
)


def test_facts_json_roundtrip():
    assert facts_from_json(facts_to_json(FACTS)) == FACTS


def test_cache_home_honours_environment(isolated_env):
    assert cache_home() == isolated_env / "cache"


@pytest.fixture(params=["memory", "sqlite"])
def cache(request, tmp_path):
    c = MemoryCache() if request.param == "memory" else SqliteCache(tmp_path / "c.sqlite")
    yield c
    c.close()


def test_labels_heads_and_commits_roundtrip(cache):
    assert cache.get_labels("b1", "python", "v1") is None
    cache.put_labels("b1", "python", "v1", ["code", "code", "blank"])
    assert cache.get_labels("b1", "python", "v1") == ["code", "code", "blank"]
    assert cache.get_labels("b1", "python", "v2") is None  # version is part of the key
    cache.put_head("b1", ["line 1", "line 2"])
    assert cache.get_head("b1") == ["line 1", "line 2"]
    assert cache.get_commit(FACTS.commit.sha, FACTS_KEY) is None
    cache.put_commit(FACTS.commit.sha, FACTS_KEY, FACTS)
    assert cache.get_commit(FACTS.commit.sha, FACTS_KEY) == FACTS
    assert cache.get_commit(FACTS.commit.sha, "facts-v0;other") is None


def test_sqlite_persists_and_clears(tmp_path):
    path = tmp_path / "c.sqlite"
    first = SqliteCache(path)
    first.put_labels("b1", "python", "v1", ["code"])
    first.put_commit(FACTS.commit.sha, FACTS_KEY, FACTS)
    first.close()
    second = SqliteCache(path)
    assert second.get_labels("b1", "python", "v1") == ["code"]
    info = second.info()
    assert (info["blob_labels"], info["commit_facts"], info["blob_heads"]) == (1, 1, 0)
    assert info["bytes"] > 0
    second.clear(blobs=True, commits=False)
    assert second.get_labels("b1", "python", "v1") is None
    assert second.get_commit(FACTS.commit.sha, FACTS_KEY) == FACTS
    second.clear(blobs=False, commits=True)
    assert second.get_commit(FACTS.commit.sha, FACTS_KEY) is None
    second.close()
```

- [ ] **Step 2: Run to see failure**

Run: `uv run pytest tests/unit/test_cache.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.cache'`.

- [ ] **Step 3: Implement**

`src/commit_anatomy/cache.py`:

```python
"""Facts cache: line labels and heads per blob, facts per commit. Never conclusions."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .git import DIFF_OPTIONS
from .lines import rle_decode, rle_encode
from .model import Commit, CommitFacts, FileChange, Label

FACTS_SCHEMA = 1
FACTS_KEY = f"facts-v{FACTS_SCHEMA};{DIFF_OPTIONS}"


def cache_home() -> Path:
    if override := os.environ.get("COMMIT_ANATOMY_CACHE_DIR"):
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "commit-anatomy"


def facts_to_json(facts: CommitFacts) -> str:
    c = facts.commit
    return json.dumps(
        {
            "commit": {
                "sha": c.sha,
                "parents": list(c.parents),
                "author_name": c.author_name,
                "author_email": c.author_email,
                "author_date": c.author_date.isoformat(),
                "committer_date": c.committer_date.isoformat(),
                "message": c.message,
            },
            "changes": [asdict(ch) for ch in facts.changes],
        },
        ensure_ascii=False,
    )


def facts_from_json(text: str) -> CommitFacts:
    data = json.loads(text)
    c = data["commit"]
    commit = Commit(
        sha=c["sha"],
        parents=tuple(c["parents"]),
        author_name=c["author_name"],
        author_email=c["author_email"],
        author_date=datetime.fromisoformat(c["author_date"]),
        committer_date=datetime.fromisoformat(c["committer_date"]),
        message=c["message"],
    )
    changes = tuple(
        FileChange(
            **{
                **ch,
                "removed_lines": tuple(tuple(r) for r in ch["removed_lines"]),
                "added_lines": tuple(tuple(r) for r in ch["added_lines"]),
            }
        )
        for ch in data["changes"]
    )
    return CommitFacts(commit, changes)


class MemoryCache:
    """Used for --no-cache: nothing outlives the run."""

    def __init__(self):
        self._labels: dict[tuple[str, str, str], list[Label]] = {}
        self._heads: dict[str, list[str]] = {}
        self._commits: dict[tuple[str, str], CommitFacts] = {}

    def get_labels(self, blob: str, alias: str, version: str) -> list[Label] | None:
        return self._labels.get((blob, alias, version))

    def put_labels(self, blob: str, alias: str, version: str, labels: list[Label]) -> None:
        self._labels[(blob, alias, version)] = list(labels)

    def get_head(self, blob: str) -> list[str] | None:
        return self._heads.get(blob)

    def put_head(self, blob: str, lines: list[str]) -> None:
        self._heads[blob] = list(lines)

    def get_commit(self, sha: str, key: str) -> CommitFacts | None:
        return self._commits.get((sha, key))

    def put_commit(self, sha: str, key: str, facts: CommitFacts) -> None:
        self._commits[(sha, key)] = facts

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS blob_labels (
    blob TEXT, alias TEXT, version TEXT, labels TEXT NOT NULL,
    PRIMARY KEY (blob, alias, version));
CREATE TABLE IF NOT EXISTS blob_heads (blob TEXT PRIMARY KEY, head TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commit_facts (
    sha TEXT, key TEXT, facts TEXT NOT NULL, PRIMARY KEY (sha, key));
"""


class SqliteCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)

    def _one(self, sql: str, args: tuple) -> str | None:
        row = self._db.execute(sql, args).fetchone()
        return row[0] if row else None

    def get_labels(self, blob: str, alias: str, version: str) -> list[Label] | None:
        text = self._one(
            "SELECT labels FROM blob_labels WHERE blob = ? AND alias = ? AND version = ?",
            (blob, alias, version),
        )
        return rle_decode(json.loads(text)) if text is not None else None

    def put_labels(self, blob: str, alias: str, version: str, labels: list[Label]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO blob_labels VALUES (?, ?, ?, ?)",
            (blob, alias, version, json.dumps(rle_encode(labels))),
        )

    def get_head(self, blob: str) -> list[str] | None:
        text = self._one("SELECT head FROM blob_heads WHERE blob = ?", (blob,))
        return json.loads(text) if text is not None else None

    def put_head(self, blob: str, lines: list[str]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO blob_heads VALUES (?, ?)",
            (blob, json.dumps(lines, ensure_ascii=False)),
        )

    def get_commit(self, sha: str, key: str) -> CommitFacts | None:
        text = self._one("SELECT facts FROM commit_facts WHERE sha = ? AND key = ?", (sha, key))
        return facts_from_json(text) if text is not None else None

    def put_commit(self, sha: str, key: str, facts: CommitFacts) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO commit_facts VALUES (?, ?, ?)",
            (sha, key, facts_to_json(facts)),
        )

    def info(self) -> dict[str, int]:
        self.flush()
        counts = {
            table: self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("blob_labels", "blob_heads", "commit_facts")
        }
        return counts | {"bytes": self.path.stat().st_size}

    def clear(self, *, blobs: bool, commits: bool) -> None:
        if blobs:
            self._db.execute("DELETE FROM blob_labels")
            self._db.execute("DELETE FROM blob_heads")
        if commits:
            self._db.execute("DELETE FROM commit_facts")
        self._db.commit()
        self._db.execute("VACUUM")

    def flush(self) -> None:
        self._db.commit()

    def close(self) -> None:
        self._db.commit()
        self._db.close()
```

- [ ] **Step 4: Run to see pass**

Run: `uv run pytest tests/unit/test_cache.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/commit_anatomy/cache.py tests/unit/test_cache.py
git commit -m "feat: SQLite facts cache for blob labels, heads and commit facts"
```

---

### Task 11: Analyzer and tidy rows

**Files:**
- Create: `src/commit_anatomy/analysis.py`, `src/commit_anatomy/rows.py`
- Modify: `tests/helpers.py` (add `APP`)
- Test: `tests/integration/test_analysis.py`

**Interfaces:**
- Consumes: `GitRepo.commits/diff/read_blob/is_ancestor`, the cache interface from Task 10 (`FACTS_KEY`), `EffectiveConfig`, `order_rules`, `Classifier`, `Windows`, `classify_intent`, `LineLabeler`, `lines.blank_labels/count_ranges/decode/split_lines`, `plugins.base.language_hint`.
- Produces:
  - `analysis.HEAD_LINES = 20`.
  - `analysis.SideResult(match: Match, counts: Counter[str])`, `analysis.FileResult(change: FileChange, old: SideResult | None, new: SideResult | None, flags: frozenset[str])`, `analysis.CommitResult(commit: Commit, intent: str, files: tuple[FileResult, ...])`.
  - `analysis.Analyzer(repo, cache, config, active, labeler)` with attribute `classifier` and methods `facts(shas) -> list[CommitFacts]`, `analyze(shas) -> list[CommitResult]` (same order as `shas`).
  - `rows.Row` (fields `sha, date, author, intent, subject, path, old_path, status, category, subcategory, rule_id, rule_origin, label, added, deleted, flags: tuple[str, ...]`), `rows.FIELDS: list[str]`, `rows.rows_from(results) -> list[Row]`.
  - Flags: `binary`, `rename`, `moved` (rename at 100 % similarity), `reclassified`, `generated`, `unlexed`.
  - The analyzer only calls `commits`, `diff`, `read_blob` and `is_ancestor` on its `repo` argument.

- [ ] **Step 1: Add a shared sample file to the test helpers**

Append to `tests/helpers.py`:

```python
# docstring, code, blank, comment, code
APP = '"""Doc."""\nimport os\n\n# note\nx = 1\n'
```

- [ ] **Step 2: Write failing tests**

`tests/integration/test_analysis.py`:

```python
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
    repo.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub.path), "vendor/sm")
    rows = {r.path: r for r in rows_from(analyze(repo, repo.commit("chore: add submodule")))}
    sm = rows["vendor/sm"]
    assert (sm.category, sm.subcategory, sm.rule_id, sm.added) == ("supporting", "deps", "core:submodule", 0)


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
```

- [ ] **Step 3: Run to see failure**

Run: `uv run pytest tests/integration/test_analysis.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.analysis'`.

- [ ] **Step 4: Implement the analyzer**

`src/commit_anatomy/analysis.py`:

```python
"""From cached facts to classified, line-counted file sides. Nothing derived is cached."""

from __future__ import annotations

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
        if self.config.taxonomy.split_lines(category):
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
        return labels

    def _head(self, blob: str) -> list[str]:
        head = self.cache.get_head(blob)
        if head is None:
            content = self.repo.read_blob(blob)
            head = split_lines(decode(content))[:HEAD_LINES] if content is not None else []
            self.cache.put_head(blob, head)
        return head
```

- [ ] **Step 5: Implement the rows**

`src/commit_anatomy/rows.py`:

```python
"""Tidy rows: one per (commit, file, category side, line label). The canonical output."""

from __future__ import annotations

from dataclasses import dataclass, fields

from .analysis import CommitResult
from .model import LABELS, Match


@dataclass(frozen=True)
class Row:
    sha: str
    date: str
    author: str
    intent: str
    subject: str
    path: str
    old_path: str
    status: str
    category: str
    subcategory: str
    rule_id: str
    rule_origin: str
    label: str
    added: int
    deleted: int
    flags: tuple[str, ...]


FIELDS = [f.name for f in fields(Row)]


def rows_from(results: list[CommitResult]) -> list[Row]:
    rows: list[Row] = []
    for result in results:
        commit = result.commit
        for f in result.files:
            ch = f.change
            base = {
                "sha": commit.sha,
                "date": commit.author_date.isoformat(),
                "author": commit.author_name,
                "intent": result.intent,
                "subject": commit.subject,
                "path": ch.path,
                "old_path": ch.old_path if ch.old_path and ch.old_path != ch.path else "",
                "status": ch.status,
                "flags": tuple(sorted(f.flags)),
            }
            # Added lines come from the new side, deleted lines from the old side.
            cells: dict[tuple[Match, str], list[int]] = {}
            for side, index in ((f.new, 0), (f.old, 1)):
                if side is None:
                    continue
                for label, count in side.counts.items():
                    if count:
                        cells.setdefault((side.match, label), [0, 0])[index] += count
            if not cells:  # moves, binaries, mode changes, empty files: one zero row
                side = f.new or f.old
                assert side is not None
                cells[(side.match, "raw")] = [0, 0]
            ordered = sorted(
                cells.items(), key=lambda kv: (kv[0][0].category, LABELS.index(kv[0][1]))
            )
            for (match, label), (added, deleted) in ordered:
                top, _, sub = match.category.partition("/")
                rows.append(
                    Row(
                        **base,
                        category=top,
                        subcategory=sub,
                        rule_id=match.rule_id,
                        rule_origin=match.origin,
                        label=label,
                        added=added,
                        deleted=deleted,
                    )
                )
    return rows
```

- [ ] **Step 6: Run to see pass**

Run: `uv run pytest tests/integration/test_analysis.py -q`
Expected: `8 passed`.

- [ ] **Step 7: Commit**

```bash
git add src/commit_anatomy/analysis.py src/commit_anatomy/rows.py tests/helpers.py tests/integration/test_analysis.py
git commit -m "feat: analyze commits into classified, line-counted rows"
```

---

### Task 12: Aggregation

**Files:**
- Create: `src/commit_anatomy/aggregate.py`, `tests/result_helpers.py`
- Test: `tests/unit/test_aggregate.py`

**Interfaces:**
- Consumes: `analysis.CommitResult/FileResult/SideResult`, `rows.rows_from`, `rows.Row`.
- Produces:
  - `aggregate.CODE_LABELS = ("code", "unlexed")`, `aggregate.COMMENT_LABELS = ("comment", "docstring")`.
  - `aggregate.Counts(added: Counter, deleted: Counter)` with `add(row)` and `value(metric="churn", basis="non_blank", labels: tuple[str, ...] | None = None) -> int` (metric `churn|added|deleted`, basis `non_blank|total`; `labels` sums only those labels and ignores the basis).
  - `aggregate.Group(key, title="", intent="", categories: dict[str, Counts], moved=0)` with `total(metric, basis) -> int`.
  - `aggregate.aggregate(results, by: str, level: str) -> list[Group]` (`by`: `commit|intent|week|month|total`; `level`: `top|sub`; commit groups keep input order and include commits without rows; other groups sort by key; weeks are ISO `2026-W02`, months `2026-01`).
  - `aggregate.merge_groups(groups, key="total") -> Group`.
  - `tests/result_helpers.py`: `commit(sha, day, subject)`, `file(path, category, added=None, deleted=None, flags=())`, and the fixtures `R1`, `R2`, `EMPTY` used by later report tests.

- [ ] **Step 1: Add synthetic results for report tests**

`tests/result_helpers.py`:

```python
"""Hand-built analysis results for aggregation and report tests."""

from collections import Counter
from datetime import UTC, datetime

from commit_anatomy.analysis import CommitResult, FileResult, SideResult
from commit_anatomy.model import Commit, FileChange, Match


def commit(sha: str, day: int, subject: str = "feat: x") -> Commit:
    when = datetime(2026, 1, day, 12, tzinfo=UTC)
    return Commit(sha, (), "T", "t@example.com", when, when, subject)


def file(path, category, added=None, deleted=None, flags=()) -> FileResult:
    match = Match(category, "r", "o", "")
    change = FileChange(path, path, "M", False, None, "a", "b", (), ())
    return FileResult(
        change,
        SideResult(match, Counter(deleted or {})),
        SideResult(match, Counter(added or {})),
        frozenset(flags),
    )


# 2026-01-05 is in ISO week 2, 2026-01-13 in week 3.
R1 = CommitResult(
    commit("c1", 5),
    "feat",
    (
        file("src/a.py", "code/app", {"code": 6, "comment": 2, "blank": 2}, {"code": 1}),
        file("README.md", "docs/dev", {"raw": 3, "blank": 1}),
    ),
)
R2 = CommitResult(commit("c2", 13, "fix: y"), "fix", (file("tests/t.py", "tests/unit", {"code": 4}),))
EMPTY = CommitResult(commit("c3", 14, "chore: nothing"), "chore", ())
```

- [ ] **Step 2: Write failing tests**

`tests/unit/test_aggregate.py`:

```python
from collections import Counter

from result_helpers import EMPTY, R1, R2, commit, file

from commit_anatomy.aggregate import Counts, aggregate, merge_groups
from commit_anatomy.analysis import CommitResult


def test_counts_values():
    c = Counts(Counter({"code": 6, "comment": 2, "blank": 2}), Counter({"code": 1}))
    assert c.value("added", "non_blank") == 8
    assert c.value("added", "total") == 10
    assert c.value("churn", "non_blank") == 9
    assert c.value("deleted", "total") == 1
    assert c.value("churn", "non_blank", ("code", "unlexed")) == 7
    assert c.value("added", "non_blank", ("comment", "docstring")) == 2


def test_by_commit_keeps_every_commit_in_order_including_empty_ones():
    groups = aggregate([R1, R2, EMPTY], "commit", "top")
    assert [(g.key, g.intent, g.title) for g in groups] == [
        ("c1", "feat", "feat: x"),
        ("c2", "fix", "fix: y"),
        ("c3", "chore", "chore: nothing"),
    ]
    assert set(groups[0].categories) == {"code", "docs"}
    assert groups[2].categories == {}
    assert groups[0].total("churn", "non_blank") == 12


def test_sub_level_and_intent_grouping():
    groups = {g.key: g for g in aggregate([R1, R2], "intent", "sub")}
    assert set(groups) == {"feat", "fix"}
    assert set(groups["feat"].categories) == {"code/app", "docs/dev"}


def test_week_month_and_total_buckets():
    assert [g.key for g in aggregate([R1, R2], "week", "top")] == ["2026-W02", "2026-W03"]
    assert [g.key for g in aggregate([R1, R2], "month", "top")] == ["2026-01"]
    (total,) = aggregate([R1, R2], "total", "top")
    assert total.categories["tests"].value("added", "non_blank") == 4


def test_moved_files_are_counted_once():
    moved = CommitResult(
        commit("c4", 15), "refactor", (file("b.txt", "unclassified", flags=("moved", "rename")),)
    )
    assert aggregate([moved], "commit", "top")[0].moved == 1


def test_merge_groups():
    total = merge_groups(aggregate([R1, R2], "commit", "top"))
    assert total.key == "total"
    assert total.categories["code"].value("added", "total") == 10
    assert total.total("churn", "non_blank") == 16
```

- [ ] **Step 3: Run to see failure**

Run: `uv run pytest tests/unit/test_aggregate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.aggregate'`.

- [ ] **Step 4: Implement**

`src/commit_anatomy/aggregate.py`:

```python
"""Group results by commit, intent, period or in total, per category."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .analysis import CommitResult
from .rows import Row, rows_from

CODE_LABELS = ("code", "unlexed")
COMMENT_LABELS = ("comment", "docstring")


@dataclass
class Counts:
    added: Counter = field(default_factory=Counter)
    deleted: Counter = field(default_factory=Counter)

    def add(self, row: Row) -> None:
        self.added[row.label] += row.added
        self.deleted[row.label] += row.deleted

    def value(
        self,
        metric: str = "churn",
        basis: str = "non_blank",
        labels: tuple[str, ...] | None = None,
    ) -> int:
        def pick(counter: Counter) -> int:
            if labels is not None:
                return sum(counter[label] for label in labels)
            total = sum(counter.values())
            return total - counter["blank"] if basis == "non_blank" else total

        added, deleted = pick(self.added), pick(self.deleted)
        return {"churn": added + deleted, "added": added, "deleted": deleted}[metric]


@dataclass
class Group:
    key: str
    title: str = ""
    intent: str = ""
    categories: dict[str, Counts] = field(default_factory=dict)
    moved: int = 0

    def total(self, metric: str, basis: str) -> int:
        return sum(c.value(metric, basis) for c in self.categories.values())


def _bucket(row: Row, by: str) -> str:
    if by == "commit":
        return row.sha
    if by == "intent":
        return row.intent
    if by == "total":
        return "total"
    when = datetime.fromisoformat(row.date)
    if by == "week":
        year, week, _ = when.isocalendar()
        return f"{year}-W{week:02d}"
    if by == "month":
        return f"{when.year}-{when.month:02d}"
    raise ValueError(f"unknown grouping {by!r}")


def aggregate(results: list[CommitResult], by: str, level: str) -> list[Group]:
    groups: dict[str, Group] = {}
    if by == "commit":
        for r in results:
            groups[r.commit.sha] = Group(r.commit.sha, r.commit.subject, r.intent)
    seen_moves: set[tuple[str, str, str]] = set()
    for row in rows_from(results):
        key = _bucket(row, by)
        group = groups.setdefault(key, Group(key))
        category = row.category if level == "top" or not row.subcategory else (
            f"{row.category}/{row.subcategory}"
        )
        group.categories.setdefault(category, Counts()).add(row)
        if "moved" in row.flags and (key, row.sha, row.path) not in seen_moves:
            seen_moves.add((key, row.sha, row.path))
            group.moved += 1
    if by == "commit":
        return list(groups.values())
    return sorted(groups.values(), key=lambda g: g.key)


def merge_groups(groups: list[Group], key: str = "total") -> Group:
    merged = Group(key)
    for group in groups:
        merged.moved += group.moved
        for category, counts in group.categories.items():
            target = merged.categories.setdefault(category, Counts())
            target.added.update(counts.added)
            target.deleted.update(counts.deleted)
    return merged
```

- [ ] **Step 5: Run to see pass**

Run: `uv run pytest tests/unit/test_aggregate.py -q`
Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/commit_anatomy/aggregate.py tests/result_helpers.py tests/unit/test_aggregate.py
git commit -m "feat: aggregate results by commit, intent, week, month or in total"
```

---

### Task 13: Table, JSON/CSV and explain reports

**Files:**
- Create: `src/commit_anatomy/report/__init__.py` (empty), `src/commit_anatomy/report/table.py`, `src/commit_anatomy/report/tabular.py`, `src/commit_anatomy/report/explain.py`
- Test: `tests/unit/test_reports.py`

**Interfaces:**
- Consumes: `aggregate.Group/merge_groups/CODE_LABELS/COMMENT_LABELS`, `taxonomy.Taxonomy/UNCLASSIFIED`, `rows.Row/FIELDS`, `analysis.CommitResult`, `classify.GLOB_FLAGS`, `model.LABELS`.
- Produces:
  - `report.table.render_table(groups, taxonomy, *, metric, basis, by) -> rich.table.Table` (caption `basis: … · metric: … · moved files: N · unclassified: M lines`, yellow when M > 0; a `total` row when there is more than one group).
  - `report.tabular.to_json(rows) -> str` (list of objects, `flags` as a list), `report.tabular.to_csv(rows) -> str` (header = `FIELDS`, `flags` joined with `|`).
  - `report.explain.render_explain(result, path_glob: str | None = None) -> rich.table.Table`.
  - All user-supplied text goes into Rich as `Text`, never as markup (commit subjects contain `[` and `]`).

- [ ] **Step 1: Write failing tests**

`tests/unit/test_reports.py`:

```python
import csv
import io
import json
from collections import Counter

from result_helpers import R1, R2, commit, file
from rich.console import Console

from commit_anatomy.aggregate import aggregate
from commit_anatomy.analysis import CommitResult, FileResult, SideResult
from commit_anatomy.model import FileChange, Match
from commit_anatomy.report.explain import render_explain
from commit_anatomy.report.table import render_table
from commit_anatomy.report.tabular import to_csv, to_json
from commit_anatomy.rows import FIELDS, rows_from
from commit_anatomy.taxonomy import Taxonomy


def text_of(renderable) -> str:
    console = Console(record=True, width=200)
    console.print(renderable)
    return console.export_text()


def table_for(results, by="commit"):
    return text_of(
        render_table(
            aggregate(results, by, "top"), Taxonomy(), metric="churn", basis="non_blank", by=by
        )
    )


def test_table_shows_counts_shares_and_code_split():
    out = table_for([R1, R2])
    assert "c1" in out and "[feat]" in out and "feat: x" in out  # brackets survive: no markup
    assert "+8 −1" in out and "75%" in out  # code is 9 of the commit's 12 lines
    assert "code 7 · comments 2" in out
    assert "total" in out
    assert "unclassified: 0 lines" in out
    header = next(line for line in out.splitlines() if "docs" in line and "tests" in line)
    assert header.index("docs") < header.index("tests") < header.index("code")


def test_table_reports_unclassified_lines():
    r = CommitResult(commit("c9", 6), "unknown", (file("x.bin", "unclassified", {"raw": 5}),))
    assert "unclassified: 5 lines" in table_for([r])


def test_json_and_csv_rows():
    rows = rows_from([R1])
    data = json.loads(to_json(rows))
    assert {d["path"] for d in data} == {"src/a.py", "README.md"}
    assert set(data[0]) == set(FIELDS)
    assert isinstance(data[0]["flags"], list)
    parsed = list(csv.DictReader(io.StringIO(to_csv(rows))))
    assert len(parsed) == len(rows) and list(parsed[0]) == FIELDS
    assert to_json([]) == "[]"


def test_explain_shows_rule_origin_pattern_counts_and_flags():
    old = Match("docs/dev", "core:docs", "plugin:core", "**/*.{md,rst}")
    new = Match("docs/user", "django:user-docs", "plugin:django", "docs/manual/**")
    change = FileChange(
        "README.md", "docs/manual/README.md", "R", False, 75, "a", "b", ((4, 1),), ((4, 1),)
    )
    result = CommitResult(
        commit("c5", 7, "docs: move [manual]"),
        "docs",
        (
            FileResult(
                change,
                SideResult(old, Counter({"raw": 1})),
                SideResult(new, Counter({"raw": 1})),
                frozenset({"rename", "reclassified"}),
            ),
        ),
    )
    out = text_of(render_explain(result))
    for fragment in (
        "README.md → docs/manual/README.md",
        "docs/dev",
        "docs/user",
        "core:docs",
        "plugin:django",
        "docs/manual/**",
        "−raw 1",
        "+raw 1",
        "reclassified",
        "[manual]",
    ):
        assert fragment in out, fragment
    assert "core:docs" not in text_of(render_explain(result, "src/**"))
```

- [ ] **Step 2: Run to see failure**

Run: `uv run pytest tests/unit/test_reports.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.report'`.

- [ ] **Step 3: Implement the reports**

Create empty `src/commit_anatomy/report/__init__.py`.

`src/commit_anatomy/report/table.py`:

```python
"""Terminal table: one row per group, one column per category."""

from __future__ import annotations

from rich import box
from rich.table import Table
from rich.text import Text

from ..aggregate import CODE_LABELS, COMMENT_LABELS, Group, merge_groups
from ..taxonomy import UNCLASSIFIED, Taxonomy


def _title(group: Group, by: str) -> Text:
    if by == "commit":
        return Text(f"{group.key[:8]} [{group.intent}]\n{group.title[:60]}")
    return Text(group.key)


def _cell(group: Group, category: str, taxonomy: Taxonomy, metric: str, basis: str) -> Text:
    counts = group.categories.get(category)
    if counts is None:
        return Text("")
    total = group.total(metric, basis)
    share = counts.value(metric, basis) / total if total else 0.0
    added, deleted = counts.value("added", basis), counts.value("deleted", basis)
    head = {"churn": f"+{added} −{deleted}", "added": f"+{added}", "deleted": f"−{deleted}"}
    text = f"{head[metric]}  {share:.0%}"
    if taxonomy.split_lines(category):
        code = counts.value(metric, basis, CODE_LABELS)
        comments = counts.value(metric, basis, COMMENT_LABELS)
        text += f"\ncode {code} · comments {comments}"
    return Text(text)


def render_table(
    groups: list[Group], taxonomy: Taxonomy, *, metric: str, basis: str, by: str
) -> Table:
    total = merge_groups(groups)
    categories = sorted(total.categories, key=taxonomy.order_key)
    table = Table(box=box.SIMPLE_HEAD)
    table.add_column(by)
    for category in categories:
        table.add_column(category, justify="right")
    for group in groups:
        table.add_row(
            _title(group, by), *(_cell(group, c, taxonomy, metric, basis) for c in categories)
        )
    if len(groups) > 1:
        table.add_section()
        table.add_row(
            Text("total"), *(_cell(total, c, taxonomy, metric, basis) for c in categories)
        )
    unclassified = sum(
        counts.value(metric, basis)
        for category, counts in total.categories.items()
        if category.partition("/")[0] == UNCLASSIFIED
    )
    table.caption = Text(
        f"basis: {basis} · metric: {metric} · moved files: {total.moved}"
        f" · unclassified: {unclassified} lines"
    )
    table.caption_style = "yellow" if unclassified else "dim"
    return table
```

`src/commit_anatomy/report/tabular.py`:

```python
"""Machine-readable output: the tidy rows as JSON or CSV."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict

from ..rows import FIELDS, Row


def to_json(rows: list[Row]) -> str:
    if not rows:
        return "[]"
    return json.dumps(
        [asdict(r) | {"flags": list(r.flags)} for r in rows], indent=2, ensure_ascii=False
    )


def to_csv(rows: list[Row]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(asdict(r) | {"flags": "|".join(r.flags)})
    return out.getvalue()
```

`src/commit_anatomy/report/explain.py`:

```python
"""Per-file explanation of a commit: which rule placed each side, and why."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from wcmatch import glob

from ..analysis import CommitResult, SideResult
from ..classify import GLOB_FLAGS
from ..model import LABELS


def _lines(side: SideResult, sign: str) -> str:
    parts = [f"{sign}{label} {side.counts[label]}" for label in LABELS if side.counts[label]]
    return " ".join(parts) or "0"


def render_explain(result: CommitResult, path_glob: str | None = None) -> Table:
    commit = result.commit
    table = Table(title=Text(f"{commit.sha[:12]} [{result.intent}] {commit.subject}"))
    for column in ("file", "status", "side", "category", "rule", "origin", "matched", "lines"):
        table.add_column(column)
    table.add_column("flags")
    for f in result.files:
        ch = f.change
        paths = [p for p in (ch.old_path, ch.new_path) if p]
        if path_glob and not any(glob.globmatch(p, path_glob, flags=GLOB_FLAGS) for p in paths):
            continue
        if ch.old_path and ch.new_path and ch.old_path != ch.new_path:
            name = f"{ch.old_path} → {ch.new_path}"
        else:
            name = ch.path
        first = True
        for side_name, side, sign in (("old", f.old, "−"), ("new", f.new, "+")):
            if side is None:
                continue
            m = side.match
            table.add_row(
                Text(name if first else ""),
                Text(ch.status if first else ""),
                Text(side_name),
                Text(m.category),
                Text(m.rule_id),
                Text(m.origin),
                Text(m.matched),
                Text(_lines(side, sign)),
                Text(", ".join(sorted(f.flags)) if first else ""),
            )
            first = False
    return table
```

- [ ] **Step 4: Run to see pass**

Run: `uv run pytest tests/unit/test_reports.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/commit_anatomy/report tests/unit/test_reports.py
git commit -m "feat: table, JSON/CSV and explain reports"
```

---

### Task 14: Command line (analyze, explain, detect, rules, cache)

**Files:**
- Create: `src/commit_anatomy/cli.py`
- Modify: `tests/helpers.py` (add `seed_project`)
- Test: `tests/integration/test_cli.py`

**Interfaces:**
- Consumes: everything above. Typer's `CliRunner` relies on Click ≥ 8.2 (pulled in by Typer), which keeps `result.stdout` and `result.stderr` apart.
- Produces: `cli.app` (Typer app), `cli.main()` (console script), `cli._session(path, no_cache)` context manager returning `Session(repo, active, config, analyzer)`, `cli._handle_errors` decorator (ConfigError → exit 2; GitError and PluginError → exit 1; message on stderr as `error: …`), `cli._stderr()`. Commands: `analyze`, `explain`, `detect`, `rules`, `cache info`, `cache clear`. Tasks 15 and 16 add `trend` and `init` to this file.

- [ ] **Step 1: Add a seeded two-commit project to the helpers**

Append to `tests/helpers.py`:

```python
def seed_project(repo: "ScriptedRepo") -> "ScriptedRepo":
    """A small Django-shaped history: one feature commit, then one fix."""
    repo.commit("feat: start", {"manage.py": "", "src/app.py": APP, "README.md": "# T\n\nText\n"})
    repo.commit("fix: tweak", {"src/app.py": APP + "y = 2\n"})
    return repo
```

- [ ] **Step 2: Write failing tests**

`tests/integration/test_cli.py`:

```python
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
    run("analyze", "-C", project, "--no-cache")
    assert not cache_file.exists()
    run("analyze", "-C", project)
    assert cache_file.exists()
    info = run("cache", "info")
    assert info.exit_code == 0 and "commit_facts" in info.stdout
    assert run("cache", "clear").exit_code == 0
    assert run("cache", "clear", "--blobs").exit_code == 0
```

- [ ] **Step 3: Run to see failure**

Run: `uv run pytest tests/integration/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.cli'`.

- [ ] **Step 4: Implement**

`src/commit_anatomy/cli.py` (no `from __future__ import annotations` here: Typer reads the annotations at runtime):

```python
"""commit-anatomy command line."""

import functools
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .aggregate import aggregate
from .analysis import Analyzer
from .cache import MemoryCache, SqliteCache, cache_home
from .config import ConfigError, EffectiveConfig, load_config
from .git import GitError, GitRepo
from .lexers.pygments_backend import PygmentsLabeler
from .model import Scope
from .plugins.base import GitRepoView, Plugin, PluginError, active_scopes, load_plugins
from .report.explain import render_explain
from .report.table import render_table
from .report.tabular import to_csv, to_json
from .rows import rows_from

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Break commits down into docs, tests, code and supporting changes.",
)
cache_app = typer.Typer(no_args_is_help=True, help="Inspect or clear the facts cache.")
app.add_typer(cache_app, name="cache")


class By(StrEnum):
    commit = "commit"
    intent = "intent"
    week = "week"
    month = "month"
    total = "total"


class Level(StrEnum):
    top = "top"
    sub = "sub"


class Metric(StrEnum):
    churn = "churn"
    added = "added"
    deleted = "deleted"


class Basis(StrEnum):
    non_blank = "non_blank"
    total = "total"


class Format(StrEnum):
    table = "table"
    json = "json"
    csv = "csv"


RepoOption = Annotated[Path, typer.Option("--repo", "-C", help="Repository to analyse.")]
Revspecs = Annotated[
    list[str] | None,
    typer.Argument(help="Commits or ranges (default: HEAD).", show_default=False),
]
NoCache = Annotated[bool, typer.Option("--no-cache", help="Neither read nor write the cache.")]
Since = Annotated[str | None, typer.Option(help="Only commits after this date (walks history).")]
Until = Annotated[str | None, typer.Option(help="Only commits before this date (walks history).")]
Merges = Annotated[bool, typer.Option("--merges", help="Include merge commits when walking.")]
MetricOption = Annotated[Metric, typer.Option(help="Count added lines, deleted lines or both.")]
BasisOption = Annotated[Basis, typer.Option(help="Leave blank lines out, or count all lines.")]


def _stderr() -> Console:
    return Console(stderr=True, highlight=False)


def _handle_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ConfigError as e:
            _stderr().print(Text(f"error: {e}", style="red"))
            raise typer.Exit(2) from None
        except (GitError, PluginError) as e:
            _stderr().print(Text(f"error: {e}", style="red"))
            raise typer.Exit(1) from None

    return wrapper


@dataclass
class Session:
    repo: GitRepo
    active: list[tuple[Plugin, Scope]]
    config: EffectiveConfig
    analyzer: Analyzer


@contextmanager
def _session(path: Path, no_cache: bool) -> Iterator[Session]:
    repo = GitRepo(path)
    cache = MemoryCache() if no_cache else SqliteCache(cache_home() / "cache.sqlite")
    try:
        if repo.is_shallow():
            _stderr().print(Text("warning: shallow clone, history is truncated", style="yellow"))
        active = active_scopes(load_plugins(), GitRepoView(repo))
        config = load_config(repo.root, repo.remote_url(), repo.resolve_commit)
        analyzer = Analyzer(repo, cache, config, active, PygmentsLabeler())
        yield Session(repo, active, config, analyzer)
    finally:
        cache.close()
        repo.close()


@app.command()
@_handle_errors
def analyze(
    revspec: Revspecs = None,
    repo: RepoOption = Path("."),
    since: Since = None,
    until: Until = None,
    by: Annotated[By, typer.Option(help="How to group commits.")] = By.commit,
    level: Annotated[Level, typer.Option(help="Top categories or subcategories.")] = Level.top,
    metric: MetricOption = Metric.churn,
    basis: BasisOption = Basis.non_blank,
    output_format: Annotated[
        Format, typer.Option("--format", "-f", help="Output format.")
    ] = Format.table,
    history: Annotated[
        bool, typer.Option("--history", help="Walk the ancestry of each REVSPEC.")
    ] = False,
    merges: Merges = False,
    no_cache: NoCache = False,
) -> None:
    """Report how each commit's changed lines split across categories."""
    with _session(repo, no_cache) as s:
        shas = s.repo.list_commits(
            revspec or ["HEAD"], history=history, since=since, until=until, merges=merges
        )
        if not shas:
            _stderr().print("No commits matched.")
        results = s.analyzer.analyze(shas)
    if output_format is Format.json:
        sys.stdout.write(to_json(rows_from(results)) + "\n")
    elif output_format is Format.csv:
        sys.stdout.write(to_csv(rows_from(results)))
    elif results:
        groups = aggregate(results, by, level)
        Console().print(
            render_table(groups, s.config.taxonomy, metric=metric, basis=basis, by=by)
        )


@app.command()
@_handle_errors
def explain(
    commit: Annotated[str, typer.Argument(help="The commit to explain.")],
    repo: RepoOption = Path("."),
    path: Annotated[str | None, typer.Option(help="Only files matching this glob.")] = None,
    no_cache: NoCache = False,
) -> None:
    """Show which rule classified each file of a commit, and why."""
    with _session(repo, no_cache) as s:
        results = s.analyzer.analyze(s.repo.list_commits([commit]))
    console = Console()
    for result in results:
        console.print(render_explain(result, path))


@app.command()
@_handle_errors
def detect(repo: RepoOption = Path(".")) -> None:
    """List the active plugins, their scopes and the config files found."""
    with _session(repo, no_cache=True) as s:
        table = Table("plugin", "scope")
        for plugin, scope in s.active:
            table.add_row(Text(plugin.name), Text(scope.root or "/"))
        files = ", ".join(str(f) for f in s.config.files) or "none"
    console = Console()
    console.print(table)
    console.print(Text(f"Config files: {files}"))


@app.command()
@_handle_errors
def rules(repo: RepoOption = Path(".")) -> None:
    """Print the effective rules in the order they are tried."""
    with _session(repo, no_cache=True) as s:
        table = Table("#", "stage", "origin", "id", "category", "paths", "content", "window")
        for index, entry in enumerate(s.analyzer.classifier.entries, 1):
            r = entry.rule
            window = f"{r.since or ''}..{r.until or ''}" if r.since or r.until else ""
            table.add_row(
                *(
                    Text(value)
                    for value in (
                        str(index),
                        r.stage,
                        entry.origin,
                        r.id,
                        r.category,
                        ", ".join(r.paths),
                        r.content or "",
                        window,
                    )
                )
            )
    Console().print(table)


@cache_app.command("info")
@_handle_errors
def cache_info() -> None:
    """Show where the cache is and what it holds."""
    cache = SqliteCache(cache_home() / "cache.sqlite")
    try:
        info = cache.info()
    finally:
        cache.close()
    table = Table("entry", "count")
    for key, value in info.items():
        table.add_row(key, str(value))
    console = Console()
    console.print(Text(str(cache.path)))
    console.print(table)


@cache_app.command("clear")
@_handle_errors
def cache_clear(
    blobs: Annotated[bool, typer.Option("--blobs", help="Only blob labels and heads.")] = False,
    commits: Annotated[bool, typer.Option("--commits", help="Only commit facts.")] = False,
) -> None:
    """Delete cached entries (both kinds unless one is chosen)."""
    both = not blobs and not commits
    cache = SqliteCache(cache_home() / "cache.sqlite")
    try:
        cache.clear(blobs=blobs or both, commits=commits or both)
    finally:
        cache.close()
    _stderr().print("Cache cleared.")


def main() -> None:
    app()
```

- [ ] **Step 5: Run to see pass, then try it by hand**

Run: `uv run pytest tests/integration/test_cli.py -q`
Expected: `9 passed`.

Run: `uv run commit-anatomy analyze --history --by total --level sub`
Expected: a table of this repository's own history (docs-heavy), with no traceback.

- [ ] **Step 6: Commit**

```bash
git add src/commit_anatomy/cli.py tests/helpers.py tests/integration/test_cli.py
git commit -m "feat: command line with analyze, explain, detect, rules and cache"
```

---

### Task 15: Trend report

**Files:**
- Create: `src/commit_anatomy/report/trend.py`
- Modify: `src/commit_anatomy/cli.py` (add `trend`)
- Test: `tests/unit/test_trend.py`, `tests/integration/test_cli.py` (append)

**Interfaces:**
- Consumes: `aggregate.Group`, `taxonomy.Taxonomy`, `cli._session`, `cli._handle_errors`, `aggregate.aggregate`.
- Produces: `report.trend.render_trend(groups, taxonomy, *, metric, basis, title) -> str` (self-contained HTML: no scripts, no external assets; two inline SVG charts — share per bucket and absolute lines per bucket; each bar segment has a `<title>` `"{bucket} · {category}: {n} lines ({share})"`; light and dark colour schemes). CLI: `commit-anatomy trend [REVSPEC...] -C REPO --bucket week|month -o FILE.html` (always walks history).

Before writing the chart code, load the `dataviz` skill and check the palette below against it (categorical, distinguishable in both themes); adjust the hex values if the skill's validator rejects them.

- [ ] **Step 1: Write failing tests**

`tests/unit/test_trend.py`:

```python
from result_helpers import R1, R2

from commit_anatomy.aggregate import aggregate
from commit_anatomy.report.trend import render_trend
from commit_anatomy.taxonomy import Taxonomy


def html_for(results, title="repo: change composition per week"):
    groups = aggregate(results, "week", "top")
    return render_trend(groups, Taxonomy(), metric="churn", basis="non_blank", title=title)


def test_trend_page():
    html = html_for([R1, R2])
    assert html.startswith("<!doctype html>")
    assert "<title>repo: change composition per week</title>" in html
    assert html.count("<svg") == 2
    assert "2026-W02" in html and "2026-W03" in html
    assert "2026-W02 · code: 9 lines (75%)" in html
    assert "prefers-color-scheme: dark" in html
    assert "<script" not in html and "http" not in html


def test_trend_escapes_text():
    assert "&lt;x&gt;" in html_for([R1], title="<x>")


def test_trend_without_data():
    assert "No commits" in html_for([])
```

Append to `tests/integration/test_cli.py`:

```python
def test_trend_command_writes_html(project, tmp_path):
    out = tmp_path / "trend.html"
    r = run("trend", "-C", project, "--bucket", "month", "-o", str(out))
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert html.count("<svg") == 2 and "2026-01" in html
```

- [ ] **Step 2: Run to see failure**

Run: `uv run pytest tests/unit/test_trend.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.report.trend'`.

- [ ] **Step 3: Implement the renderer**

`src/commit_anatomy/report/trend.py`:

```python
"""Self-contained HTML trend: stacked bars per week or month, drawn as inline SVG."""

from __future__ import annotations

from html import escape

from ..aggregate import Group
from ..taxonomy import Taxonomy

PALETTE = {
    "docs": "#4C78A8",
    "tests": "#54A24B",
    "code": "#F58518",
    "supporting": "#B279A2",
    "unclassified": "#9D9D9D",
}
EXTRA = ("#E45756", "#72B7B2", "#EECA3B", "#FF9DA6")

BAR, GAP, HEIGHT, LEFT, BOTTOM, TOP = 28, 10, 220, 56, 70, 12

STYLE = """
:root { --bg: #ffffff; --fg: #1f2328; --muted: #656d76; --grid: #d0d7de; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --grid: #30363d; }
}
body { background: var(--bg); color: var(--fg); margin: 24px 16px;
       font: 14px/1.4 system-ui, -apple-system, sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 24px 0 8px; }
.sub { color: var(--muted); margin: 0 0 16px; }
.chart { overflow-x: auto; }
.grid { stroke: var(--grid); }
.tick { fill: var(--muted); font-size: 11px; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 16px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 12px; height: 12px; border-radius: 2px; display: inline-block; }
"""


def _chart(groups, categories, colors, metric, basis, share: bool, label: str) -> str:
    totals = [g.total(metric, basis) for g in groups]
    scale = 1 if share else max([*totals, 1])
    width = LEFT + len(groups) * (BAR + GAP) + 16
    height = TOP + HEIGHT + BOTTOM
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="{escape(label)}">'
    ]
    for fraction in (0, 0.5, 1):
        y = TOP + HEIGHT * (1 - fraction)
        tick = f"{fraction:.0%}" if share else str(round(scale * fraction))
        parts.append(
            f'<line class="grid" x1="{LEFT}" x2="{width - 8}" y1="{y}" y2="{y}"/>'
            f'<text class="tick" x="{LEFT - 6}" y="{y + 4}" text-anchor="end">{tick}</text>'
        )
    for i, group in enumerate(groups):
        x = LEFT + i * (BAR + GAP) + GAP / 2
        total = totals[i]
        y = TOP + HEIGHT
        for category in categories:
            counts = group.categories.get(category)
            value = counts.value(metric, basis) if counts else 0
            if not value or not total:
                continue
            h = HEIGHT * (value / total if share else value / scale)
            y -= h
            parts.append(
                f'<rect x="{x}" y="{y:.1f}" width="{BAR}" height="{h:.1f}" '
                f'fill="{colors[category]}"><title>{escape(group.key)} · {escape(category)}: '
                f"{value} lines ({value / total:.0%})</title></rect>"
            )
        parts.append(
            f'<text class="tick" transform="translate({x + BAR / 2},{TOP + HEIGHT + 12}) '
            f'rotate(45)">{escape(group.key)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_trend(
    groups: list[Group], taxonomy: Taxonomy, *, metric: str, basis: str, title: str
) -> str:
    categories = [t for t in taxonomy.tops if any(t in g.categories for g in groups)]
    colors = {
        c: PALETTE.get(c, EXTRA[i % len(EXTRA)]) for i, c in enumerate(categories)
    }
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head><body>"
        f"<h1>{escape(title)}</h1>"
        f'<p class="sub">metric: {escape(metric)} · basis: {escape(basis)}</p>'
    )
    if not groups:
        return head + "<p>No commits in this range.</p></body></html>"
    legend = "".join(
        f'<span><i style="background:{colors[c]}"></i>{escape(c)}</span>' for c in categories
    )
    share = _chart(groups, categories, colors, metric, basis, True, "Share of changed lines")
    size = _chart(groups, categories, colors, metric, basis, False, "Changed lines")
    return (
        head
        + f'<div class="legend">{legend}</div>'
        + f'<h2>Share of changed lines</h2><div class="chart">{share}</div>'
        + f'<h2>Changed lines</h2><div class="chart">{size}</div>'
        + "</body></html>"
    )
```

- [ ] **Step 4: Add the `trend` command**

In `src/commit_anatomy/cli.py` add the import `from .report.trend import render_trend`, then add after `explain`:

```python
class Bucket(StrEnum):
    week = "week"
    month = "month"


@app.command()
@_handle_errors
def trend(
    revspec: Revspecs = None,
    repo: RepoOption = Path("."),
    bucket: Annotated[Bucket, typer.Option(help="Bar per week or per month.")] = Bucket.week,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="HTML file to write.")
    ] = Path("commit-anatomy-trend.html"),
    since: Since = None,
    until: Until = None,
    metric: MetricOption = Metric.churn,
    basis: BasisOption = Basis.non_blank,
    merges: Merges = False,
    no_cache: NoCache = False,
) -> None:
    """Write an HTML chart of how the change mix evolves over time."""
    with _session(repo, no_cache) as s:
        shas = s.repo.list_commits(
            revspec or ["HEAD"], history=True, since=since, until=until, merges=merges
        )
        results = s.analyzer.analyze(shas)
        title = f"{s.repo.root.name}: change composition per {bucket}"
        html = render_trend(
            aggregate(results, bucket, "top"),
            s.config.taxonomy,
            metric=metric,
            basis=basis,
            title=title,
        )
    output.write_text(html, encoding="utf-8")
    _stderr().print(Text(f"Wrote {output}"))
```

- [ ] **Step 5: Run to see pass**

Run: `uv run pytest tests/unit/test_trend.py tests/integration/test_cli.py -q`
Expected: `13 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/commit_anatomy/report/trend.py src/commit_anatomy/cli.py tests/unit/test_trend.py tests/integration/test_cli.py
git commit -m "feat: HTML trend report per week or month"
```

---

### Task 16: `init` — starter config from introspection

**Files:**
- Create: `src/commit_anatomy/init.py`
- Modify: `src/commit_anatomy/cli.py` (add `init`)
- Test: `tests/integration/test_init.py`

**Interfaces:**
- Consumes: `analysis.CommitResult`, `taxonomy.Taxonomy/UNCLASSIFIED`, `config.config_home/normalize_remote/REPO_FILE/ConfigError`, `cli._session/_handle_errors/_stderr`.
- Produces:
  - `init.SPLIT_SHARE = 0.10`, `init.Suggestion(paths: tuple[str, ...], category: str, why: str)`.
  - `init.guess_category(directory: str, filenames: list[str], taxonomy) -> tuple[str, str]` (category, reason).
  - `init.suggest_unclassified(results, taxonomy) -> list[Suggestion]` — one per directory (`dir/**`), sorted by directory. When a directory also holds classified files (at any depth), it lists the unclassified files instead, so the suggestion cannot override existing classifications.
  - `init.suggest_splits(results, taxonomy, share=SPLIT_SHARE) -> list[Suggestion]` — for a rule matched through a `prefix/**` glob, one suggestion per child directory, when at least two children each carry ≥ `share` of that rule's non-blank churn.
  - `init.render_config(*, unclassified, splits, active, commits: int, match: dict | None, today: date) -> str` — valid TOML; split suggestions first, then unclassified.
  - CLI: `commit-anatomy init [REVSPEC...] -C REPO [--user|--repo] [--force] [--no-cache]`.

- [ ] **Step 1: Write failing tests**

`tests/integration/test_init.py`:

```python
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
    assert [(s.paths, s.category) for s in splits] == [
        (("docs/dev/**",), "docs/dev"),
        (("docs/manual/**",), "docs/user"),
    ]
    assert "core:docs" in splits[1].why and "57%" in splits[1].why


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
```

The `57%` is docs/manual's share of `core:docs` churn through `docs/**`: 4 of 7 lines.

- [ ] **Step 2: Run to see failure**

Run: `uv run pytest tests/integration/test_init.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'commit_anatomy.init'`.

- [ ] **Step 3: Implement the suggestions**

`src/commit_anatomy/init.py`:

```python
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
        for group, category in ((CODE_EXT, "code/app"), (DOC_EXT, "docs/dev"), (DATA_EXT, "supporting/build")):
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


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def render_config(
    *,
    unclassified: list[Suggestion],
    splits: list[Suggestion],
    active,
    commits: int,
    match: dict | None,
    today: date,
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
```

- [ ] **Step 4: Add the `init` command**

In `src/commit_anatomy/cli.py` add the imports

```python
from datetime import date

from .config import REPO_FILE, config_home, normalize_remote
from .init import render_config, suggest_splits, suggest_unclassified
```

(merge `REPO_FILE, config_home, normalize_remote` into the existing `from .config import …` line) and add:

```python
@app.command()
@_handle_errors
def init(
    revspec: Revspecs = None,
    repo: RepoOption = Path("."),
    user: Annotated[
        bool,
        typer.Option(
            "--user/--repo",
            help="Write your per-repo user config (default) or the in-repo .commit-anatomy.toml.",
        ),
    ] = True,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
    no_cache: NoCache = False,
) -> None:
    """Write a starter config with suggested rules, from the repository's history."""
    with _session(repo, no_cache) as s:
        root = s.repo.root
        if user:
            target = config_home() / "repos" / f"{root.name}.toml"
            remote = s.repo.remote_url()
            match = {"remote": normalize_remote(remote)} if remote else {"path": str(root)}
        else:
            target, match = root / REPO_FILE, None
        if target.exists() and not force:
            raise ConfigError(target, "init", "file exists; use --force to overwrite")
        shas = s.repo.list_commits(revspec or ["HEAD"], history=True)
        results = s.analyzer.analyze(shas)
        text = render_config(
            unclassified=suggest_unclassified(results, s.config.taxonomy),
            splits=suggest_splits(results, s.config.taxonomy),
            active=s.active,
            commits=len(shas),
            match=match,
            today=date.today(),
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _stderr().print(Text(f"Wrote {target}"))
```

- [ ] **Step 5: Run to see pass, then the whole suite**

Run: `uv run pytest tests/integration/test_init.py -q`
Expected: `6 passed`.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all tests pass, no lint findings.

- [ ] **Step 6: Commit**

```bash
git add src/commit_anatomy/init.py src/commit_anatomy/cli.py tests/integration/test_init.py
git commit -m "feat: init writes a starter config with suggested rules"
```

---

### Task 17: Acceptance on the hrcek repositories, rule tuning and README

**Files:**
- Modify: `src/commit_anatomy/plugins/*.py` and their golden tests (only as the findings below require), `README.md`

**Interfaces:**
- Consumes: the installed CLI.
- Produces: built-in rules that leave ≤ 1 % of non-blank churn `unclassified` on the full history of `$HRCEK_DIR/hrcek`, `$HRCEK_DIR/hrcek-extension` and `$HRCEK_DIR/hrcek-ios`, with **no** config files (spec success criterion), and a README with usage.

`$HRCEK_DIR` is the directory holding the three hrcek checkouts; ask the user for it if it is not set (`export HRCEK_DIR=…`). This task only reads the hrcek repositories. It never writes to them and never adds a `.commit-anatomy.toml` to them.

- [ ] **Step 1: Check detection**

Run:

```bash
for r in hrcek hrcek-extension hrcek-ios; do uv run commit-anatomy detect -C $HRCEK_DIR/$r; done
```

Expected: `hrcek` → `django (/)`; `hrcek-extension` → `wxt (/)`; `hrcek-ios` → `xcode (/)` and `xcode (HrcekKit)` (plus `core (/)` everywhere). If not, fix the detection (golden test first), then re-run.

- [ ] **Step 2: Measure the unclassified share and list the worst paths**

Run:

```bash
for r in hrcek hrcek-extension hrcek-ios; do
  uv run commit-anatomy analyze -C $HRCEK_DIR/$r --history -f csv | uv run python -c '
import collections, csv, sys
total, unclassified, paths = 0, 0, collections.Counter()
for row in csv.DictReader(sys.stdin):
    n = 0 if row["label"] == "blank" else int(row["added"]) + int(row["deleted"])
    total += n
    if row["category"] == "unclassified":
        unclassified += n
        paths[row["path"]] += n
print(f"{sys.argv[1]}: {unclassified}/{total} non-blank lines unclassified = {unclassified / max(total, 1):.2%}")
for path, n in paths.most_common(15):
    print(f"   {n:6d}  {path}")
' "$r"
done
```

Expected: one summary line per repository, each followed by its worst unclassified paths.

- [ ] **Step 3: Tune the rules until every repository is at ≤ 1 %**

For each unclassified path pattern that matters (more than 0.1 % of that repository's churn):

1. Add a case to the matching plugin's golden test (`tests/golden/test_<plugin>_plugin.py`; generic files go in `test_core_plugin.py`) and run `uv run pytest tests/golden -q` to see it fail.
2. Add the narrowest glob to that plugin's `RULES`, in the right position (more specific rules first).
3. Re-run the golden tests (pass) and Step 2.

Generic files belong in `core` (e.g. `.gitattributes`, `.gitmodules`, `.editorconfig`); ecosystem files belong in their plugin. Do not add catch-all globs such as `**` to reach the target.

- [ ] **Step 4: Spot-check classifications**

For each repository, run `explain` on its three most recent commits:

```bash
for r in hrcek hrcek-extension hrcek-ios; do
  for sha in $(git -C $HRCEK_DIR/$r rev-list -3 HEAD); do
    uv run commit-anatomy explain $sha -C $HRCEK_DIR/$r
  done
done
```

Check by eye: `CLAUDE.md` / `AGENTS.md` / `docs/superpowers/**` → `docs/llm`; Django migrations → `supporting/migrations`; `.po` / `.xcstrings` → `supporting/translations`; colocated `*.test.ts` → `tests/unit`; Swift under `HrcekKit/Tests` → `tests/unit`. Fix any misclassification the same way as Step 3.

- [ ] **Step 5: Check that the cache pays off**

Run twice and compare the wall-clock times:

```bash
time uv run commit-anatomy analyze -C $HRCEK_DIR/hrcek-extension --history --by total
time uv run commit-anatomy analyze -C $HRCEK_DIR/hrcek-extension --history --by total
```

Expected: the second run is several times faster than the first. Write both timings into the task report.

- [ ] **Step 6: Write the README**

Replace `README.md` with:

````markdown
# commit-anatomy

Break commits down into documentation, tests, code (with and without comments) and
supporting changes (dependencies, migrations, translations, build, CI…), and tag each
commit's intent (feat, fix, refactor…). Built to see *what* pull requests grow with.

## Install

```bash
uv tool install --editable .
```

## Use

```bash
commit-anatomy analyze                          # the HEAD commit
commit-anatomy analyze main..feature            # a range
commit-anatomy analyze --history --by month     # whole history, per month
commit-anatomy analyze --history --by intent --level sub
commit-anatomy analyze --history -f csv > rows.csv   # tidy rows for pandas / spreadsheets
commit-anatomy explain HEAD                     # which rule placed each file, and why
commit-anatomy trend --bucket month -o trend.html
commit-anatomy detect                           # active plugins and config files
commit-anatomy rules                            # effective rules in precedence order
commit-anatomy init                             # starter config with suggested rules
commit-anatomy cache info | cache clear
```

All commands take `-C <repo>`. Counts use non-blank lines by default (`--basis total`
to include blank lines) and added + deleted lines (`--metric added|deleted`).

## Configure

Configuration is optional. Rules live in your per-repo user config
(`~/.config/commit-anatomy/repos/<name>.toml`, which needs a `match = { path = "…" }` or
`match = { remote = "…" }`) or in `.commit-anatomy.toml` at the repository root. The
user file wins.

```toml
[[rules]]
paths = ["docs/manual/**"]
category = "docs/user"

[[rules]]
paths = ["docs/**"]
category = "docs/user"
until = "2026-03-01"          # the path meant something else before this date

[taxonomy.docs]
subcategories = ["adr"]

[taxonomy.translations]       # promote translations to a top-level category
[taxonomy.remap]
"supporting/translations" = "translations"

[intent.keywords]
feat = ["add*", "keep", "offer*"]

[intent.overrides]
"a1b2c3d" = "fix"
```

See `docs/specs/2026-10-15-commit-anatomy-design.md` for the full design.

## Plugins

Built in: `core`, `django`, `wxt`, `xcode`. A plugin is any object with `name`,
`detect(repo)`, `rules()` and `languages()`, registered under the entry-point group
`commit_anatomy.plugins`.
````

- [ ] **Step 7: Run everything and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all tests pass, no lint findings.

```bash
git add README.md src tests
git commit -m "feat: tune built-in rules on real repositories; document usage"
```

Report the final unclassified share per repository and the timings from Step 5.
