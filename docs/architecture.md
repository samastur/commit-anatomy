# Architecture

This document explains how commit-anatomy is put together: the ideas it is built on, the
modules that implement them, and how data flows from `git` to a report. It favours the parts
that are not obvious from reading one file at a time. For the full design rationale and the
exact requirements, see the spec (`docs/specs/2026-10-15-commit-anatomy-design.md`); new
developers should start with `docs/getting-started.md`.

## What the tool does, in one paragraph

For each commit, commit-anatomy takes every changed file, decides which **category** each
side of the change belongs to (docs, tests, code, supporting, or unclassified), labels every
changed line (code, comment, docstring, blank…), tags the commit with an **intent** (feat,
fix, refactor…) and then counts. The counts can be shown per commit, per intent, per week or
month, or exported as tidy rows. The point is to see *what* pull requests grow with: did a PR
get bigger because of code, tests, docs or lock files?

## The pipeline

```
           git CLI                      cache (SQLite)
              │                              │
  revspecs ─► GitRepo.list_commits ─► shas   │
              │                              │
              ▼                              │
  Analyzer.facts ── commit facts ◄──────────►│  (sha, FACTS_KEY) → CommitFacts
              │   (metadata + FileChanges)   │
              ▼                              │
  for each FileChange, each side (old/new):  │
     Classifier.classify(path, blob, commit) │  ← rules from config + plugins
         └─ content rules read blob "head" ◄►│  blob → first 20 lines
     line labels for the whole blob     ◄───►│  (blob, alias, labeler version) → labels
     count_ranges(labels, changed ranges)    │
              │                              │
              ▼
  CommitResult(commit, intent, files)        ← intent from the commit message
              │
              ▼
  rows_from()  ─► tidy Rows ─► json / csv
              │
              ▼
  aggregate() ─► Groups ─► table / trend (HTML)
```

`cli.py` wires this together: it opens a `GitRepo`, a cache, detects plugins, loads
config, builds an `Analyzer`, and hands the results to a reporter.

## Core concepts

### Facts versus conclusions (the cache rule)

The most important design decision: **the cache stores facts, never conclusions.**

*Facts* are things that can never change for a given key:

- what a commit changed: `CommitFacts` (author, dates, message, and for each file the paths,
  blob ids, status and the line ranges of each hunk), keyed by commit sha;
- the first 20 lines of a blob (the "head"), keyed by blob id;
- the label of every line of a blob, keyed by blob id, lexer alias and labeller version.

*Conclusions* are everything derived from config and rules: categories, intents, counts. They
are recomputed on every run. Because classification is cheap and the expensive parts
(`git diff-tree`, reading blobs, lexing) are cached, editing a rule and re-running is fast and
can never show stale results. No "invalidate the cache when config changes" logic exists,
because none is needed.

Cache keys carry everything the fact depends on:

- `FACTS_KEY` (`cache.py`) embeds a schema number and the exact `diff-tree` options, so
  changing how diffs are taken or stored makes old entries invisible rather than wrong.
- Label entries are keyed by `labeler.version`, which includes the Pygments version. Bump the
  string in `PygmentsLabeler.version` whenever labelling logic changes.

`MemoryCache` implements the same interface for `--no-cache`. Both are duck-typed; there is
no shared base class.

### Two sides per file change

A `FileChange` has an **old side** (old path, old blob, removed line ranges) and a **new side**
(new path, new blob, added line ranges). Each side is classified on its own:

- deleted lines are counted against the category of the *old* path and blob;
- added lines are counted against the category of the *new* path and blob.

So moving `docs/notes.md` to `tests/fixtures/notes.md` counts its deletions as docs and its
additions as tests, and the file gets the `reclassified` flag. Added files have only a new
side; deleted files only an old one.

Flags on a `FileResult` record things worth knowing: `binary`, `rename`, `moved` (a rename at
100% similarity), `reclassified`, `generated`, `unlexed`.

### Labels are computed for the whole blob, counts for the hunks

Diffs are taken with `-U0`, so a hunk carries only the changed lines, not their context. A
lexer looking at only those lines would get multi-line strings and comments wrong (is this
line inside a docstring?). So the labeller lexes the **entire blob** once, producing one label
per line, and `count_ranges` indexes into that list with the hunk ranges.

This is also what makes labels cacheable: a blob is content-addressed, appears in many
commits, and its labels never change.

Line numbering must match git's exactly; `lines.split_lines` exists for that (no phantom
line after a trailing newline, CRLF kept as one line, a lone CR not treated as a break).

### Line labels

| label | meaning |
|---|---|
| `code` | the line has at least one non-comment token |
| `comment` | only comment tokens |
| `docstring` | only docstring tokens (and no code) |
| `blank` | whitespace only |
| `unlexed` | the category wants line splitting, but no lexer applied (unknown type, file > 1 MB, lexer crashed) |
| `raw` | the category does not split lines, so the line was never lexed |

