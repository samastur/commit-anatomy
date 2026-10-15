from datetime import datetime

from commit_anatomy.classify import Classifier, RuleEntry, Windows, order_rules
from commit_anatomy.model import Commit, Rule, Scope
from commit_anatomy.taxonomy import Taxonomy


def commit(sha="c1", date="2026-05-01T00:00:00+00:00"):
    when = datetime.fromisoformat(date)
    return Commit(sha, (), "T", "t@example.com", when, when, "msg")


def make(entries, heads=None, ancestors=frozenset(), taxonomy=None):
    windows = Windows(lambda a, b: (a, b) in ancestors)
    return Classifier(
        entries, taxonomy or Taxonomy(), windows, head=lambda blob: (heads or {}).get(blob, [])
    )


def test_first_match_wins_and_reports_the_pattern():
    c = make(
        [
            RuleEntry(Rule("a", "docs/user", ("docs/manual/**",)), "cfg"),
            RuleEntry(Rule("b", "docs/dev", ("docs/**",)), "cfg"),
        ]
    )
    m = c.classify("docs/manual/en/x.md", None, commit())
    assert (m.category, m.rule_id, m.origin, m.matched) == (
        "docs/user",
        "a",
        "cfg",
        "docs/manual/**",
    )
    assert c.classify("docs/dev/y.md", None, commit()).category == "docs/dev"


def test_fallback_is_unclassified():
    m = make([]).classify("x.bin", None, commit())
    assert (m.category, m.rule_id, m.origin) == ("unclassified", "fallback", "none")


def test_globstar_matches_zero_directories_and_dotfiles():
    c = make([RuleEntry(Rule("md", "docs/dev", ("**/*.md", ".*rc")), "cfg")])
    assert c.classify("README.md", None, commit()).category == "docs/dev"
    assert c.classify("a/b/c.md", None, commit()).category == "docs/dev"
    assert c.classify(".prettierrc", None, commit()).category == "docs/dev"


def test_scoped_rule_only_matches_inside_its_root():
    c = make([RuleEntry(Rule("t", "tests/unit", ("Tests/**",)), "plugin:xcode@Kit", "Kit")])
    m = c.classify("Kit/Tests/A.swift", None, commit())
    assert (m.category, m.root) == ("tests/unit", "Kit")
    assert c.classify("Tests/A.swift", None, commit()).category == "unclassified"
    assert c.classify("KitX/Tests/A.swift", None, commit()).category == "unclassified"


def test_content_rule_reads_only_content_lines_of_the_head():
    gen = Rule("g", "supporting/generated", content=r"@generated", content_lines=2)
    c = make(
        [RuleEntry(gen, "core")],
        heads={"b1": ["x", "// @generated"], "b2": ["x", "y", "// @generated"]},
    )
    m = c.classify("a.ts", "b1", commit())
    assert (m.category, m.matched) == ("supporting/generated", "content:@generated")
    assert c.classify("a.ts", "b2", commit()).category == "unclassified"
    assert c.classify("a.ts", None, commit()).category == "unclassified"  # no blob: binary


def test_paths_and_content_must_both_match():
    r = Rule("g", "supporting/generated", ("src/**",), content="DO NOT EDIT")
    c = make([RuleEntry(r, "cfg")], heads={"b": ["// DO NOT EDIT"]})
    assert c.classify("src/a.ts", "b", commit()).matched == "src/** + content:DO NOT EDIT"
    assert c.classify("lib/a.ts", "b", commit()).category == "unclassified"


def test_date_window_since_inclusive_until_exclusive():
    r = Rule("w", "docs/user", ("docs/**",), since="2026-03-01", until="2026-06-01")
    c = make([RuleEntry(r, "cfg")])
    at = lambda d: c.classify("docs/a.md", None, commit(date=d)).category  # noqa: E731
    assert at("2026-02-28T23:59:59+00:00") == "unclassified"
    assert at("2026-03-01T00:00:00+00:00") == "docs/user"
    assert at("2026-06-01T00:00:00+00:00") == "unclassified"


def test_commit_window_uses_ancestry():
    r = Rule("w", "docs/user", ("docs/**",), until="X")
    c = make([RuleEntry(r, "cfg")], ancestors={("X", "after")})
    assert c.classify("docs/a.md", None, commit(sha="before")).category == "docs/user"
    assert c.classify("docs/a.md", None, commit(sha="after")).category == "unclassified"


def test_remap_applies_to_every_match():
    taxonomy = Taxonomy().extended(
        {"translations": {}, "remap": {"supporting/translations": "translations"}}
    )
    c = make(
        [RuleEntry(Rule("l", "supporting/translations", ("locale/**",)), "p")],
        taxonomy=taxonomy,
    )
    assert c.classify("locale/sl/x.po", None, commit()).category == "translations"


def test_order_rules_config_then_pre_then_deepest_scope_then_core():
    core, dj, kit = Scope("core", ""), Scope("django", ""), Scope("xcode", "Kit")
    pre = Rule("core:gen", "supporting/generated", content="x", stage="pre")
    entries = order_rules(
        [(Rule("u", "docs/dev", ("a",)), "user:x.toml")],
        [
            (core, [pre, Rule("core:docs", "docs/dev", ("docs/**",))]),
            (dj, [Rule("dj", "code/app", ("src/**",))]),
            (kit, [Rule("kit", "tests/unit", ("Tests/**",))]),
        ],
    )
    assert [e.rule.id for e in entries] == ["u", "core:gen", "kit", "dj", "core:docs"]
    assert (entries[2].origin, entries[2].root) == ("plugin:xcode@Kit", "Kit")
    assert entries[0].origin == "user:x.toml"
