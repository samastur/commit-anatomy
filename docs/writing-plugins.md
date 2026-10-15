# Writing a plugin

A plugin teaches commit-anatomy about one kind of project: a framework, a build system, a
platform. The built-in plugins are `core` (applies everywhere), `django`, `wxt` (browser
extensions) and `xcode`. This guide explains what a plugin can do, what it must implement,
and how to test and ship it. Read the "Rules, precedence and first match wins" and "Scopes"
sections of `docs/architecture.md` first.

## What a plugin can do

A plugin can:

- **detect** where in a repository its kind of project lives, possibly in several places
  (a monorepo);
- contribute **rules** that classify paths into categories (`tests/unit`, `supporting/deps`…),
  by glob, by content of the file's first lines, or both;
- give **language hints**: tell the line labeller which Pygments lexer to use for files it
  would not recognise by name (for example Django templates are `html+django`, not `html`).

A plugin cannot:

- add categories to the taxonomy. Use the built-in categories (see `taxonomy.py`). New
  categories can only come from user or repository config;
- override config rules. User and repository config always win;
- change line counting, intent or reports.

## The protocol

A plugin is any object with these four members (`plugins/base.py`, `Plugin`):

```python
class Plugin(Protocol):
    name: str
    def detect(self, repo: RepoView) -> list[Scope]: ...
    def rules(self) -> list[Rule]: ...
    def languages(self) -> dict[str, str]: ...
```

### `name`

A short, unique identifier. It appears in rule origins (`plugin:<name>@<root>`) and in
`commit-anatomy detect`. By convention, rule ids are prefixed with it: `django:migrations`.

### `detect(repo) -> list[Scope]`

Called once per run with a read-only view of the tree at `HEAD`:

```python
class RepoView(Protocol):
    def files(self) -> list[str]: ...          # every path in HEAD, repo-relative
    def read(self, path: str) -> bytes | None: ...  # content of a file in HEAD
```

Return one `Scope(self.name, root)` per directory where your project kind lives. `root` is
repository-relative, `""` for the repository root, never with a trailing slash. Return `[]`
when the plugin does not apply.

Guidelines:

- Detect by a **marker file** that is unambiguous: `manage.py`, a `package.json` that
  depends on `wxt`, a `*.xcodeproj` directory, `Package.swift`.
- `files()` can be long (tens of thousands of paths). Filter by name first and `read()` only
  the candidates; each read goes through git.
- Skip vendored directories (`node_modules/`, `vendor/`) when they can contain the marker.
- Deduplicate roots: `dict.fromkeys(roots)` keeps order.
- Treat unparseable marker files as "not this kind of project" rather than raising. Any
  exception raised from `detect` stops the run with `error: plugin '<name>' failed to detect`
  and exit code 1.

Detection looks only at `HEAD`. The scopes it returns are used for every commit in the
analysed history.

### `rules() -> list[Rule]`

The rules for one scope. Build them with the `rule()` helper:

```python
from commit_anatomy.plugins.base import rule

rule("rails:migrations", "supporting/migrations", "db/migrate/**", "db/schema.rb")
rule("rails:generated", "supporting/generated", content=r"^# This file is auto-generated", stage="pre")
```

`rule(id, category, *paths, content=None, content_lines=5, stage="normal")`:

| argument | meaning |
|---|---|
| `id` | unique id shown by `explain` and `rules`; use `<plugin>:<what>` |
| `category` | a valid `top/sub` category from the default taxonomy |
| `paths` | globs, matched against the path **relative to the scope root** |
| `content` | a regex searched (`re.search`) in the first `content_lines` lines of the file |
| `content_lines` | how many lines of the file `content` sees, at most 20 |
| `stage` | `"pre"` to run before every scope's normal rules, else `"normal"` |

A rule matches a file side when one of its `paths` matches (if it has any) **and** its
`content` matches (if it has one). A rule with only `content` applies to every file in the
scope. Content rules never match binary files.