Only categories whose top level has `split_lines = true` (by default `tests` and `code`) are
lexed. Docs, supporting files and unclassified files only get `blank`/`raw`, which is cheap.
The cache key reflects this: `alias` is the lexer alias, `"none"` when lexing was wanted but
impossible, or `"blank"` when lexing was not wanted.

The `--basis` option decides whether blank lines are counted. In the table, `unlexed` counts
as code (better to over-count code than to drop lines).

### The taxonomy is data

`taxonomy.py` defines categories as two levels, `top/sub` (`tests/unit`, `supporting/deps`).
A top-level category with no subcategories (only `unclassified` by default) is used bare.
The taxonomy is not hard-coded elsewhere: config can add subcategories, add new top-level
categories, toggle `split_lines`, and **remap** a category to another
(`"supporting/translations" = "translations"`).

Remapping happens at match time (`Classifier.classify` returns the remapped category), so
everything downstream sees only the final category.

### Rules, precedence and first match wins

A `Rule` (`model.py`) says: if a path matches one of these globs, and/or the first N lines of
the blob match this regex, and the commit is inside this time window, the side belongs to
this category. Every rule has an `id` and, once ordered, an `origin` that says where it came
from (`user:hrcek.toml`, `.commit-anatomy.toml`, `plugin:django@backend`…).

`order_rules` (`classify.py`) builds one flat list, and `Classifier.classify` returns the
**first** rule that matches. The order is:

1. rules from the user's per-repo config file(s);
2. rules from `.commit-anatomy.toml` in the repository;
3. plugin rules with `stage = "pre"`;
4. plugin rules with `stage = "normal"`;
5. nothing matched: `unclassified`.

Within each plugin stage, scopes are ordered: non-core plugins first, deepest scope root
first, and the `core` plugin last. Within one plugin, rules keep the order the plugin lists
them in, so plugins list specific rules before general ones (`tests/**/fixtures/**` before
`tests/**`).

The `pre` stage exists for rules that must beat path rules, such as core's content rule that
recognises `@generated` / "DO NOT EDIT" headers: a generated `src/api.ts` must be
`supporting/generated` even though `wxt:app` matches `src/**`.

`commit-anatomy rules` prints the final list; `commit-anatomy explain <commit>` shows which
rule matched each side and which glob or regex did it. They are the first tools to reach for
when a classification looks wrong.

### Scopes: plugins are detected per directory

A plugin does not simply apply to a repository: it **detects scopes**. A `Scope` is a plugin
name plus a root directory (`""` for the repository root). A monorepo with a Django backend in
`backend/` and an Xcode app at the root gets two scopes: `django` at `backend` and `xcode` at the root.

A scoped rule only sees paths under its root, and its globs are matched against the path
**relative to the root** (`classify.relative`). So the Django plugin can say `manage.py`
and mean `backend/manage.py`. Config rules have root `""` and see full paths.

Detection runs once per run against the tree at `HEAD` (not per commit). The rules active
at HEAD are used for the whole history. If the past looked different, config rules with
`since`/`until` windows cover it.

### Time windows

A rule may have `since` and/or `until`, each either a date or a commit:

- a date is compared with the commit's committer date (a bare date means midnight UTC);
- a commit means "descends from that commit", checked with `git merge-base --is-ancestor`
  and memoised per run.

`since` is inclusive, `until` exclusive. Windows let one config say "before March, `docs/`
was user docs; afterwards it is developer docs".

### Intent

`intent.py` tags each commit, independently of the files, in this order: a per-sha override
from config, a conventional-commit prefix (`fix(api): …`), the first word of the subject
against keyword patterns (`add*` → feat, `fix*` → fix…), else `unknown`. Intent is a
conclusion, so it is never cached.

### Tidy rows are the canonical output

`rows.py` turns results into one `Row` per (commit, file, category side, line label) with
added and deleted counts. JSON and CSV output are exactly these rows, intended for pandas or a
spreadsheet. Every file change yields at least one row, even a pure move or a binary file
(one zero row), so nothing silently disappears.

`aggregate.py` is built on the rows: it buckets them by commit, intent, ISO week, month or
total, and sums `Counts` per category. `Counts.value(metric, basis, labels)` computes
`churn`/`added`/`deleted` over all non-blank lines, all lines, or a chosen set of labels.
Reporters only consume `Group`s (table, trend) or `CommitResult`s (explain); they never call
git.

## Module map

