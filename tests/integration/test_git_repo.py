import pytest

from commit_anatomy.git import GitError, GitRepo


def test_not_a_git_repository(tmp_path):
    with pytest.raises(GitError, match="not a git repository"):
        GitRepo(tmp_path)


def test_list_commits_single_walk_range_and_since(repo):
    a = repo.commit("one", {"a.txt": "1\n"})
    b = repo.commit("two", {"a.txt": "2\n"})
    c = repo.commit("three", {"a.txt": "3\n"})
    g = GitRepo(repo.path)
    assert g.list_commits(["HEAD"]) == [c]
    assert g.list_commits(["HEAD"], history=True) == [a, b, c]
    assert g.list_commits([f"{a}..HEAD"]) == [b, c]
    assert g.list_commits([c, a]) == [c, a]
    assert g.list_commits(["HEAD..HEAD"]) == []
    assert g.list_commits(["HEAD"], since="2026-01-06T00:00:00+00:00") == [b, c]


def test_bad_revspec_is_a_git_error(repo):
    repo.commit("one", {"a.txt": "1\n"})
    with pytest.raises(GitError, match="nope"):
        GitRepo(repo.path).list_commits(["nope"])


def test_merges_skipped_when_walking_but_kept_when_named(repo):
    base = repo.commit("base", {"a.txt": "1\n"})
    repo.git("checkout", "-q", "-b", "side")
    side = repo.commit("side", {"b.txt": "1\n"})
    repo.git("checkout", "-q", "main")
    main = repo.commit("main", {"c.txt": "1\n"})
    repo.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = repo.git("rev-parse", "HEAD").strip()
    g = GitRepo(repo.path)
    assert set(g.list_commits(["HEAD"], history=True)) == {base, side, main}
    assert merge in g.list_commits(["HEAD"], history=True, merges=True)
    assert g.list_commits([merge]) == [merge]


def test_commit_metadata(repo):
    sha = repo.commit("Add the thing\n\nWhy it matters.\n", {"a.txt": "1\n"})
    g = GitRepo(repo.path)
    (c,) = g.commits([sha])
    assert c.sha == sha and c.parents == ()
    assert c.subject == "Add the thing" and c.body == "Why it matters."
    assert (c.author_name, c.author_email) == ("Test Author", "author@example.com")
    assert c.author_date.isoformat() == "2026-01-05T12:00:00+00:00"
    assert g.commits([]) == []


def test_head_files_and_reading_them(repo):
    repo.commit("one", {"a.txt": "1\n", "dir/with space.md": "x\n"})
    g = GitRepo(repo.path)
    assert g.head_files() == ["a.txt", "dir/with space.md"]
    assert g.read_head_file("dir/with space.md") == b"x\n"
    assert g.read_head_file("missing file.txt") is None
    g.close()


def test_empty_repository_has_no_head_files(repo):
    assert GitRepo(repo.path).head_files() == []


def test_remote_shallow_ancestry_and_resolution(repo):
    a = repo.commit("one", {"a.txt": "1\n"})
    b = repo.commit("two", {"a.txt": "2\n"})
    g = GitRepo(repo.path)
    assert g.remote_url() is None
    repo.git("remote", "add", "origin", "git@github.com:me/x.git")
    assert g.remote_url() == "git@github.com:me/x.git"
    assert g.is_shallow() is False
    assert g.is_ancestor(a, b) and g.is_ancestor(a, a) and not g.is_ancestor(b, a)
    assert g.resolve_commit(a[:7]) == a
    assert g.resolve_commit("nope") is None
    assert len(g.empty_tree()) in (40, 64)


def test_root_is_the_toplevel_even_from_a_subdirectory(repo):
    repo.commit("one", {"sub/a.txt": "1\n"})
    assert GitRepo(repo.path / "sub").root == repo.path.resolve()


def test_head_files_from_a_subdirectory_are_root_relative(repo):
    repo.commit("one", {"a.txt": "1\n", "sub/b.txt": "2\n"})
    g = GitRepo(repo.path / "sub")
    assert g.head_files() == ["a.txt", "sub/b.txt"]
    assert g.read_head_file("sub/b.txt") == b"2\n"
    g.close()
