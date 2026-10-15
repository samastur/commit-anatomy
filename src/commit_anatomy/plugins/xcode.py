"""Xcode / Swift Package Manager projects: detected by *.xcodeproj or Package.swift."""

from ..model import Rule, Scope
from .base import RepoView, rule

RULES = [
    rule("xcode:ui-tests", "tests/e2e", "**/*UITests/**"),
    rule("xcode:tests", "tests/unit", "**/Tests/**", "**/*Tests/**"),
    rule("xcode:test-plans", "tests/support", "**/*.xctestplan"),
    rule("xcode:translations", "supporting/translations", "**/*.{xcstrings,strings,stringsdict}"),
    rule(
        "xcode:build",
        "supporting/build",
        "**/*.xcodeproj/**",
        "**/*.xcworkspace/**",
        "**/*.xcconfig",
        "**/*.entitlements",
        "**/Info.plist",
        "Config/**",
        ".swiftlint.yml",
        ".swift-format",
    ),
    rule(
        "xcode:deps",
        "supporting/deps",
        "**/Package.swift",
        "**/Package.resolved",
        "Brewfile",
        "Brewfile.lock.json",
        "Gemfile",
        "Gemfile.lock",
        "Podfile",
        "Podfile.lock",
    ),
    rule("xcode:assets", "supporting/assets", "**/*.xcassets/**"),
    rule(
        "xcode:app",
        "code/app",
        "**/*.swift",
        "**/*.{m,mm,h}",
        "Support/**",
        # JavaScript preprocessing files of share / action extensions
        # (NSExtensionJavaScriptPreprocessingFile) sit in the extension target.
        "**/*{Extension,Share,Action}/**/*.js",
    ),
]


class XcodePlugin:
    name = "xcode"

    def detect(self, repo: RepoView) -> list[Scope]:
        roots = []
        for path in repo.files():
            parts = path.split("/")
            for i, part in enumerate(parts[:-1]):
                if part.endswith(".xcodeproj"):
                    roots.append("/".join(parts[:i]))
                    break
            if parts[-1] == "Package.swift":
                roots.append("/".join(parts[:-1]))
        return [Scope(self.name, root) for root in dict.fromkeys(roots)]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {}


plugin = XcodePlugin()
