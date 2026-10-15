# commit-anatomy — design

Date: 2026-10-15
Status: draft for review

## 1. Purpose

Pull requests grew noticeably once development moved to LLM-assisted
workflows. `commit-anatomy` answers *what they grew with*: for one commit or
a range of commits it reports how many changed lines were documentation,
tests, implementation code (with and without comments) and supporting
changes (dependencies, migrations, translations, build config…), and whether
the commit was a feature, a fix or something else.

First targets are three repositories with different stacks:

| Repo | Stack | Notes |
|---|---|---|
| `hrcek` | Django, Python, uv | `docs/manual/{en,sl}`, `docs/dev`, `locale/`, app migrations |
| `hrcek-extension` | WXT, TypeScript, pnpm, Lingui | tests colocated as `src/**/*.test.ts` plus `tests/{e2e,contract,fake-hrcek}` |
| `hrcek-ios` | Xcode, Swift, SPM | `HrcekKit/Tests`, `*UITests`, `.xcstrings`, `project.pbxproj` |

Go and Kotlin/Android repositories are expected later and the design must
accommodate them without core changes.

All three repos are squash-merged (0, 3 and 0 merge commits), so in practice
one commit ≈ one PR; commit ranges cover the multi-commit case.

### Success criteria

- `commit-anatomy analyze` runs on the full history of all three repos with
  built-in plugins and no repo config, with ≤ 1 % of churn `unclassified`.
- Every file in every commit can be traced to the rule that classified it
  (`explain`).
- Reclassifying (e.g. splitting `docs/dev` from `docs/user`) is a config
  change only — never a code change.
- Adding a Go or Kotlin plugin requires only a new plugin module.

### Non-goals (v1)

- GitHub/GitLab API integration (PR metadata, labels).
- Charts that combine several repos (concatenate CSV output instead).
- tree-sitter backend, AST-level analysis ("new vs modified functions").
- LLM-based intent classification.
- Go and Kotlin/Android *plugins* (their lexing is tested in v1; the plugins
  come later).

## 2. Concepts

**Category** — two levels, `top/sub`. The taxonomy is data (config), not
code, so a subcategory can later be promoted to a top level (translations
are the likely candidate) without code changes.

Default taxonomy:

| Top | Subcategories | Line-split |
|---|---|---|
| `docs` | `user`, `dev`, `api`, `llm`, `changelog` | no |
| `tests` | `unit`, `integration`, `e2e`, `support` | yes |
| `code` | `app`, `tooling` | yes |
| `supporting` | `deps`, `migrations`, `translations`, `build`, `ci`, `generated`, `assets` | no |
| `unclassified` | — | no |

- `docs/llm`: files that exist to steer LLM agents — `CLAUDE.md`,
  `AGENTS.md`, `REVIEW_GUIDE.md`, `docs/superpowers/**` (specs, plans).
- `code/app` includes UI (templates, CSS, SwiftUI); a repo config may split
  it if ever needed.
- `code/tooling`: `scripts/`, `.githooks/`.

**Line label** — for line-split categories each changed line gets one label:
`code`, `comment`, `docstring`, `blank`, or `unlexed` (no lexer / lexer
failure / oversize blob). A line containing both code and a trailing comment
is `code`. For non-split categories every text line is labelled `blank`
(whitespace only) or `raw`; blank detection needs no lexer, so every
category can be measured with and without blank lines.

**Metrics** derived per category:

- `total` = all changed lines (all labels) — reconciles with `git diff --numstat`.
- `non_blank` = `total − blank`; defined for every category and the default
  basis for comparing categories (so markdown paragraph breaks do not
  inflate docs relative to code).
- `with_comments` = `code + comment + docstring + unlexed` (line-split
  categories; equals `non_blank` there).
- `code_only` = `code + unlexed`.

Example: a commit adding 20 code, 5 comment, 3 docstring and 4 blank lines
→ `total` 32, `non_blank` = `with_comments` 28, `code_only` 20.

Direction: `churn` = added + deleted (default); `added` and `deleted` are
selectable. Basis: `non_blank` (default) or `total`.

