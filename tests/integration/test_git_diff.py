import os

from helpers import ScriptedRepo

from commit_anatomy.git import GitRepo


def changes(repo, sha):
    g = GitRepo(repo.path)
    (commit,) = g.commits([sha])
    result = {c.path: c for c in g.diff(commit)}
    g.close()
    return result


def test_root_commit_is_diffed_against_the_empty_tree(repo):
    sha = repo.commit("root", {"a.txt": "1\n2\n"})
    ch = changes(repo, sha)["a.txt"]
    assert (ch.status, ch.old_path, ch.new_path, ch.old_blob, ch.added_lines) == (
        "A",
        None,
        "a.txt",
        None,
        ((1, 2),),
    )


def test_modify_delete_and_renames(repo):
    repo.commit(
        "root",
        {
            "edit.md": "one\ntwo\nthree\nfour\n",
            "gone.txt": "x\n",
            "keep.txt": "k\n",
            "move.txt": "1\n2\n3\n4\n5\n6\n7\n8\n",
        },
    )
    repo.git("mv", "keep.txt", "kept.txt")
    repo.git("mv", "move.txt", "moved.txt")
    sha = repo.commit(
        "second",
        {
            "edit.md": "one\nTWO\nthree\nfour\nfive\n",
            "gone.txt": None,
            "moved.txt": "1\n2\n3\nFOUR\n5\n6\n7\n8\n",
        },
    )
    ch = changes(repo, sha)
    assert ch["edit.md"].removed_lines == ((2, 1),)
    assert ch["edit.md"].added_lines == ((2, 1), (5, 1))
    gone = ch["gone.txt"]
    assert (gone.status, gone.new_path, gone.removed_lines) == ("D", None, ((1, 1),))
    kept = ch["kept.txt"]
    assert (kept.status, kept.old_path, kept.similarity, kept.added_lines) == (
        "R",
        "keep.txt",
        100,
        (),
    )
    moved = ch["moved.txt"]
    assert (moved.status, moved.old_path, moved.removed_lines, moved.added_lines) == (
        "R",
        "move.txt",
        ((4, 1),),
        ((4, 1),),
    )
    assert moved.similarity < 100


def test_binary_mode_symlink_and_submodule(repo, tmp_path):
    sub = ScriptedRepo(tmp_path / "sub")
    sub.commit("s", {"s.txt": "s\n"})
    repo.commit("root", {"bin.dat": b"\x00\x01", "run.sh": "echo\n", "link": "target\n"})
    os.chmod(repo.path / "run.sh", 0o755)
    (repo.path / "link").unlink()
    os.symlink("bin.dat", repo.path / "link")
    repo.write({"bin.dat": b"\x00\x02"})
    repo.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub.path), "sm")
    ch = changes(repo, repo.commit("second"))
    assert ch["bin.dat"].binary
    assert ch["run.sh"].status == "M" and ch["run.sh"].added_lines == ()
    assert ch["link"].status == "T"
    assert (ch["link"].removed_lines, ch["link"].added_lines) == (((1, 1),), ((1, 1),))
    assert ch["sm"].submodule and ch["sm"].added_lines == ()
    assert ".gitmodules" in ch


def test_non_ascii_and_quoted_paths(repo):
    names = ["Hrček.md", "with space.txt", 'q"uote.txt', "tab\there.txt"]
    ch = changes(repo, repo.commit("root", {n: "x\n" for n in names}))
    assert sorted(ch) == sorted(names)
    assert all(c.added_lines == ((1, 1),) for c in ch.values())


def test_no_trailing_newline(repo):
    repo.commit("root", {"a.txt": "x"})
    assert changes(repo, repo.commit("two", {"a.txt": "y"}))["a.txt"].added_lines == ((1, 1),)


def test_empty_commit_has_no_changes(repo):
    repo.commit("root", {"a.txt": "1\n"})
    assert changes(repo, repo.commit("empty")) == {}


def test_merge_is_diffed_against_its_first_parent(repo):
    repo.commit("base", {"a.txt": "1\n"})
    repo.git("checkout", "-q", "-b", "side")
    repo.commit("side", {"b.txt": "1\n"})
    repo.git("checkout", "-q", "main")
    repo.commit("main", {"c.txt": "1\n"})
    repo.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = repo.git("rev-parse", "HEAD").strip()
    assert list(changes(repo, merge)) == ["b.txt"]


def test_read_blob(repo):
    sha = repo.commit("root", {"a.txt": "hello\n"})
    g = GitRepo(repo.path)
    (commit,) = g.commits([sha])
    (ch,) = g.diff(commit)
    assert g.read_blob(ch.new_blob) == b"hello\n"
    assert g.read_blob("0" * 40) is None
    g.close()
