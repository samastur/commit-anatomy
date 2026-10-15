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