**Intent** — per commit: `feat | fix | refactor | test | docs | chore |
release | unknown`. Orthogonal to categories, so reports can say "fix
commits: 40 % tests".

## 3. Architecture

```
revspec ─► git layer ─► Commit + FileChange[] ─► classifier ─► line labeller ─► rows ─► aggregator ─► reporters
                │                                   ▲    ▲           ▲
                └─ message ─► intent                │    │           └─ lexer backend (Pygments)
                                              plugins   config
```

Each unit has one job and a narrow interface; all of them are testable
without the others.

### 3.1 Git layer (`git.py`)

- Uses the `git` CLI via subprocess (no libgit2). Worktrees work as-is.
- Input: revspecs (`HEAD`, `<sha>`, `A..B`, several), optional
  `--since/--until` dates. Default `HEAD` (a single commit).
- Each commit is diffed against its first parent; root commits against the
  empty tree. Merge commits are skipped unless `--merges`.
- Per commit: `git diff -M --numstat` (binary detection, rename pairs) and
  `git diff -M -U0 --no-color` (hunk headers → changed old/new line numbers).
  Blob contents via one long-lived `git cat-file --batch` process.
- Produces `Commit(sha, parents, author, date, subject, body)` and
  `FileChange(path, old_path, status, binary, similarity, old_blob,
  new_blob, removed_lines: list[int], added_lines: list[int])`.
- Shallow clone: warning that history is truncated.

### 3.2 Plugins (`plugins/`)

```python
class Plugin(Protocol):
    name: str
    def detect(self, repo: RepoView) -> list[Scope]: ...   # [] = not applicable
    def rules(self) -> list[Rule]: ...                      # ordered, paths relative to scope root
    def languages(self) -> dict[str, str]: ...              # glob → Pygments lexer alias hints
```

- `RepoView` gives read-only access to the tree at HEAD (list files, read
  file) so detection can inspect `pyproject.toml`, `package.json`, etc.
- A `Scope` is a directory root (repo root or subdirectory). Several plugins
  — or one plugin several times — may be active in one repo (monorepos).
- Plugins are discovered via the entry-point group `commit_anatomy.plugins`.
  Built-ins register through the same group; there is no privileged path.
- Built-in in v1: `core` (always active, repo root), `django`, `wxt`, `xcode`.

### 3.3 Rules and configuration (`config.py`, `classify.py`)

A rule:

```toml
[[rules]]
id = "user-manual"               # optional; generated if absent
paths = ["docs/manual/**"]       # globs (globstar, braces); any match
content = 'Code generated .* DO NOT EDIT'  # optional regex
content_lines = 5                # lines searched by `content` (default 5)
category = "docs/user"
since = "2026-03-01"             # optional; date or commit, inclusive
until = "a1b2c3d"                # optional; date or commit, exclusive
```

- A rule matches when every given condition holds (`paths` and/or `content`
  and the time window).
