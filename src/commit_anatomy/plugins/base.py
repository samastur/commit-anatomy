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
    active = []
    for plugin in plugins:
        try:
            scopes = plugin.detect(view)
        except Exception as e:
            raise PluginError(f"plugin {plugin.name!r} failed to detect: {e}") from e
        active.extend((plugin, scope) for scope in scopes)
    return active


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
