from commit_anatomy.git import parse_diff_tree, unquote
from commit_anatomy.model import FileChange

Z = "0" * 40
A, B, C, D = "a" * 40, "b" * 40, "c" * 40, "d" * 40

SAMPLE = "\n".join(
    [
        f":100644 100644 {A} {B} M\tHrček.md",
        f":100644 100644 {A} {B} M\tbin.dat",
        f":100644 120000 {A} {B} T\tlink",
        f":100644 100755 {C} {C} M\tmode.sh",
        f":100644 100644 {D} {D} R100\tkeep.txt\tmoved.txt",
        f':000000 100644 {Z} {B} A\t"q\\"uote.txt"',
        f":100644 000000 {A} {Z} D\tgone.txt",
        f":000000 160000 {Z} {C} A\tsm",
        "",
        "diff --git a/Hrček.md b/Hrček.md",
        f"index {A}..{B} 100644",
        "--- a/Hrček.md",
        "+++ b/Hrček.md",
        "@@ -2 +2 @@ one",
        "-two",
        "+TWO",
        "@@ -4,0 +5 @@ four",
        "+five",
        "diff --git a/bin.dat b/bin.dat",
        f"index {A}..{B} 100644",
        "Binary files a/bin.dat and b/bin.dat differ",
        "diff --git a/link b/link",
        "deleted file mode 100644",
        f"index {A}..{Z}",
        "--- a/link",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-t",
        "diff --git a/link b/link",
        "new file mode 120000",
        f"index {Z}..{B}",
        "--- /dev/null",
        "+++ b/link",
        "@@ -0,0 +1 @@",
        "+keep.txt",
        "\\ No newline at end of file",
        "diff --git a/mode.sh b/mode.sh",
        "old mode 100644",
        "new mode 100755",
        "diff --git a/keep.txt b/moved.txt",
        "similarity index 100%",
        "rename from keep.txt",
        "rename to moved.txt",
        'diff --git "a/q\\"uote.txt" "b/q\\"uote.txt"',
        "new file mode 100644",
        f"index {Z}..{B}",
        "--- /dev/null",
        '+++ "b/q\\"uote.txt"',
        "@@ -0,0 +1,2 @@",
        "+a",
        "+b",
        "diff --git a/gone.txt b/gone.txt",
        "deleted file mode 100644",
        f"index {A}..{Z}",
        "--- a/gone.txt",
        "+++ /dev/null",
        "@@ -1,3 +0,0 @@",
        "-1",
        "-2",
        "-3",
        "diff --git a/sm b/sm",
        "new file mode 160000",
        f"index {Z}..{C}",
        "--- /dev/null",
        "+++ b/sm",
        "@@ -0,0 +1 @@",
        f"+Subproject commit {C}",
        "",
    ]
)


def test_parse_diff_tree():
    changes = parse_diff_tree(SAMPLE)
    by_path = {c.path: c for c in changes}
    assert [c.path for c in changes] == [
        "Hrček.md",
        "bin.dat",
        "link",
        "mode.sh",
        "moved.txt",
        'q"uote.txt',
        "gone.txt",
        "sm",
    ]
    assert by_path["Hrček.md"] == FileChange(
        "Hrček.md", "Hrček.md", "M", False, None, A, B, ((2, 1),), ((2, 1), (5, 1))
    )
    assert by_path["bin.dat"].binary and by_path["bin.dat"].added_lines == ()
    link = by_path["link"]
    assert (link.status, link.removed_lines, link.added_lines) == ("T", ((1, 1),), ((1, 1),))
    assert (by_path["mode.sh"].removed_lines, by_path["mode.sh"].added_lines) == ((), ())
    moved = by_path["moved.txt"]
    assert (moved.old_path, moved.new_path, moved.status, moved.similarity) == (
        "keep.txt",
        "moved.txt",
        "R",
        100,
    )
    quote = by_path['q"uote.txt']
    assert (quote.old_path, quote.old_blob, quote.added_lines) == (None, None, ((1, 2),))
    gone = by_path["gone.txt"]
    assert (gone.new_path, gone.new_blob, gone.removed_lines) == (None, None, ((1, 3),))
    assert by_path["sm"].submodule and by_path["sm"].added_lines == ()


def test_unquote():
    assert unquote('"tab\\there.txt"') == "tab\there.txt"
    assert unquote('"caf\\303\\251.txt"') == "café.txt"
    assert unquote('"q\\"uote"') == 'q"uote'
    assert unquote("plain name.txt") == "plain name.txt"


def test_empty_output_has_no_changes():
    assert parse_diff_tree("") == []