- Globs use `wcmatch` (`GLOBSTAR | BRACE | DOTGLOB`).
- Time windows: a commit bound means "analysed commit is a descendant of
  (since) / not a descendant of (until) X" via `git merge-base
  --is-ancestor`, cached; a date bound compares committer date.

**Where config lives** (the tool is not part of the analysed repos):

1. User config: `~/.config/commit-anatomy/repos/<name>.toml`, each with
   `match = { path = "~/src/my-project" }` or
   `match = { remote = "github.com/Hrcek-app/hrcek-ios" }`.
2. In-repo: `.commit-anatomy.toml` at the repo root.

Why user config wins: the in-repo file is the team's shared, committed
view; the user file is the analyst's personal view of that same repo (like
`.git/info/exclude` vs `.gitignore`) — for repos one cannot or does not want
to commit to, or to disagree locally without editing the shared file. There
is deliberately no repo-independent global user config in v1.

Config files can contain `[[rules]]`, `[taxonomy]` (add or rename
categories, set `split_lines`), `[intent]` (keyword tables) and
`[intent.overrides]` (`"<sha prefix>" = "fix"`).

**Precedence** — first matching rule wins, in this order:

1. user config rules (in file order)
2. in-repo config rules
3. plugin rules with `stage = "pre"` (e.g. content-detected generated
   files, which must win over broad path rules such as `src/**`)
4. plugin rules, deepest scope first; within a plugin, declaration order
5. `core` rules
6. fallback → `unclassified`

`stage` is a rule field (`"pre"` or `"normal"`, default `"normal"`).

Config errors (unknown category, bad glob, bad regex, unknown field) are
fatal and reported with file and field.

**Refactors and moves:**

- Rules are evaluated against the path *as it is in the analysed commit*:
  removed lines are classified by `old_path`, added lines by `path`.
- A file whose two sides land in different categories is flagged
  `reclassified`.
- A pure rename (similarity 100 %) contributes 0 lines and is counted in a
  separate `moved` counter.
- When a path changes meaning over time, use `since`/`until` windows.
- Config is always the *current* config; it is never read from historical
  commits, so results are reproducible from what is visible.

### 3.4 Line labeller (`lines.py`, `lexers/`)

```python
class LineLabeler(Protocol):
    version: str
    def label(self, path: str, content: bytes, hint: str | None) -> list[Label]: ...
```

- Full lexing runs only for categories with `split_lines = true`; for all
  other text files only blank detection runs (no lexer).
- Lexes the *whole* old and new blob (so multi-line comments and docstrings
  are correct), then reads labels for `removed_lines` (old) and
  `added_lines` (new).
- Pygments backend: token types `Comment.*` → `comment`; `String.Doc`, and
  Python string-expression statements that are a module/class/function
  docstring → `docstring`; whitespace-only → `blank`; anything else on the
  line → `code`. Lexer chosen by plugin hint, then by filename.
- Known language behaviour verified by tests: Python docstrings, Django
  templates (`HtmlDjangoLexer`: `{# #}`, `{% comment %}`, `<!-- -->`),
  TypeScript JSDoc, Swift `///` and `/** */`, Go `//`, Kotlin KDoc.
- Degraded cases → `unlexed`: no lexer, lexer exception, blob > 1 MB
  (configurable). Non-UTF-8 decoded with `errors="replace"`.
- Labels cached in `~/.cache/commit-anatomy/` keyed by
  `(blob_sha, lexer alias, labeler.version)`; `--no-cache` disables it.
- The protocol allows a tree-sitter backend later without touching callers.

### 3.5 Intent classifier (`intent.py`)

In order:

1. `[intent.overrides]` from config (sha prefix → intent).
2. Conventional-commit prefix (`feat:`, `fix(scope):`, `refactor!:` …).
3. Keyword table on the subject's *first word*, case-insensitive,
   whole-word or prefix-of-word as listed (configurable). Defaults:
   `fix|bug|crash|regress|correct|repair` → `fix`;
   `release|bump version` → `release`; `refactor|rename|move|extract|tidy`
   → `refactor`; `test` (as first word) → `test`; `document|docs|manual|translate`
   → `docs`; `add|support|introduce|allow|let|offer|show|enable` → `feat`.
4. Otherwise `unknown`.

`hrcek` and `hrcek-ios` use imperative prose without prefixes, so expect
more `unknown` there; overrides are the remedy.

### 3.6 Rows, aggregation, reporters

The canonical output is tidy rows, one per (commit, file side, label):

```
sha, date, author, intent, subject, path, old_path, status, category,
subcategory, rule_id, rule_origin, label, added, deleted, flags
```

`flags` ⊆ {`reclassified`, `rename`, `moved`, `binary`, `generated`,
`unlexed`}. Binary files produce one row with 0 lines and the `binary`
flag. Submodule pointer changes → `supporting/deps`, 0 lines.

The aggregator groups rows `--by commit | intent | week | month | total`
at `--level top | sub`. All reporters consume aggregated or raw rows; none
touch git.

## 4. CLI

```
commit-anatomy analyze [REVSPEC...] [-C REPO] [--since D] [--until D]
                       [--by commit|intent|week|month|total] [--level top|sub]
                       [--metric churn|added|deleted] [--basis non_blank|total]
                       [--format table|json|csv]
                       [--merges] [--no-cache]
commit-anatomy explain COMMIT [-C REPO] [--path GLOB]
commit-anatomy trend   [REVSPEC...] [-C REPO] --bucket week|month [-o FILE.html]
commit-anatomy detect  [-C REPO]
commit-anatomy rules   [-C REPO]
commit-anatomy init    [-C REPO] [--user|--repo] [REVSPEC...] [--force]
```

