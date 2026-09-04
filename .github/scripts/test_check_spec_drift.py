# .github/scripts/test_check_spec_drift.py
"""
check_spec_drift.py の回帰テスト。

Issue #402 以前は、本テストが実リポジトリの状態(例: ai_logic.md / bounty_router.md
が孤立ドキュメントとして残っていること)を前提に assert していたため、
「孤立ドキュメントを片付けると回帰テストが壊れる」という逆インセンティブが
生じていた。現在は tmp_path 上に git 管理された疑似リポジトリを組み立て、
REPO_ROOT / SPEC_ROOT を monkeypatch して検証する(実リポジトリの内容には
一切依存しない)。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
ローカルでは `python -m pytest .github/scripts/ -q` で実行できる。

各テストが検証している Issue:
  - #105: フラット命名規約の同名stem衝突(dashboard_common.md の disambiguation)
  - #124: DDD直下にフラット配置された test_*.py の除外
  - #188: ソース削除済み仕様書の孤立ドキュメント検知
  - #283: family-quest の Vitest テスト(*.test.ts / src/test/)の除外
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent / "check_spec_drift.py"

_spec = importlib.util.spec_from_file_location("check_spec_drift", SCRIPT_PATH)
module = importlib.util.module_from_spec(_spec)
sys.modules["check_spec_drift"] = module
_spec.loader.exec_module(module)  # type: ignore[union-attr]


# --- 疑似リポジトリのヘルパー ---

def _git(repo: Path, *args: str, date: str | None = None) -> str:
    """疑似リポジトリ上で git を実行する。date は author/committer 日時(epoch秒等)。"""
    env = dict(os.environ)
    # ローカルの ~/.gitconfig (署名設定・フック等)に影響されないよう固定する
    env.update({
        "GIT_AUTHOR_NAME": "spec-drift-test",
        "GIT_AUTHOR_EMAIL": "spec-drift-test@example.com",
        "GIT_COMMITTER_NAME": "spec-drift-test",
        "GIT_COMMITTER_EMAIL": "spec-drift-test@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    })
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


def _write(repo: Path, rel: str, content: str = "") -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or f"# {rel}\n", encoding="utf-8")
    return path


def _commit_all(repo: Path, message: str, date: str | None = None) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message, date=date)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def pseudo_repo(tmp_path, monkeypatch):
    """実リポジトリの規約を最小限に再現した疑似リポジトリ(git初期化・1コミット済み)。

    - MY_HOME_SYSTEM/common.py と views/dashboard/common.py の同名stem衝突(#105)
    - DDD直下の test_*.py と、その仕様書 test_*.md(#124)
    - family-quest の *.test.ts / src/test/ 配下(#283)
    - ソースが存在しない仕様書 ai_logic.md / bounty_router.md(#188)
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    # MY_HOME_SYSTEM
    _write(repo, "MY_HOME_SYSTEM/common.py")
    _write(repo, "MY_HOME_SYSTEM/views/dashboard/common.py")
    _write(repo, "MY_HOME_SYSTEM/views/dashboard/__init__.py")
    _write(repo, "MY_HOME_SYSTEM/services/foo_service.py")
    _write(repo, "MY_HOME_SYSTEM/start_all.sh")
    _write(repo, "MY_HOME_SYSTEM/tests/test_backup_service.py")
    _write(repo, "docs/specifications/MY_HOME_SYSTEM/README.md")
    _write(repo, "docs/specifications/MY_HOME_SYSTEM/common.md")
    _write(repo, "docs/specifications/MY_HOME_SYSTEM/dashboard_common.md")
    _write(repo, "docs/specifications/MY_HOME_SYSTEM/foo_service.md")
    _write(repo, "docs/specifications/MY_HOME_SYSTEM/start_all.md")
    # 対応ソースが存在しない仕様書(#188)
    _write(repo, "docs/specifications/MY_HOME_SYSTEM/ai_logic.md")
    _write(repo, "docs/specifications/MY_HOME_SYSTEM/bounty_router.md")

    # DDD (tests/ ディレクトリ無し、test_*.py をフラット配置)
    _write(repo, "DDD/extract_youtube_urls.py")
    _write(repo, "DDD/test_newface_monitor_lock.py")
    _write(repo, "DDD/test_newface_monitor_datamanager.py")
    _write(repo, "docs/specifications/DDD/README.md")
    _write(repo, "docs/specifications/DDD/extract_youtube_urls.md")
    _write(repo, "docs/specifications/DDD/test_newface_monitor_lock.md")

    # family-quest
    _write(repo, "family-quest/src/App.tsx")
    _write(repo, "family-quest/src/lib/utils.ts")
    _write(repo, "family-quest/src/lib/utils.test.ts")
    _write(repo, "family-quest/src/hooks/useOnlineStatus.ts")
    _write(repo, "family-quest/src/hooks/useOnlineStatus.test.ts")
    _write(repo, "family-quest/src/features/quest/hooks/useQuestStatus.test.ts")
    _write(repo, "family-quest/src/test/setup.ts")
    _write(repo, "family-quest/src/vite-env.d.ts")
    _write(repo, "docs/specifications/family-quest/README.md")
    _write(repo, "docs/specifications/family-quest/App.md")
    _write(repo, "docs/specifications/family-quest/src/lib/utils.md")
    _write(repo, "docs/specifications/family-quest/src/hooks/useOnlineStatus.md")

    _write(repo, "docs/specifications/全体設計書.md")
    _write(repo, "docs/specifications/README.md")

    _commit_all(repo, "initial", date="1700000000 +0000")

    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "SPEC_ROOT", repo / "docs" / "specifications")
    return repo


# --- Issue #105の回帰テスト ---
#
# フラット命名規約(MY_HOME_SYSTEM/**/name.py → docs/specifications/MY_HOME_SYSTEM/name.md)
# はサブディレクトリをまたいで同名stemが存在する場合(MY_HOME_SYSTEM/common.py と
# MY_HOME_SYSTEM/views/dashboard/common.py)に衝突する。修正前は両方が common.md に
# マップされ、手動で作成された disambiguation 名の仕様書 dashboard_common.md は
# 一切対応付けられず、常に検知対象外になっていた。


def test_nested_common_py_maps_to_disambiguated_doc_first(pseudo_repo):
    """views/dashboard/common.py は、フラット規約の common.md ではなく
    dashboard_common.md を最有力候補として返すこと。"""
    candidates = module.source_to_doc_candidates(Path("MY_HOME_SYSTEM/views/dashboard/common.py"))

    assert candidates[0] == module.SPEC_ROOT / "MY_HOME_SYSTEM" / "dashboard_common.md"
    assert module.SPEC_ROOT / "MY_HOME_SYSTEM" / "common.md" in candidates

    existing = [c for c in candidates if c.exists()]
    assert existing and existing[0].name == "dashboard_common.md", (
        "views/dashboard/common.py の変更が誤って common.md 側のドリフトとして"
        "扱われてしまう(=Issue #105の再発)。"
    )


def test_top_level_common_py_still_maps_to_flat_doc_name(pseudo_repo):
    """ソースディレクトリ直下のファイルは disambiguation 名を試みず、
    従来どおりフラットな <stem>.md のみを候補とすること。"""
    candidates = module.source_to_doc_candidates(Path("MY_HOME_SYSTEM/common.py"))
    assert candidates == [module.SPEC_ROOT / "MY_HOME_SYSTEM" / "common.md"]


def test_dashboard_common_doc_resolves_back_to_nested_source(pseudo_repo):
    """doc_to_source_candidates(dashboard_common.md) が views/dashboard/common.py を
    候補として返すこと(修正前は候補0件で孤立判定からも除外されていた)。"""
    candidates = module.doc_to_source_candidates(
        module.SPEC_ROOT / "MY_HOME_SYSTEM" / "dashboard_common.md"
    )
    expected = Path("MY_HOME_SYSTEM/views/dashboard/common.py")
    assert expected in candidates
    assert any((module.REPO_ROOT / c).exists() for c in candidates)


def test_plain_common_doc_does_not_gain_extra_matches_from_disambiguation_logic(pseudo_repo):
    """common.md のstemにはアンダースコアが無いため disambiguation 探索は発火せず、
    素のstem探索で見つかる common.py 群だけが候補になること。"""
    candidates = module.doc_to_source_candidates(module.SPEC_ROOT / "MY_HOME_SYSTEM" / "common.md")
    assert set(candidates) == {
        Path("MY_HOME_SYSTEM/common.py"),
        Path("MY_HOME_SYSTEM/views/dashboard/common.py"),
    }


def test_full_audit_does_not_flag_dashboard_common_doc_as_orphaned(pseudo_repo):
    report = module.cmd_full()
    assert "dashboard_common.md" not in "\n".join(report.orphaned)


# --- Issue #124の回帰テスト ---
#
# DDDには tests/ ディレクトリが無く、DDD直下に test_*.py をフラット配置する規約に
# なっている。ディレクトリ名だけでは検知できず、is_tracked_source が DDD の
# test_*.py を「仕様書があるべきソース」と誤認して偽陽性を報告していた。


def test_ddd_flat_test_file_is_not_a_tracked_source():
    assert module.is_tracked_source(Path("DDD/test_newface_monitor_datamanager.py")) is False


def test_my_home_system_nested_test_file_is_still_not_tracked():
    assert module.is_tracked_source(Path("MY_HOME_SYSTEM/tests/test_backup_service.py")) is False


def test_non_test_ddd_source_file_is_still_tracked():
    assert module.is_tracked_source(Path("DDD/extract_youtube_urls.py")) is True


def test_doc_to_source_candidates_still_resolves_existing_test_doc_to_its_source(pseudo_repo):
    """is_test_file による除外は is_tracked_source 側のみに適用され、
    doc_to_source_candidates(孤立判定に使う逆引き)には影響しないこと。"""
    candidates = module.doc_to_source_candidates(
        module.SPEC_ROOT / "DDD" / "test_newface_monitor_lock.md"
    )
    assert Path("DDD/test_newface_monitor_lock.py") in candidates
    assert any((module.REPO_ROOT / c).exists() for c in candidates)


def test_full_audit_does_not_flag_ddd_test_files_as_undocumented(pseudo_repo):
    report = module.cmd_full()
    assert "DDD/test_" not in "\n".join(report.undocumented)


def test_full_audit_does_not_flag_existing_ddd_test_docs_as_orphaned(pseudo_repo):
    report = module.cmd_full()
    assert "DDD/test_" not in "\n".join(report.orphaned)


# --- Issue #188の回帰テスト ---
#
# doc_to_source_candidates は MY_HOME_SYSTEM/DDD 側では実在するファイルのみを候補と
# して返していたため、ソースが本当に削除された仕様書では候補が常に空リストになり、
# cmd_full() の `if candidates and not any(exists)` 判定がスキップされて孤立
# ドキュメントとして永久に検知されなかった。


def test_doc_to_source_candidates_returns_nonempty_for_source_that_no_longer_exists(pseudo_repo):
    candidates = module.doc_to_source_candidates(module.SPEC_ROOT / "MY_HOME_SYSTEM" / "ai_logic.md")
    assert candidates, "対応ソースが存在しない仕様書でも候補は空であってはならない"
    assert not any((module.REPO_ROOT / c).exists() for c in candidates)


def test_full_audit_detects_orphaned_py_docs_with_no_matching_source(pseudo_repo):
    report = module.cmd_full()
    orphaned_str = "\n".join(report.orphaned)
    assert "MY_HOME_SYSTEM/ai_logic.md" in orphaned_str
    assert "MY_HOME_SYSTEM/bounty_router.md" in orphaned_str


def test_full_audit_is_clean_once_orphaned_docs_are_removed(pseudo_repo):
    """孤立ドキュメントを削除すれば検知事項なしになること(Issue #402: 実リポジトリの
    孤立ドキュメント整理がテストを壊さないことの確認)。"""
    (module.SPEC_ROOT / "MY_HOME_SYSTEM" / "ai_logic.md").unlink()
    (module.SPEC_ROOT / "MY_HOME_SYSTEM" / "bounty_router.md").unlink()
    _commit_all(pseudo_repo, "remove orphaned docs", date="1700000100 +0000")

    report = module.cmd_full()
    assert report.orphaned == []
    assert report.undocumented == []
    assert report.stale == []


# --- Issue #283の回帰テスト ---
#
# family-quest の Vitest テスト(*.test.ts、src/test/ 配下)は "tests" ディレクトリ名にも
# test_*.py 命名にも一致しないため、「仕様書が見つからないファイル」として誤検知されていた。


def test_test_ts_suffix_file_is_not_a_tracked_source():
    assert module.is_tracked_source(Path("family-quest/src/hooks/useOnlineStatus.test.ts")) is False


def test_nested_test_ts_suffix_file_is_not_a_tracked_source():
    assert module.is_tracked_source(Path("family-quest/src/features/quest/hooks/useQuestStatus.test.ts")) is False


def test_src_test_dir_file_is_not_a_tracked_source():
    assert module.is_tracked_source(Path("family-quest/src/test/setup.ts")) is False


def test_non_test_fq_source_file_is_still_tracked():
    assert module.is_tracked_source(Path("family-quest/src/lib/utils.ts")) is True


def test_full_audit_does_not_flag_family_quest_test_files_as_undocumented(pseudo_repo):
    report = module.cmd_full()
    undocumented_str = "\n".join(report.undocumented)
    assert "useOnlineStatus.test.ts" not in undocumented_str
    assert "useQuestStatus.test.ts" not in undocumented_str
    assert "src/test/setup.ts" not in undocumented_str
    assert "utils.test.ts" not in undocumented_str
    assert "vite-env.d.ts" not in undocumented_str


# --- pr モードの基本動作 ---


def test_pr_mode_reports_stale_doc_when_only_source_changed(pseudo_repo):
    base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
    _write(pseudo_repo, "MY_HOME_SYSTEM/services/foo_service.py", "# changed\n")
    head = _commit_all(pseudo_repo, "change source only", date="1700000200 +0000")

    report = module.cmd_pr(base, head)
    assert any("foo_service.py" in s and "foo_service.md" in s for s in report.stale)
    assert report.undocumented == []


def test_pr_mode_is_clean_when_doc_updated_alongside_source(pseudo_repo):
    base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
    _write(pseudo_repo, "MY_HOME_SYSTEM/services/foo_service.py", "# changed\n")
    _write(pseudo_repo, "docs/specifications/MY_HOME_SYSTEM/foo_service.md", "# updated\n")
    head = _commit_all(pseudo_repo, "change source and doc", date="1700000200 +0000")

    report = module.cmd_pr(base, head)
    assert report.is_empty()


def test_pr_mode_reports_new_source_without_doc_as_undocumented(pseudo_repo):
    base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
    _write(pseudo_repo, "MY_HOME_SYSTEM/services/new_service.py")
    head = _commit_all(pseudo_repo, "add new source", date="1700000200 +0000")

    report = module.cmd_pr(base, head)
    assert report.undocumented == ["MY_HOME_SYSTEM/services/new_service.py"]


def test_pr_mode_reports_deleted_source_whose_doc_was_left_untouched(pseudo_repo):
    base = _git(pseudo_repo, "rev-parse", "HEAD").strip()
    (pseudo_repo / "MY_HOME_SYSTEM/services/foo_service.py").unlink()
    head = _commit_all(pseudo_repo, "delete source", date="1700000200 +0000")

    report = module.cmd_pr(base, head)
    assert any("foo_service.py が削除されました" in s for s in report.orphaned)


# --- full モードのドリフト判定(コミット日時ベース) ---


def test_full_audit_reports_stale_when_source_committed_after_doc(pseudo_repo):
    _write(pseudo_repo, "MY_HOME_SYSTEM/services/foo_service.py", "# newer\n")
    _commit_all(pseudo_repo, "source newer than doc", date="1700086400 +0000")  # +1日

    report = module.cmd_full()
    assert any(
        s.startswith("MY_HOME_SYSTEM/services/foo_service.py") and "foo_service.md" in s
        for s in report.stale
    )
