from plugin_helpers import classify_paths

from commit_anatomy.model import Scope
from commit_anatomy.plugins.base import StaticRepoView
from commit_anatomy.plugins.core import plugin as core
from commit_anatomy.plugins.wxt import plugin as wxt

PACKAGE = '{"name": "x", "devDependencies": {"wxt": "^0.20.0"}}'

EXPECTED = {
    "package.json": "supporting/deps",
    "pnpm-lock.yaml": "supporting/deps",
    "pnpm-workspace.yaml": "supporting/deps",
    "src/entrypoints/popup/App.tsx": "code/app",
    "src/lib/queue.ts": "code/app",
    "src/lib/queue.test.ts": "tests/unit",
    "tests/e2e/save.spec.ts": "tests/e2e",
    "tests/contract/api.test.ts": "tests/integration",
    "tests/fake-hrcek/server.ts": "tests/support",
    "tests/entrypoints/popup.test.ts": "tests/unit",
    "src/locales/sl/messages.po": "supporting/translations",
    "wxt.config.ts": "supporting/build",
    "vitest.config.ts": "supporting/build",
    "tsconfig.json": "supporting/build",
    "eslint.config.js": "supporting/build",
    ".prettierrc": "supporting/build",
    "docs/superpowers/specs/s.md": "docs/llm",
    "README.md": "docs/dev",
}


def test_wxt_rules():
    files = {path: "" for path in EXPECTED} | {"package.json": PACKAGE}
    assert classify_paths([core, wxt], files) == EXPECTED


def test_detected_by_wxt_dependency_only():
    assert wxt.detect(StaticRepoView({"package.json": PACKAGE})) == [Scope("wxt", "")]
    assert wxt.detect(StaticRepoView({"package.json": '{"dependencies": {"react": "1"}}'})) == []
    assert wxt.detect(StaticRepoView({"package.json": "[1, 2]"})) == []
    assert wxt.detect(StaticRepoView({"package.json": "{broken"})) == []