- **analyze / table**: one row per group; for each top category `+a −d`
  and share of the group on the selected basis (`non_blank` by default);
  for line-split categories also `code_only` and comment lines. Footer: totals, `moved` count,
  `unclassified` (highlighted when non-zero).
- **analyze / json, csv**: the tidy rows (`--by` ignored), loadable
  straight into pandas or a spreadsheet.
- **explain**: per file — category on each side, rule id, origin (plugin
  or config file), which glob or content pattern matched, label counts,
  flags.
- **trend**: self-contained HTML with inline SVG (rendered by the tool, no
  JS, no external assets): stacked bars of category share per bucket plus
  absolute churn per bucket.
- **detect**: active plugins and scopes, config files found.
- **rules**: the effective ordered rule list with origins.
- **init**: writes a starter config — the user per-repo file by default
  (`--user`, with `match` set to the repo's remote, or its path if there is
  no remote) or `.commit-anatomy.toml` with `--repo`. Deterministic, from
  introspection over REVSPEC (default: full history of HEAD):
  - header comment with detected plugins and scopes;
  - one suggested rule per directory whose files end up `unclassified`
    (best-guess category from the core heuristics, otherwise
    `unclassified`), with a comment giving file count and churn;
  - a suggested split where one rule matches several sibling directories
    that each carry ≥ 10 % of that rule's churn (e.g. `docs/**` matching
    both `docs/manual/` and `docs/dev/`), with a comment;
  - every suggestion is an active rule with a `# why:` comment; the user
    edits or deletes them and checks the effect with `explain`.
  Refuses to overwrite an existing file unless `--force`. Without `init`,
  configuration is optional and written by hand.

Exit codes: 0 success, 1 runtime error (not a git repo, bad revspec — git's
stderr included), 2 usage or config error.

## 5. Built-in plugin defaults

All paths relative to scope root.

**core** (always on, lowest precedence)
- `CLAUDE.md`, `AGENTS.md`, `**/CLAUDE.md`, `**/AGENTS.md`, `REVIEW_GUIDE.md`,
  `docs/superpowers/**` → `docs/llm`
- `CHANGELOG*` → `docs/changelog`
- content `(?i)(code generated|do not edit|@generated)` → `supporting/generated`
  (`stage = "pre"`)
- `.github/**`, `.pre-commit-config.yaml`, `.gitlab-ci.yml` → `supporting/ci`
- `scripts/**`, `.githooks/**` → `code/tooling`
- `LICENSE*`, `.gitignore`, `.dockerignore`, `.gitleaks.toml`, `Dockerfile`,
  `docker/**`, `deploy/**`, `.*rc`, `.editorconfig` → `supporting/build`
- `**/*.{png,jpg,jpeg,gif,svg,webp,avif,ico,pdf,ttf,otf,woff,woff2}` → `supporting/assets`
- `docs/**`, `**/*.{md,rst}` → `docs/dev`

**django** — detected by `manage.py` or `django` in `pyproject.toml` dependencies
- `**/migrations/*.py` → `supporting/migrations`
- `locale/**`, `**/locale/**` → `supporting/translations`
- `pyproject.toml`, `uv.lock`, `requirements*.txt`, `.python-version` → `supporting/deps`
- `docs/manual/**` → `docs/user`; `docs/api/**` → `docs/api`; `docs/dev/**` → `docs/dev`
- `**/conftest.py`, `tests/**/factories*.py`, `tests/**/fixtures/**` → `tests/support`
- `tests/**/integration/**` → `tests/integration`; `tests/**/e2e/**` → `tests/e2e`
- `tests/**` → `tests/unit`
- `src/**`, `**/*.py`, `**/templates/**`, `**/static/**` → `code/app`

**wxt** — detected by `wxt` in `package.json` dependencies
- (in this order)
- `tests/e2e/**` → `tests/e2e`; `tests/contract/**` → `tests/integration`;
  `tests/fake-*/**`, `tests/**/fixtures/**` → `tests/support`; `tests/**` → `tests/unit`
