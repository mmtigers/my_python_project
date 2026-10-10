# .github/scripts/test_check_changelog_update.py
"""check_changelog_update.py の回帰テスト(純粋関数のみ。git には依存しない)。"""
import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent / "check_changelog_update.py"
_spec = importlib.util.spec_from_file_location("check_changelog_update", SCRIPT_PATH)
module = importlib.util.module_from_spec(_spec)
sys.modules["check_changelog_update"] = module
_spec.loader.exec_module(module)  # type: ignore[union-attr]

CHANGELOG = "family-quest/src/lib/changelog.json"


def test_app_source_change_without_changelog_is_flagged():
    files = ["family-quest/src/App.tsx", "docs/specifications/README.md"]
    assert module.find_missing(files) == ["family-quest/src/App.tsx"]


def test_changelog_updated_clears_the_flag():
    assert module.find_missing(["family-quest/src/App.tsx", CHANGELOG]) == []


def test_tests_types_and_non_frontend_files_are_ignored():
    files = [
        "family-quest/src/App.test.tsx",
        "family-quest/src/test/setup.ts",
        "family-quest/src/vite-env.d.ts",
        "family-quest/src/index.css",
        "family-quest/package.json",
        "MY_HOME_SYSTEM/config.py",
    ]
    assert module.find_missing(files) == []


def test_changelog_alone_is_not_flagged():
    assert module.find_missing([CHANGELOG]) == []


def test_report_lists_files_only_when_missing():
    assert "検知事項なし" in module.build_report([])
    report = module.build_report(["family-quest/src/App.tsx"])
    assert "`family-quest/src/App.tsx`" in report
    assert "ブロッキングではありません" in report
