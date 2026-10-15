"""Pygments-based line labeller: lex the whole blob, then label each line."""

from __future__ import annotations

import posixpath
from functools import cache, lru_cache

import pygments
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.token import Comment, String
from pygments.util import ClassNotFound

from ..lines import decode, split_lines
from ..model import Label

MAX_BYTES = 1_000_000


@cache
def _lexer(alias: str) -> Lexer:
    # stripnl=False keeps leading blank lines, so token lines stay aligned with git's lines.
    return get_lexer_by_name(alias, stripnl=False, stripall=False, ensurenl=True)


@lru_cache(maxsize=4096)
def _alias(basename: str, hint: str | None) -> str | None:
    lexer = None
    if hint:
        try:
            lexer = get_lexer_by_name(hint)
        except ClassNotFound:
            pass  # unknown hint: fall back to the filename
    if lexer is None:
        try:
            lexer = get_lexer_for_filename(basename)
        except ClassNotFound:
            return None
    return lexer.aliases[0] if lexer.aliases else None


def _kind(token) -> str:
    if token in Comment.Preproc or token in Comment.PreprocFile:
        return "code"  # C #include, Django {% %} and {{ }} delimiters
    if token in Comment:
        return "comment"
    if token in String.Doc:
        return "docstring"
    return "code"


def _label(kinds: set[str]) -> Label:
    if not kinds:
        return "blank"
    if "code" in kinds:
        return "code"
    if "docstring" in kinds:
        return "docstring"
    return "comment"


def _unlexed(lines: list[str]) -> list[Label]:
    return ["blank" if not line.strip() else "unlexed" for line in lines]


class PygmentsLabeler:
    version = f"pygments-labeler-1/pygments-{pygments.__version__}"

    def __init__(self, max_bytes: int = MAX_BYTES):
        self.max_bytes = max_bytes

    def resolve(self, path: str, hint: str | None) -> str | None:
        return _alias(posixpath.basename(path), hint)

    def label(self, content: bytes, alias: str | None) -> list[Label]:
        text = decode(content)
        lines = split_lines(text)
        if alias is None or len(content) > self.max_bytes:
            return _unlexed(lines)
        # CRLF -> LF keeps the line count; a lone CR is not a line break for git, so blank it.
        lexable = text.replace("\r\n", "\n").replace("\r", " ")
        kinds: list[set[str]] = [set() for _ in lines]
        line = 0
        try:
            for token, value in _lexer(alias).get_tokens(lexable):
                for i, part in enumerate(value.split("\n")):
                    if i:
                        line += 1
                    if line < len(kinds) and part.strip():
                        kinds[line].add(_kind(token))
        except Exception:  # a lexer bug must not abort the analysis
            return _unlexed(lines)
        return [_label(k) for k in kinds]