**Glob syntax** is [wcmatch](https://facelessuser.github.io/wcmatch/glob/) with `GLOBSTAR`,
`BRACE` and `DOTGLOB`:

- `**` crosses directories, `*` does not: `tests/*.py` matches `tests/a.py` but not
  `tests/unit/a.py`;
- `**/name` matches `name` at any depth, including the scope root;
- braces expand: `**/*.{test,spec}.{ts,tsx}`;
- dotfiles are matched like any other file;
- matching is case-sensitive.

**Order matters.** Rules are tried in the order `rules()` returns them, and the first match
wins. List specific rules before general ones:

```python
RULES = [
    rule("wxt:e2e", "tests/e2e", "tests/e2e/**"),
    rule("wxt:test-support", "tests/support", "tests/**/fixtures/**"),
    rule("wxt:tests", "tests/unit", "tests/**"),  # after the more specific tests/ rules
    ...
]
```

**Use `stage="pre"` sparingly.** A `pre` rule beats the normal rules of *every* plugin and
scope, not only yours. It is meant for facts about the file itself, such as "this file is
generated", that should win regardless of where the file lives.

**Where your rules sit among others.** Scoped plugins are tried deepest root first, then
alphabetically by plugin entry-point name, and `core` last. So you don't need to restate
core's rules (README, CHANGELOG, `.github/`, images…), and a nested project's rules beat the
outer project's. Config rules from the user always come before yours.

Return the same list on every call; define it once at module level.

### `languages() -> dict[str, str]`

Maps globs (relative to the scope root) to a Pygments lexer alias. Only needed when the file
name alone picks the wrong lexer, or none:

```python
def languages(self) -> dict[str, str]:
    return {"**/templates/**/*.{html,txt}": "html+django"}
```

Hints only matter for categories that split lines into code and comments (`tests` and
`code` by default). An unknown alias falls back to the file name. Return `{}` if you have no
hints. List aliases with `uv run python -c "from pygments.lexers import get_all_lexers;
print([l[1] for l in get_all_lexers()])"`.

## A complete example

`src/commit_anatomy_rails/__init__.py` in your own package:

```python
"""Ruby on Rails applications: detected by config/application.rb."""

import posixpath

from commit_anatomy.model import Rule, Scope
from commit_anatomy.plugins.base import RepoView, rule

MARKER = "config/application.rb"

RULES = [
    rule("rails:migrations", "supporting/migrations", "db/migrate/**", "db/schema.rb"),
    rule("rails:translations", "supporting/translations", "config/locales/**"),
    rule("rails:deps", "supporting/deps", "Gemfile", "Gemfile.lock", ".ruby-version"),
    rule("rails:system-tests", "tests/e2e", "test/system/**", "spec/system/**"),
    rule("rails:test-support", "tests/support", "test/fixtures/**", "spec/support/**",
         "spec/factories/**", "test/test_helper.rb", "spec/*_helper.rb"),
    rule("rails:tests", "tests/unit", "test/**", "spec/**"),
    rule("rails:tooling", "code/tooling", "bin/**", "lib/tasks/**"),
    rule("rails:app", "code/app", "app/**", "config/**", "lib/**", "config.ru", "Rakefile"),
]


class RailsPlugin:
    name = "rails"

    def detect(self, repo: RepoView) -> list[Scope]:
        roots = [
            posixpath.dirname(posixpath.dirname(path))
            for path in repo.files()
            if path == MARKER or path.endswith("/" + MARKER)
        ]
        return [Scope(self.name, root) for root in dict.fromkeys(roots)]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {"app/views/**/*.erb": "erb"}


plugin = RailsPlugin()
```

Note that `config/locales/**` comes before `config/**`, and the test rules before
`app/**`-style catch-alls.

## Registering the plugin

Plugins are found through the Python entry-point group `commit_anatomy.plugins`. The entry
point must name the plugin **object** (an instance), not the class.

In your package's `pyproject.toml`:

```toml
[project.entry-points."commit_anatomy.plugins"]
rails = "commit_anatomy_rails:plugin"
```

Install it in the same environment as commit-anatomy. From a commit-anatomy checkout, as a
tool:

```bash
uv tool install --editable . --with-editable ../commit-anatomy-rails
```

or, into this repository's development environment, `uv pip install -e ../commit-anatomy-rails`
(a later `uv sync` removes it again).

For a plugin that ships **with commit-anatomy**, add the module under
`src/commit_anatomy/plugins/`, add a line to the entry-point table in this repository's
`pyproject.toml`, and run `uv sync`. Entry points are read from the installed package
metadata, so a new entry is invisible until the package is reinstalled.

Plugins are loaded sorted by entry-point name. If one fails to import, the run stops with
`error: plugin '<name>' failed to load` and exit code 1.

## Checking it on a real repository

```bash
commit-anatomy detect -C ~/src/some-rails-app      # is the plugin active, at which roots?
commit-anatomy rules -C ~/src/some-rails-app       # where do your rules sit in the order?
commit-anatomy analyze --history --level sub -C ~/src/some-rails-app
commit-anatomy explain <sha> -C ~/src/some-rails-app --path 'config/**'
```

The footer of `analyze` reports the number of unclassified lines; the aim is to keep that near
zero for the projects the plugin is written for. `explain` shows the rule and glob that placed
each file, which is the fastest way to find an over-broad glob.

No cache clearing is needed when you change rules: classifications are never cached. Changing
`languages()` hints changes the lexer alias, which is part of the label cache key, so new
labels are computed automatically.

## Testing

Copy the pattern of `tests/golden/test_*_plugin.py`: a table of paths and the category each
one should get, run through the real rule ordering together with `core`. In this repository,
use `tests/plugin_helpers.py`:

```python
from plugin_helpers import classify_paths

from commit_anatomy.plugins.core import plugin as core
from commit_anatomy_rails import plugin as rails

EXPECTED = {
    "config/application.rb": "code/app",
    "config/locales/sl.yml": "supporting/translations",
    "db/migrate/20260101_create_entries.rb": "supporting/migrations",
    "test/models/entry_test.rb": "tests/unit",
    "test/fixtures/entries.yml": "tests/support",
    "README.md": "docs/dev",          # from core
}


def test_rails_rules():
    files = {path: "" for path in EXPECTED}
    assert classify_paths([core, rails], files) == EXPECTED
```

`classify_paths` builds an in-memory `StaticRepoView` from the dict (path → content), so it
also exercises `detect`; give marker files real content when detection reads them. For an
external plugin, copy `plugin_helpers.py` (it is about 25 lines) into your test suite.

Worth adding:

- a test that `detect` returns `[]` on an unrelated repository, and does not raise on a
  malformed marker file;
- a monorepo case: the project in a subdirectory, with paths outside it left to other rules
  (see `tests/golden/test_monorepo.py`);
- content rules with both a matching and a near-miss header.
