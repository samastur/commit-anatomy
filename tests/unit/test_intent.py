import pytest

from commit_anatomy.intent import DEFAULT_KEYWORDS, INTENTS, classify_intent


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("feat: offer pictures a lazy loader has not swapped in yet", "feat"),
        ("fix(popup): spend only the queued copy", "fix"),
        ("refactor!: split the module", "refactor"),
        ("perf: faster lookups", "refactor"),
        ("ci: boot the simulator", "chore"),
        ("docs: explain setup", "docs"),
        ("Release 0.2.0", "release"),
        ("Fix crash when offline", "fix"),
        ("Let the entry picture take an API token", "feat"),
        ("Letter templates get a margin", "unknown"),  # "let" is an exact word, not a prefix
        ("Translate the user manual into Slovenian", "docs"),
        ("Keep saves Hrček could not take, and send them later", "unknown"),
        ("Show entries as a list or as cards\n\nBody text mentions fix.", "feat"),
        ("", "unknown"),
    ],
)
def test_intent_from_message(message, expected):
    assert classify_intent("abc123", message) == expected


def test_override_by_sha_prefix_wins():
    assert classify_intent("abcdef1234", "feat: x", overrides={"abcd": "fix"}) == "fix"
    assert classify_intent("ffff", "feat: x", overrides={"abcd": "fix"}) == "feat"


def test_custom_keywords_replace_a_default_list():
    keywords = {**DEFAULT_KEYWORDS, "feat": ("keep",)}
    assert classify_intent("a", "Keep saves for later", keywords=keywords) == "feat"
    assert classify_intent("a", "Add a button", keywords=keywords) == "unknown"


def test_every_default_keyword_intent_is_known():
    assert set(DEFAULT_KEYWORDS) <= set(INTENTS) - {"unknown"}
