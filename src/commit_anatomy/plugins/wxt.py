"""WXT browser extensions: detected by a wxt dependency in package.json."""

import json
import posixpath

from ..model import Rule, Scope
from .base import RepoView, rule

RULES = [
    rule("wxt:e2e", "tests/e2e", "tests/e2e/**"),
    rule("wxt:integration", "tests/integration", "tests/contract/**", "tests/integration/**"),
    rule(
        "wxt:test-support",
        "tests/support",
        "tests/fake-*/**",
        "tests/**/fixtures/**",
        "tests/helpers/**",
    ),
    rule("wxt:tests", "tests/unit", "tests/**"),
    rule("wxt:colocated-tests", "tests/unit", "**/*.{test,spec}.{ts,tsx,js,jsx,mts}"),
    rule(
        "wxt:deps",
        "supporting/deps",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "package-lock.json",
        "yarn.lock",
        ".npmrc",
        ".nvmrc",
    ),
    rule("wxt:translations", "supporting/translations", "**/locales/**", "**/*.po"),
    rule(
        "wxt:build",
        "supporting/build",
        "*.config.{ts,js,mjs,cjs}",
        "tsconfig*.json",
        "eslint.config.*",
        ".prettierrc*",
        ".prettierignore",
    ),
    rule("wxt:generated", "supporting/generated", ".wxt/**", ".output/**"),
    rule("wxt:app", "code/app", "src/**", "assets/**/*.{css,ts}", "public/**/*.{css,js,html}"),
]


def _depends_on_wxt(content: bytes) -> bool:
    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return any(
        isinstance(data.get(key), dict) and "wxt" in data[key]
        for key in ("dependencies", "devDependencies")
    )


class WxtPlugin:
    name = "wxt"

    def detect(self, repo: RepoView) -> list[Scope]:
        roots = [
            posixpath.dirname(path)
            for path in repo.files()
            if posixpath.basename(path) == "package.json"
            and "node_modules/" not in path
            and _depends_on_wxt(repo.read(path) or b"")
        ]
        return [Scope(self.name, root) for root in dict.fromkeys(roots)]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {}


plugin = WxtPlugin()
