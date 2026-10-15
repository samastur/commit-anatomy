"""commit-anatomy command line."""

import functools
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
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
from .config import (
    REPO_FILE,
    ConfigError,
    EffectiveConfig,
    config_home,
    load_config,
    normalize_remote,
)
from .git import GitError, GitRepo
from .init import render_config, suggest_splits, suggest_unclassified
from .lexers.pygments_backend import PygmentsLabeler
from .model import Scope
from .plugins.base import GitRepoView, Plugin, PluginError, active_scopes, load_plugins
from .report.explain import render_explain
from .report.table import render_table
from .report.tabular import to_csv, to_json
from .report.trend import render_trend
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


def _warn_unlexed(analyzer: Analyzer) -> None:
    for ext in sorted(analyzer.unlexed_exts):
        message = f"warning: lines in {ext} files could not be lexed; counted as unlexed"
        _stderr().print(Text(message, style="yellow"))


def _is_cache_error(e: Exception) -> bool:
    if isinstance(e, sqlite3.Error):
        return True
    filename = getattr(e, "filename", None)
    if filename is None:
        return False
    return Path(os.path.abspath(filename)).is_relative_to(os.path.abspath(cache_home()))


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
        except (sqlite3.Error, OSError) as e:
            if _is_cache_error(e):
                path = cache_home() / "cache.sqlite"
                hint = "delete the file or run `commit-anatomy cache clear`"
                message = Text(f"error: cache {path}: {e} ({hint})", style="red")
            elif isinstance(e, OSError) and e.filename is not None:
                message = Text(f"error: {e.filename}: {e.strerror}", style="red")
            else:
                message = Text(f"error: {e}", style="red")
            _stderr().print(message, soft_wrap=True)
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
    cache = None
    try:
        cache = MemoryCache() if no_cache else SqliteCache(cache_home() / "cache.sqlite")
        if repo.is_shallow():
            _stderr().print(Text("warning: shallow clone, history is truncated", style="yellow"))
        active = active_scopes(load_plugins(), GitRepoView(repo))
        config = load_config(repo.root, repo.remote_url(), repo.resolve_commit)
        analyzer = Analyzer(repo, cache, config, active, PygmentsLabeler())
        yield Session(repo, active, config, analyzer)
    finally:
        if cache is not None:
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
        _warn_unlexed(s.analyzer)
    if output_format is Format.json:
        sys.stdout.write(to_json(rows_from(results)) + "\n")
    elif output_format is Format.csv:
        sys.stdout.write(to_csv(rows_from(results)))
    elif results:
        groups = aggregate(results, by, level)
        Console().print(render_table(groups, s.config.taxonomy, metric=metric, basis=basis, by=by))


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
        _warn_unlexed(s.analyzer)
    console = Console()
    for result in results:
        console.print(render_explain(result, path))


class Bucket(StrEnum):
    week = "week"
    month = "month"


@app.command()
@_handle_errors
def trend(
    revspec: Revspecs = None,
    repo: RepoOption = Path("."),
    bucket: Annotated[Bucket, typer.Option(help="Bar per week or per month.")] = Bucket.week,
    output: Annotated[Path, typer.Option("--output", "-o", help="HTML file to write.")] = Path(
        "commit-anatomy-trend.html"
    ),
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
        _warn_unlexed(s.analyzer)
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
            origin = f"user:{target.name}"
            remote = s.repo.remote_url()
            match = {"remote": normalize_remote(remote)} if remote else {"path": str(root)}
        else:
            target, origin, match = root / REPO_FILE, REPO_FILE, None
        if target.exists() and not force:
            raise ConfigError(target, "init", "file exists; use --force to overwrite")
        shas = s.repo.list_commits(revspec or ["HEAD"], history=True)
        # Analyse without the rules of the file about to be (re)written, or they would hide
        # exactly the paths it needs to suggest and --force would replace them with nothing.
        kept = tuple((rule, o) for rule, o in s.config.rules if o != origin)
        config = replace(s.config, rules=kept)
        analyzer = Analyzer(s.repo, s.analyzer.cache, config, s.active, s.analyzer.labeler)
        results = analyzer.analyze(shas)
        _warn_unlexed(analyzer)
        text = render_config(
            unclassified=suggest_unclassified(results, config.taxonomy),
            splits=suggest_splits(results, config.taxonomy),
            active=s.active,
            commits=len(shas),
            match=match,
            today=date.today(),
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _stderr().print(Text(f"Wrote {target}"))


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
    path = cache_home() / "cache.sqlite"
    try:
        cache = SqliteCache(path)
        try:
            cache.clear(blobs=blobs or both, commits=commits or both)
        finally:
            cache.close()
    except sqlite3.Error:
        # An unreadable database cannot be cleared selectively: remove the file.
        path.unlink(missing_ok=True)
    _stderr().print("Cache cleared.")


def main() -> None:
    app()
