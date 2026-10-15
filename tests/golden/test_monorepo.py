from plugin_helpers import classify_paths

from commit_anatomy.plugins.core import plugin as core
from commit_anatomy.plugins.django import plugin as django
from commit_anatomy.plugins.wxt import plugin as wxt


def test_plugin_rules_stay_inside_their_scope():
    files = {
        "backend/manage.py": "",
        "backend/src/app/views.py": "",
        "backend/docs/manual/a.md": "",
        "extension/package.json": '{"devDependencies": {"wxt": "1"}}',
        "extension/src/a.test.ts": "",
        "extension/tests/e2e/x.ts": "",
        "templates/x.html": "",  # outside every plugin scope
        "tests/x.py": "",  # outside every plugin scope, but core's tests rule is generic
        "docs/manual/a.md": "",  # docs/manual means docs/user only inside the django scope
    }
    assert classify_paths([core, django, wxt], files) == {
        "backend/manage.py": "code/app",
        "backend/src/app/views.py": "code/app",
        "backend/docs/manual/a.md": "docs/user",
        "extension/package.json": "supporting/deps",
        "extension/src/a.test.ts": "tests/unit",
        "extension/tests/e2e/x.ts": "tests/e2e",
        "templates/x.html": "unclassified",
        "tests/x.py": "tests/unit",
        "docs/manual/a.md": "docs/dev",
    }
