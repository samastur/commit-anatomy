import pytest

from commit_anatomy.taxonomy import Taxonomy, TaxonomyError


def test_default_categories_are_valid():
    t = Taxonomy()
    assert t.is_valid("docs/llm")
    assert t.is_valid("supporting/translations")
    assert t.is_valid("unclassified")
    assert not t.is_valid("docs")  # docs has subcategories, so a bare top is not a category
    assert not t.is_valid("docs/nope")
    assert not t.is_valid("nope/x")


def test_split_lines_only_for_tests_and_code():
    t = Taxonomy()
    assert t.split_lines("tests/unit") and t.split_lines("code/app") and t.split_lines("code")
    assert not t.split_lines("docs/dev")
    assert not t.split_lines("supporting/deps")
    assert not t.split_lines("unclassified")


def test_extend_adds_subcategory_and_top_before_unclassified():
    t = Taxonomy().extended(
        {
            "docs": {"subcategories": ["adr"]},
            "infra": {"subcategories": ["terraform"], "split_lines": True},
        }
    )
    assert t.is_valid("docs/adr") and t.is_valid("docs/user")
    assert t.is_valid("infra/terraform") and t.split_lines("infra/terraform")
    assert t.tops[-1] == "unclassified"
    assert t.tops.index("infra") == t.tops.index("unclassified") - 1


def test_remap_promotes_translations():
    t = Taxonomy().extended(
        {"translations": {}, "remap": {"supporting/translations": "translations"}}
    )
    assert t.remap("supporting/translations") == "translations"
    assert t.remap("docs/dev") == "docs/dev"


def test_remap_target_must_exist():
    with pytest.raises(TaxonomyError, match="unknown target"):
        Taxonomy().extended({"remap": {"supporting/translations": "translations"}})


def test_remap_source_must_exist():
    with pytest.raises(TaxonomyError, match="remap.docs/nope"):
        Taxonomy().extended({"remap": {"docs/nope": "docs/dev"}})


def test_unknown_field_rejected():
    with pytest.raises(TaxonomyError, match="docs.colour"):
        Taxonomy().extended({"docs": {"colour": "red"}})


def test_unclassified_cannot_be_changed():
    with pytest.raises(TaxonomyError, match="unclassified"):
        Taxonomy().extended({"unclassified": {"subcategories": ["x"]}})


def test_order_key_follows_taxonomy_order():
    t = Taxonomy()
    cats = ["supporting/deps", "docs/llm", "unclassified", "code/app", "docs/user"]
    assert sorted(cats, key=t.order_key) == [
        "docs/user",
        "docs/llm",
        "code/app",
        "supporting/deps",
        "unclassified",
    ]
