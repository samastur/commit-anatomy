# Getting started as a developer

This guide gets you from a fresh clone to understanding the codebase well enough to change it.
It suggests an order to read things in and points out what matters, without walking through
every function. The companion documents are:

- `docs/architecture.md`: concepts and how the parts fit together;
- `docs/writing-plugins.md`: adding support for a new kind of project;
- `docs/specs/2026-10-15-commit-anatomy-design.md`: the full design and its reasons.

## 1. Set up (5 minutes)

You need Python 3.12+, git and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/samastur/commit-anatomy.git
cd commit-anatomy
uv sync                          # creates .venv with runtime and dev dependencies
uv run pytest -q                 # the whole suite; takes about 15 seconds
uv run ruff check . && uv run ruff format --check .
```

CI runs the same three commands on every pull request (`.github/workflows/ci.yml`).

## 2. Use the tool before reading it (15 minutes)

Run it on this repository, or any repository you know well:

```bash
uv run commit-anatomy analyze --history --by month     # the change mix per month
uv run commit-anatomy analyze --history --by intent --level sub
uv run commit-anatomy explain HEAD                     # which rule placed each file, and why
uv run commit-anatomy detect                           # active plugins and their scopes
uv run commit-anatomy rules                            # every rule, in the order it is tried
uv run commit-anatomy analyze HEAD~3..HEAD -f csv      # the tidy rows everything is built on
```

`-C <path>` points any of them at another repository. Notice:

- `explain` shows two sides (old and new) for renamed files, and the exact glob or regex that
  matched. Most questions of the form "why is this file counted as X?" are answered here.
- `rules` shows the precedence order: config first, then plugin `pre` rules, then plugin
  rules, with `core` last.
- The second run of `analyze` is much faster than the first: that's the cache.

While experimenting, set `COMMIT_ANATOMY_CACHE_DIR=/tmp/ca-cache` if you want to keep your
real cache out of it, or pass `--no-cache`.

## 3. Read the concepts (20 minutes)

Read `docs/architecture.md`, at least "Core concepts". If you only retain five things:

1. **The cache stores facts, never conclusions.** Diffs, blob heads and line labels are
   cached; categories, intents and counts are recomputed every run.
2. **Each file change has two sides.** Deleted lines are classified by the old path, added
   lines by the new path.
3. **Lines are labelled for the whole blob**, then the diff's line ranges index into the
   labels.
4. **First matching rule wins**, in a fixed precedence order, and plugin rules are scoped
   to the directory where the plugin was detected.
5. **The tidy row is the canonical output.** Tables and charts are aggregations of rows.

## 4. Read the code in this order

Each step builds on the previous one. Sizes are approximate.

| Step | Read | Why | What to skim |
|---|---|---|---|
| 1 | `model.py` (90 lines) | The vocabulary: `Commit`, `FileChange`, `Rule`, `Scope`, `Match`, `Label`. Everything else passes these around. | nothing, read it all |
| 2 | `taxonomy.py` (115) | What a category is; `split_lines`; remap. | `extended()` is config validation |
| 3 | `classify.py` (125) | The rule engine: `order_rules` and `Classifier.classify`. The heart of the tool. | `Windows` until you need time windows |
| 4 | `plugins/base.py`, then `plugins/core.py` and one of `django.py` / `wxt.py` | How rules get into the engine, and what real rules look like. | the other plugins |
| 5 | `analysis.py` (150) | `Analyzer`: facts → sides → classification → line counts. Ties steps 1-4 together. | |
| 6 | `lines.py`, `lexers/pygments_backend.py` | How lines get labels; the `-U0` + whole-blob trick. | `_kind`/`_label` details |
| 7 | `rows.py`, `aggregate.py` | From results to rows, and rows to grouped counts. | |
| 8 | `cli.py` (400) | How a command opens a session and calls everything above. Read `_session`, `analyze` and `_handle_errors`; the other commands follow the same shape. | option declarations |

You can leave these until you need them:

- `git.py`: the git CLI wrapper and diff parser. Treat it as a black box that produces
  `Commit`s and `FileChange`s. Read `parse_diff_tree` only if you touch diff handling.
- `cache.py`: SQLite storage. The important part is the keys (see "Facts versus
  conclusions").
- `config.py`: mostly validation. Read `load_config` for the precedence of config files.
- `init.py`: starter-config suggestions; self-contained.
- `report/`: each reporter is small and independent.
- `intent.py`: a short, self-contained heuristic.

## 5. Read the tests that pin the behaviour

The tests are the most precise description of what the tool does:

- `tests/golden/test_*_plugin.py`: a table of path → category per plugin. Skim one to see
  the expected classification of a real project layout.
- `tests/integration/test_analysis.py`: end-to-end analysis on real throwaway repositories,
  including renames, CRLF, binary files and cache behaviour.
- `tests/integration/test_cli.py`: what each command prints and its exit codes.

Two helpers you'll use when writing tests:

- `ScriptedRepo` (`tests/helpers.py`) creates a git repository in a temporary directory and
  commits files with fixed dates: `repo.commit("feat: x", {"src/a.py": "x = 1\n"})`. The `repo`
  fixture gives you one.
- `classify_paths` (`tests/plugin_helpers.py`) classifies paths with a list of plugins,
  without git.

Every test runs with an isolated environment (`tests/conftest.py`): no global git config, a
temporary user config directory, a temporary cache directory.

## 6. Make a first change

Good first changes, in rising order of reach:

1. **Teach a plugin a new path.** Add a glob to a rule in `plugins/xcode.py`, add the path
   and its expected category to `tests/golden/test_xcode_plugin.py`, run
   `uv run pytest tests/golden -q`.
2. **Add an intent keyword.** Extend `DEFAULT_KEYWORDS` in `intent.py`, add a case to
   `tests/unit/test_intent.py`.
3. **Add a report column or format.** Reporters consume `Group`s or `Row`s; start from
   `report/tabular.py`.

## Conventions

- Python ≥ 3.12, formatted and linted by ruff (`line-length = 100`). Run
  `uv run ruff format . && uv run ruff check --fix .` before committing.
- Runtime dependencies are deliberately few: pygments, wcmatch, typer, rich. Think twice
  before adding one.
- Test first: write a failing test, watch it fail, then make it pass.
- Only `git.py` runs git. Reporters never touch git or the cache.
- Anything derived from config is never cached. If you add a cached fact, put everything it
  depends on in its key.
- If you change how lines are labelled, bump `PygmentsLabeler.version`. If you change what a
  commit's facts contain or how diffs are taken, bump `FACTS_SCHEMA` in `cache.py`.
- User-facing errors are exceptions (`ConfigError`, `GitError`, `PluginError`) caught in
  `cli._handle_errors`; don't print and exit from deep inside the code.
- `docs/specs` and `docs/plans` record decisions as they were made; ruff skips `docs/`.

## Where to look when…

| …you want to know | Look at |
|---|---|
| why a file got its category | `commit-anatomy explain <sha> --path '<glob>'` |
| which rules exist and their order | `commit-anatomy rules`, `classify.order_rules` |
| which plugins are active | `commit-anatomy detect`, each plugin's `detect()` |
| why lines are `unlexed` | `PygmentsLabeler.resolve`/`label`; plugin `languages()` hints |
| what is cached and where | `commit-anatomy cache info`, `cache.py` |
| how a CLI option reaches the code | the command function in `cli.py` |
| the reasoning behind a design choice | the spec, section by module name |
