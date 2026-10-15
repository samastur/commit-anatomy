from pathlib import Path

import pytest

from commit_anatomy.config import ConfigError, config_home, load_config, normalize_remote

FULL = "a1b2c3d" + "0" * 33


def resolve(rev):
    return {"a1b2c3d": FULL}.get(rev)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def user_repos():
    return config_home() / "repos"


def test_no_config_gives_defaults(tmp_path):
    cfg = load_config(tmp_path, None, resolve)
    assert cfg.rules == () and cfg.files == ()
    assert cfg.taxonomy.is_valid("docs/llm")
    assert cfg.intent.overrides == {}


def test_repo_file_rule_gets_generated_id_and_origin(tmp_path):
    write(
        tmp_path / ".commit-anatomy.toml",
        '[[rules]]\npaths = ["docs/manual/**"]\ncategory = "docs/user"\n',
    )
    cfg = load_config(tmp_path, None, resolve)
    ((rule, origin),) = cfg.rules
    assert rule.paths == ("docs/manual/**",) and rule.category == "docs/user"
    assert origin == ".commit-anatomy.toml" and rule.id == ".commit-anatomy.toml#1"
    assert cfg.files == (tmp_path / ".commit-anatomy.toml",)


def test_user_file_matched_by_path_comes_before_repo_file(tmp_path, user_repos):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(
        repo / ".commit-anatomy.toml",
        '[[rules]]\nid = "team"\npaths = ["docs/**"]\ncategory = "docs/dev"\n',
    )
    write(
        user_repos / "mine.toml",
        f'match = {{ path = "{repo}" }}\n'
        '[[rules]]\nid = "mine"\npaths = ["docs/**"]\ncategory = "docs/user"\n',
    )
    write(
        user_repos / "other.toml",
        'match = { path = "/elsewhere" }\n'
        '[[rules]]\nid = "other"\npaths = ["x"]\ncategory = "docs/user"\n',
    )
    cfg = load_config(repo, None, resolve)
    assert [r.id for r, _ in cfg.rules] == ["mine", "team"]
    assert cfg.rules[0][1] == "user:mine.toml"


def test_user_file_matched_by_remote(tmp_path, user_repos):
    write(
        user_repos / "ios.toml",
        'match = { remote = "github.com/Hrcek-app/hrcek-ios" }\n'
        '[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\n',
    )
    assert len(load_config(tmp_path, "git@github.com:Hrcek-app/hrcek-ios.git", resolve).rules) == 1
    assert load_config(tmp_path, None, resolve).rules == ()


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:Hrcek-app/hrcek-ios.git",
        "https://github.com/Hrcek-app/hrcek-ios.git",
        "ssh://git@github.com:22/Hrcek-app/hrcek-ios",
        "https://github.com/hrcek-app/hrcek-ios/",
        "github.com/Hrcek-app/hrcek-ios",
    ],
)
def test_normalize_remote(url):
    assert normalize_remote(url) == "github.com/hrcek-app/hrcek-ios"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ('[[rules]]\npaths = ["x"]\ncategory = "docs/nope"\n', "rules[0].category"),
        ('[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\ncolour = "red"\n', "rules[0].colour"),
        ('[[rules]]\ncontent = "("\ncategory = "docs/dev"\n', "rules[0].content"),
        ('[[rules]]\ncategory = "docs/dev"\n', "rules[0]"),
        (
            '[[rules]]\ncontent = "a"\ncategory = "docs/dev"\ncontent_lines = 21\n',
            "rules[0].content_lines",
        ),
        ('[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\nuntil = "nope"\n', "rules[0].until"),
        ('[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\nstage = "late"\n', "rules[0].stage"),
        ("surprise = 1\n", "surprise"),
        ('[intent.overrides]\nxyz = "fix"\n', "intent.overrides.xyz"),
        ('[intent.keywords]\nmagic = ["x"]\n', "intent.keywords.magic"),
        ('[taxonomy.docs]\ncolour = "red"\n', "taxonomy"),
        ('match = { path = "/x" }\n', "match"),
        ("[[rules]\n", "toml"),
    ],
)
def test_invalid_repo_config_names_file_and_field(tmp_path, body, field):
    write(tmp_path / ".commit-anatomy.toml", body)
    with pytest.raises(ConfigError) as error:
        load_config(tmp_path, None, resolve)
    assert str(error.value).startswith(f"{tmp_path / '.commit-anatomy.toml'}: {field}:")


def test_user_file_without_match_is_an_error(tmp_path, user_repos):
    write(user_repos / "x.toml", '[[rules]]\npaths = ["x"]\ncategory = "docs/dev"\n')
    with pytest.raises(ConfigError, match="match"):
        load_config(tmp_path, None, resolve)


def test_since_date_and_until_commit_are_resolved(tmp_path):
    write(
        tmp_path / ".commit-anatomy.toml",
        '[[rules]]\npaths = ["docs/**"]\ncategory = "docs/user"\n'
        'since = 2026-03-01\nuntil = "a1b2c3d"\n',
    )
    rule = load_config(tmp_path, None, resolve).rules[0][0]
    assert rule.since == "2026-03-01"
    assert rule.until == FULL


def test_taxonomy_and_intent_merge_with_user_winning(tmp_path, user_repos):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(
        repo / ".commit-anatomy.toml",
        '[taxonomy.docs]\nsubcategories = ["adr"]\n'
        '[intent.keywords]\nfeat = ["keep"]\n'
        '[intent.overrides]\nabcd = "fix"\n',
    )
    write(
        user_repos / "mine.toml",
        f'match = {{ path = "{repo}" }}\n'
        '[intent.keywords]\nfeat = ["save"]\n'
        '[[rules]]\npaths = ["docs/adr/**"]\ncategory = "docs/adr"\n',
    )
    cfg = load_config(repo, None, resolve)
    assert cfg.taxonomy.is_valid("docs/adr")
    assert cfg.intent.keywords["feat"] == ("save",)
    assert cfg.intent.keywords["fix"][0] == "fix*"  # untouched defaults stay
    assert cfg.intent.overrides == {"abcd": "fix"}
    assert cfg.rules[0][0].category == "docs/adr"
