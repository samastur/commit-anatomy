"""Line labels: splitting like git, blank detection, run-length encoding, range counting."""

from __future__ import annotations

from collections import Counter
from typing import Protocol

from .model import Label, LineRanges


def decode(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def split_lines(text: str) -> list[str]:
    """Split on \\n the way git numbers lines: no phantom line after a final newline."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def blank_labels(text: str) -> list[Label]:
    return ["blank" if not line.strip() else "raw" for line in split_lines(text)]


def rle_encode(labels: list[Label]) -> list[list]:
    runs: list[list] = []
    for label in labels:
        if runs and runs[-1][0] == label:
            runs[-1][1] += 1
        else:
            runs.append([label, 1])
    return runs


def rle_decode(runs: list[list]) -> list[Label]:
    return [label for label, count in runs for _ in range(count)]


def count_ranges(labels: list[Label], ranges: LineRanges) -> Counter[str]:
    counts: Counter[str] = Counter()
    for start, length in ranges:
        for line in range(start, start + length):
            counts[labels[line - 1] if 0 < line <= len(labels) else "unlexed"] += 1
    return counts


class LineLabeler(Protocol):
    version: str

    def resolve(self, path: str, hint: str | None) -> str | None:
        """Lexer alias for this file (plugin hint first, then filename), None if unknown."""
        ...

    def label(self, content: bytes, alias: str | None) -> list[Label]:
        """One label per line of the whole blob."""
        ...
