"""Thin wrapper around the git CLI: commits, metadata, diffs and blobs."""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .model import Commit, FileChange

_LOG_FORMAT = "%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%B"

DIFF_ARGS = (
    "-r",
    "-M",
    "--raw",
    "--no-abbrev",
    "-p",
    "-U0",
    "--full-index",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)
DIFF_OPTIONS = "diff-tree " + " ".join(DIFF_ARGS)

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote(text: str) -> str:
    """Undo git's C-style path quoting (escapes and octal UTF-8 bytes)."""
    if not text.startswith('"'):
        return text
    out = bytearray()
    i, end = 1, len(text) - 1
    while i < end:
        ch = text[i]
        if ch == "\\":
            nxt = text[i + 1]
            if nxt in "01234567":
                out.append(int(text[i + 1 : i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, ord(nxt)))
            i += 2
            continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace")


def _header_path(line: str) -> str:
    rest = line[len("diff --git ") :]
    if rest.startswith('"'):
        quoted = _QUOTED_RE.match(rest)
        return unquote(quoted.group(0))[2:] if quoted else rest
    # Not a rename (renames are re-keyed by "rename to"): "a/<path> b/<path>", same path twice.
    half = (len(rest) - len("a/ b/")) // 2
    return rest[2 : 2 + half]


@dataclass
class _Block:
    key: str
    removed: list[tuple[int, int]] = field(default_factory=list)
    added: list[tuple[int, int]] = field(default_factory=list)
    binary: bool = False


def parse_diff_tree(text: str) -> list[FileChange]:
    """Parse `git diff-tree --raw -p -U0 --full-index` output into FileChanges."""
    raw: list[tuple[str, str, str, str, str, list[str]]] = []
    blocks: dict[str, list[_Block]] = defaultdict(list)
    current: _Block | None = None
    for line in text.split("\n"):
        if current is None and line.startswith(":"):
            meta, *paths = line.split("\t")
            old_mode, new_mode, old_blob, new_blob, status = meta[1:].split(" ")
            raw.append(
                (old_mode, new_mode, old_blob, new_blob, status, [unquote(p) for p in paths])
            )
        elif line.startswith("diff --git "):
            current = _Block(_header_path(line))
            blocks[current.key].append(current)
        elif current is None:
            continue
        elif line.startswith("rename to "):
            blocks[current.key].remove(current)
            current.key = unquote(line[len("rename to ") :])
            blocks[current.key].append(current)
        elif line.startswith("Binary files "):
            current.binary = True
        elif line.startswith("@@ "):
            hunk = _HUNK_RE.match(line)
            if hunk is None:
                continue
            old_start, old_count, new_start, new_count = hunk.groups()
            removed = 1 if old_count is None else int(old_count)
            added = 1 if new_count is None else int(new_count)
            if removed:
                current.removed.append((int(old_start), removed))
            if added:
                current.added.append((int(new_start), added))

    changes = []
    for old_mode, new_mode, old_blob, new_blob, status, paths in raw:
        letter = status[0]
        own = blocks.get(paths[-1], [])  # a typechange has two blocks for one path
        submodule = "160000" in (old_mode, new_mode)
        changes.append(
            FileChange(
                old_path=None if letter == "A" else paths[0],
                new_path=None if letter == "D" else paths[-1],
                status=letter,
                binary=any(b.binary for b in own),
                similarity=int(status[1:]) if len(status) > 1 else None,
                old_blob=None if set(old_blob) == {"0"} else old_blob,
                new_blob=None if set(new_blob) == {"0"} else new_blob,
                removed_lines=() if submodule else tuple(r for b in own for r in b.removed),
                added_lines=() if submodule else tuple(r for b in own for r in b.added),
                submodule=submodule,
            )
        )
    return changes


class GitError(Exception):
    pass


class BlobReader:
    """One long-lived `git cat-file --batch` process."""

    def __init__(self, cwd: Path):
        self._cwd = cwd
        self._proc: subprocess.Popen[bytes] | None = None

    def read(self, name: str) -> bytes | None:
        if self._proc is None:
            self._proc = subprocess.Popen(
                ["git", "-c", "core.quotePath=false", "cat-file", "--batch"],
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(name.encode("utf-8") + b"\n")
        self._proc.stdin.flush()
        header = self._proc.stdout.readline()
        if not header or header.rstrip().endswith((b" missing", b" ambiguous")):
            return None
        size = int(header.split()[-1])
        data = self._proc.stdout.read(size)
        self._proc.stdout.read(1)  # the LF after the content
        return data

    def close(self) -> None:
        if self._proc is not None:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            self._proc.wait()
            self._proc = None


class GitRepo:
    def __init__(self, path: Path | str = "."):
        self.cwd = Path(path)
        if not self.cwd.is_dir():
            raise GitError(f"{self.cwd}: not a directory")
        self.root = Path(self._run("rev-parse", "--show-toplevel").strip())
        self._reader = BlobReader(self.root)
        self._empty_tree: str | None = None

    def _call(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=self.cwd,
            input=input,
            capture_output=True,
        )

    def _run(self, *args: str, input: bytes | None = None) -> str:
        result = self._call(*args, input=input)
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()
            raise GitError(message or f"git {args[0]} failed with exit code {result.returncode}")
        return result.stdout.decode("utf-8", "replace")

    def list_commits(
        self,
        revspecs: list[str],
        *,
        history: bool = False,
        since: str | None = None,
        until: str | None = None,
        merges: bool = False,
    ) -> list[str]:
        walk = (
            history
            or since is not None
            or until is not None
            or any(".." in r or r.startswith("^") for r in revspecs)
        )
        if walk:
            args = ["rev-list", "--reverse"]
            if not merges:
                args.append("--no-merges")
            if since:
                args.append(f"--since={since}")
            if until:
                args.append(f"--until={until}")
        else:
            args = ["rev-list", "--no-walk=unsorted"]
        return self._run(*args, *revspecs, "--").split()

    def commits(self, shas: list[str]) -> list[Commit]:
        if not shas:
            return []
        out = self._run(
            "-c",
            "log.showSignature=false",
            "log",
            "--no-walk=unsorted",
            "-z",
            f"--format={_LOG_FORMAT}",
            "--stdin",
            input=("\n".join(shas) + "\n").encode(),
        )
        found: dict[str, Commit] = {}
        for record in out.split("\x00"):
            if not record.strip():
                continue
            sha, parents, name, email, authored, committed, message = record.split("\x1f", 6)
            sha = sha.strip()
            found[sha] = Commit(
                sha=sha,
                parents=tuple(parents.split()),
                author_name=name,
                author_email=email,
                author_date=datetime.fromisoformat(authored),
                committer_date=datetime.fromisoformat(committed),
                message=message.rstrip("\n"),
            )
        return [found[s] for s in shas]

    def diff(self, commit: Commit) -> list[FileChange]:
        base = commit.parents[0] if commit.parents else self.empty_tree()
        return parse_diff_tree(self._run("diff-tree", *DIFF_ARGS, base, commit.sha))

    def head_files(self) -> list[str]:
        if self._call("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
            return []
        out = self._run("ls-tree", "-r", "--full-tree", "--name-only", "-z", "HEAD")
        return [p for p in out.split("\x00") if p]

    def read_head_file(self, path: str) -> bytes | None:
        return self._reader.read(f"HEAD:{path}")

    def read_blob(self, oid: str) -> bytes | None:
        return self._reader.read(oid)

    def remote_url(self) -> str | None:
        result = self._call("config", "--get", "remote.origin.url")
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def is_shallow(self) -> bool:
        return self._run("rev-parse", "--is-shallow-repository").strip() == "true"

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self._call("merge-base", "--is-ancestor", ancestor, descendant)
        if result.returncode in (0, 1):
            return result.returncode == 0
        raise GitError(result.stderr.decode("utf-8", "replace").strip())

    def resolve_commit(self, rev: str) -> str | None:
        result = self._call("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def empty_tree(self) -> str:
        if self._empty_tree is None:
            self._empty_tree = self._run("hash-object", "-t", "tree", "--stdin", input=b"").strip()
        return self._empty_tree

    def close(self) -> None:
        self._reader.close()