| Module | Responsibility | Depends on |
|---|---|---|
| `model.py` | plain frozen dataclasses: `Commit`, `FileChange`, `CommitFacts`, `Rule`, `Scope`, `Match`, `Label` | nothing |
| `taxonomy.py` | categories, `split_lines`, remap, ordering, `[taxonomy]` config | nothing |
| `intent.py` | commit intent from the message | nothing |
| `lines.py` | git-compatible line splitting, RLE, `count_ranges`, the `LineLabeler` protocol | model |
| `lexers/pygments_backend.py` | `PygmentsLabeler`: whole-blob lexing to labels | lines |
| `git.py` | the only module that runs `git`; diff parsing; `BlobReader` | model |
| `classify.py` | `order_rules`, `Windows`, `Classifier` (first-match engine) | model, taxonomy |
| `config.py` | find, parse and validate config files into `EffectiveConfig` | classify, taxonomy, intent |
| `plugins/base.py` | plugin protocol, `RepoView`, discovery, `language_hint`, `rule()` helper | classify |
| `plugins/{core,django,wxt,xcode}.py` | built-in plugins | base |
| `cache.py` | `SqliteCache`, `MemoryCache`, facts (de)serialisation | git, lines |
| `analysis.py` | `Analyzer`: facts → classified, counted `CommitResult`s | most of the above |
| `rows.py`, `aggregate.py` | tidy rows; grouping and metrics | analysis |
| `report/` | `table`, `tabular` (json/csv), `explain`, `trend` (HTML/SVG) | aggregate, rows |
| `init.py` | suggestions for a starter config | analysis |
| `cli.py` | Typer commands, sessions, error handling and exit codes | everything |

The dependency direction is strict: `model` and `taxonomy` at the bottom, `cli` at the top,
and reporters never reach back into git or the cache.

## The git layer

`git.py` is the only place that runs git, which keeps the rest of the code testable with plain
data. Points worth knowing:

- Every call runs `git -c core.quotePath=false …`, so non-ASCII paths come back as UTF-8.
  Paths that git still quotes (with tabs, quotes or newlines) go through `unquote`.
- `list_commits` uses `rev-list --no-walk` for plain commits and walks history only for
  ranges, `--history`, `--since`/`--until`. Merges are skipped while walking unless `--merges`
  is set. A merge is diffed against its first parent; a root commit against the empty tree.
- `diff` runs `git diff-tree -r -M --raw -p -U0 --full-index …` and `parse_diff_tree` merges
  the `--raw` part (modes, blob ids, status, rename similarity) with the patch part (hunk
  ranges, binary markers). Submodule changes (mode 160000) get no line counts; the analyzer
  classifies them as `supporting/deps`.
- `BlobReader` keeps one `git cat-file --batch` process open for the whole run; reading
  thousands of blobs through one pipe is far faster than one process per blob.
- `GitError` carries git's stderr; the CLI prints it and exits 1.

## Configuration

`config.load_config` collects, highest precedence first:

1. every file in `~/.config/commit-anatomy/repos/*.toml` (or `$XDG_CONFIG_HOME`) whose
   `[match]` table matches this repository, by `path` or by `remote` URL (normalised, so
   `git@github.com:a/b.git` equals `https://github.com/a/b`);
2. `.commit-anatomy.toml` at the repository root (which may not contain `[match]`).

User files exist so you can configure a repository you don't own, or don't want to commit to.
`[taxonomy]` and `[intent]` tables are applied lowest precedence first, so the user file wins.
Rules are validated eagerly (categories against the final taxonomy, globs, regexes, dates,
commits) and any problem raises `ConfigError(file, field, reason)`, which the CLI turns into
exit code 2.

## `init`

`init` writes a starter config. It analyses the history *without* the rules of the file it is
about to write (otherwise `--force` would hide exactly the paths it must suggest), then makes
two kinds of suggestion:

- **unclassified files**: grouped by directory, with a category guessed from directory names
  and file extensions;
- **splits**: when a coarse `**` rule (such as `docs/**` → `docs/dev`) covers subdirectories
  that each carry at least 10% of its churn and whose names suggest another category
  (`docs/manual/**` → `docs/user`).

Because config rules outrank every plugin rule, a suggestion is only emitted when it cannot
reclassify files that another rule currently places, and globs are escaped with
`wcmatch.glob.escape`. The test for `init` is that applying its output leaves nothing
unclassified and moves no already-classified file away from its rule.

## Errors and exit codes

`cli._handle_errors` wraps every command:

| exit | cause |
|---|---|
| 0 | success (also when no commits matched) |
| 1 | `GitError`, `PluginError`, file I/O errors, cache errors (with a hint to run `cache clear`) |
| 2 | `ConfigError` and command-line usage errors |
| 141 | stdout closed early (`… | head`); no message |

Warnings (shallow clone, files that could not be lexed) go to stderr and never change the exit
code, so JSON/CSV on stdout stays clean.

## Testing strategy

- `tests/unit`: pure functions and small classes with hand-built data (`result_helpers.py`
  builds `CommitResult`s without git).
- `tests/integration`: real throwaway git repositories made with `ScriptedRepo`
  (`tests/helpers.py`), the CLI through Typer's `CliRunner`, and the cache.
- `tests/golden`: per plugin, a table of path → expected category, run through the real
  rule ordering with an in-memory repository (`plugin_helpers.classify_paths`).

`tests/conftest.py` isolates every test from your git config, user config and cache.

## Extending

- A new framework or project type: write a plugin (`docs/writing-plugins.md`).
- A new output: add a module under `report/` that consumes `Group`s or `Row`s, and a command
  or `--format` value in `cli.py`.
- A different line labeller: implement the `LineLabeler` protocol (`lines.py`) and give it a
  distinct `version`. It is currently wired in `cli._session`, not discovered.
