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
