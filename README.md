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
