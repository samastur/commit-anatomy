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


from helpers import ScriptedRepo  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    return ScriptedRepo(tmp_path / "repo")
