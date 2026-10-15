"""Throwaway git repositories for tests."""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

START = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)  # a Monday


class ScriptedRepo:
    def __init__(self, path: Path):
        path.mkdir(parents=True)
        self.path = path
        self._commits = 0
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, env: dict | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
        )
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
        return result.stdout

    def write(self, files: dict[str, str | bytes | None]) -> None:
        for rel, content in files.items():
            target = self.path / rel
            if content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def commit(self, message: str, files=None, when: datetime | None = None) -> str:
        if files:
            self.write(files)
        when = when or START + timedelta(days=self._commits)
        self._commits += 1
        stamp = when.isoformat()
        self.git("add", "-A")
        self.git(
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
            env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
        )
        return self.git("rev-parse", "HEAD").strip()
