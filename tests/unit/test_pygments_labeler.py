from commit_anatomy.lexers.pygments_backend import PygmentsLabeler

L = PygmentsLabeler()


def lab(text: str, path: str, hint: str | None = None):
    return L.label(text.encode(), L.resolve(path, hint))


def test_python_docstrings_comments_and_plain_strings():
    src = (
        '"""Module."""\n'
        "import os  # why\n"
        "\n"
        "def f():\n"
        '    """Doc\n'
        '    more."""\n'
        "    # note\n"
        '    x = """not a docstring"""\n'
    )
    assert lab(src, "a.py") == [
        "docstring",
        "code",  # code with a trailing comment is code
        "blank",
        "code",
        "docstring",
        "docstring",
        "comment",
        "code",
    ]


def test_django_template_comments_and_delimiters():
    src = "{# c #}\n{% comment %}\nhidden\n{% endcomment %}\n<!-- h -->\n<p>{{ x }}</p>\n"
    assert lab(src, "t.html", "html+django") == [
        "comment",
        "code",  # {% %} delimiters are Comment.Preproc in Pygments: code
        "comment",
        "code",
        "comment",
        "code",
    ]


def test_typescript_jsdoc():
    src = "/**\n * Doc\n */\nconst x = 1; // trailing\n// c\n"
    assert lab(src, "a.ts") == ["comment", "comment", "comment", "code", "comment"]


def test_swift_doc_comments():
    assert lab("/// doc\n/* a\n b */\nlet x = 1\n", "a.swift") == [
        "comment",
        "comment",
        "comment",
        "code",
    ]


def test_go_and_kotlin():
    assert lab("// Package p.\npackage p\n\n/* m */\n", "a.go") == [
        "comment",
        "code",
        "blank",
        "comment",
    ]
    assert lab("/**\n * KDoc\n */\nval x = 1\n", "a.kt") == [
        "comment",
        "comment",
        "comment",
        "code",
    ]


def test_line_endings_keep_one_label_per_git_line():
    assert lab("a = 1\r\n# c\r\n\r\nb = 2\r\n", "a.py") == ["code", "comment", "blank", "code"]
    assert lab("a = 1\n# c", "a.py") == ["code", "comment"]  # no trailing newline
    assert lab("a = 1\r# c\n", "a.py") == ["code"]  # a lone CR is not a line break for git
    assert lab("\n\nx = 1\n", "a.py") == ["blank", "blank", "code"]


def test_unknown_language_is_unlexed_but_blank_lines_stay_blank():
    assert L.resolve("x.unknownext", None) is None
    assert L.label(b"a\n\nb\n", None) == ["unlexed", "blank", "unlexed"]


def test_unknown_hint_falls_back_to_filename_lexer():
    assert L.resolve("x.py", "no-such-lexer-xyz") == L.resolve("x.py", None) == "python"
    assert L.resolve("x.unknownext", "no-such-lexer-xyz") is None


def test_oversize_blob_is_unlexed():
    assert PygmentsLabeler(max_bytes=5).label(b"x = 1\n# c\n", "python") == [
        "unlexed",
        "unlexed",
    ]


def test_non_utf8_is_decoded_with_replacement():
    assert L.label(b"x = '\xff'\n", "python") == ["code"]


def test_version_names_the_backend():
    assert "pygments" in L.version
