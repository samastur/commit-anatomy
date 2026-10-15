"""Generic rules that apply to every repository, with the lowest precedence."""

from ..model import Rule, Scope
from .base import RepoView, rule

GENERATED = (
    r"(?i)(@generated\b|\bauto-?generated\b"
    r"|\bgenerated\b.*\bdo not edit\b|\bdo not edit\b.*\bgenerated\b)"
)

RULES = [
    rule("core:generated", "supporting/generated", content=GENERATED, stage="pre"),
    rule(
        "core:llm-docs",
        "docs/llm",
        "**/CLAUDE.md",
        "**/AGENTS.md",
        "REVIEW_GUIDE.md",
        "docs/superpowers/**",
    ),
    rule("core:changelog", "docs/changelog", "**/CHANGELOG*"),
    rule("core:ci", "supporting/ci", ".github/**", ".pre-commit-config.yaml", ".gitlab-ci.yml"),
    rule("core:tooling", "code/tooling", "scripts/**", ".githooks/**"),
    rule(
        "core:build",
        "supporting/build",
        "LICENSE*",
        "**/.gitignore",
        ".dockerignore",
        ".gitleaks.toml",
        "Dockerfile",
        "docker/**",
        "deploy/**",
        ".*rc",
        ".editorconfig",
    ),
    rule(
        "core:assets",
        "supporting/assets",
        "**/*.{png,jpg,jpeg,gif,svg,webp,avif,ico,pdf,ttf,otf,woff,woff2}",
    ),
    rule("core:docs", "docs/dev", "docs/**", "**/*.{md,rst}"),
]


class CorePlugin:
    name = "core"

    def detect(self, repo: RepoView) -> list[Scope]:
        return [Scope(self.name, "")]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {}


plugin = CorePlugin()
