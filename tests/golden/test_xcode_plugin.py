from plugin_helpers import classify_paths

from commit_anatomy.model import Scope
from commit_anatomy.plugins.base import StaticRepoView
from commit_anatomy.plugins.core import plugin as core
from commit_anatomy.plugins.xcode import plugin as xcode

EXPECTED = {
    "Hrcek.xcodeproj/project.pbxproj": "supporting/build",
    "Hrcek.xcodeproj/xcshareddata/xcschemes/Hrcek.xcscheme": "supporting/build",
    "HrcekKit/Package.swift": "supporting/deps",
    "HrcekKit/Package.resolved": "supporting/deps",
    "HrcekKit/Sources/HrcekKit/Queue.swift": "code/app",
    "HrcekKit/Tests/HrcekKitTests/QueueTests.swift": "tests/unit",
    "Hrcek/ContentView.swift": "code/app",
    "HrcekShare/ShareView.swift": "code/app",
    "HrcekShare/Preprocess.js": "code/app",
    "PhotoAction/Action.js": "code/app",
    "scripts/e2e-page/app.js": "code/tooling",
    "HrcekTests/AppTests.swift": "tests/unit",
    "HrcekUITests/SaveTests.swift": "tests/e2e",
    "Hrcek.xctestplan": "tests/support",
    "Hrcek/Localizable.xcstrings": "supporting/translations",
    "Config/Base.xcconfig": "supporting/build",
    ".swiftlint.yml": "supporting/build",
    "Support/Info.plist": "supporting/build",
    "Brewfile": "supporting/deps",
    "Hrcek/Assets.xcassets/AppIcon.appiconset/Contents.json": "supporting/assets",
    "scripts/ci.sh": "code/tooling",
    "AGENTS.md": "docs/llm",
}


def test_xcode_rules():
    assert classify_paths([core, xcode], {path: "" for path in EXPECTED}) == EXPECTED


def test_detects_project_and_nested_package_scopes():
    view = StaticRepoView({"Hrcek.xcodeproj/project.pbxproj": "", "HrcekKit/Package.swift": ""})
    assert xcode.detect(view) == [Scope("xcode", ""), Scope("xcode", "HrcekKit")]
    assert xcode.detect(StaticRepoView({"README.md": ""})) == []