- `**/*.{test,spec}.{ts,tsx,js}` (colocated) → `tests/unit`
- `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `package-lock.json`,
  `yarn.lock` → `supporting/deps`
- `**/locales/**`, `**/*.po` → `supporting/translations`
- `*.config.{ts,js,mjs}`, `tsconfig*.json`, `.prettierignore` → `supporting/build`
- `.wxt/**`, `.output/**` → `supporting/generated`
- `src/**`, `assets/**/*.{css,ts}` → `code/app`

**xcode** — detected by `*.xcodeproj` or `Package.swift`
- `**/*UITests/**` → `tests/e2e`
- `**/Tests/**`, `**/*Tests/**` → `tests/unit`
- `*.xctestplan` → `tests/support`
- `**/*.{xcstrings,strings,stringsdict}` → `supporting/translations`
- `**/*.pbxproj`, `**/*.xcworkspace/**`, `**/*.xcscheme`, `**/*.xcconfig`,
  `Config/**`, `.swiftlint.yml`, `.swift-format` → `supporting/build`
- `**/Package.swift`, `**/Package.resolved`, `Brewfile` → `supporting/deps`
- `**/*.xcassets/**` → `supporting/assets`
- `**/*.swift`, `Support/**` → `code/app`

Exact globs are tuned during implementation against the three repos; the
target is the ≤ 1 % unclassified criterion.

## 6. Error handling

| Situation | Behaviour |
|---|---|
| Not a git repo, bad revspec | exit 1, message incl. git stderr |
| Invalid config | exit 2, file + field + reason |
| No lexer / lexer error / oversize blob | lines labelled `unlexed`, warning once per extension |
| Non-UTF-8 blob | decode with replacement |
| Binary file | 1 row, 0 lines, `binary` flag |
| Submodule change | `supporting/deps`, 0 lines |
| Root commit | diff against empty tree |
| Shallow clone | warning |
| Unclassified files | reported in footer; listed by `explain` |

## 7. Testing

- **Unit**: glob/content matching, precedence, `since`/`until` (dates and
  ancestry), taxonomy validation, intent parsing, hunk-header parsing,
  labeller per language on small fixture files (Python, Django template,
  TypeScript, Swift, Go, Kotlin).
- **Integration**: scripted throwaway repos in `tmp_path` (`git init`,
  commits, renames, moves across categories, binary files, merge, root
  commit); assert exact tidy rows.
- **Golden per plugin**: a minimal ecosystem skeleton per plugin; pin
  `detect` output and classification of typical files.
- No test depends on the hrcek repos. Manual acceptance: run `analyze`
  over each repo's full history and check the unclassified share.

## 8. Project layout and tooling

Python ≥ 3.12, managed with `uv`, src layout. Runtime deps: `pygments`,
`wcmatch`, `typer`, `rich`. Dev: `pytest`, `ruff`.

```
src/commit_anatomy/
  cli.py                 # commands only, thin
  git.py                 # revspec → Commit / FileChange
  model.py               # dataclasses: Commit, FileChange, Rule, Scope, Row
  config.py              # load + validate user/in-repo config, taxonomy
  classify.py            # rule engine, precedence, time windows
  lines.py               # LineLabeler protocol, hunk → label mapping
  lexers/pygments_backend.py
  intent.py
  cache.py
  aggregate.py
  report/{table,tabular,trend,explain}.py
  plugins/{base,core,django,wxt,xcode}.py
tests/{unit,integration,golden}/
```

Console script: `commit-anatomy`. Entry-point group:
`commit_anatomy.plugins`.

## 9. Future work (not v1)

- Plugins: `go` (`*_test.go`, `go.mod/go.sum`, `vendor/`, `testdata/`,
  generated header), `android` (Gradle files, `libs.versions.toml`,
  `src/test` vs `src/androidTest`, `res/values-*/strings.xml`, Room schemas).
- tree-sitter labeller backend.
- PR-level grouping via GitHub API; multi-repo trend charts.
- Optional LLM fallback for `unknown` intent; LLM-assisted `init`.
- Repo-independent global user config (between in-repo config and plugins).
