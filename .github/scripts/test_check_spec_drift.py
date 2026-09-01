# .github/scripts/test_check_spec_drift.py
"""
Issue #105の回帰テスト。

check_spec_drift.py のソース⇔仕様書対応付けはフラット命名規約
(MY_HOME_SYSTEM/**/name.py → docs/specifications/MY_HOME_SYSTEM/name.md、
サブディレクトリ構造は畳まれる)だが、これはサブディレクトリをまたいで
同名stemが存在する場合(例: MY_HOME_SYSTEM/common.py と
MY_HOME_SYSTEM/views/dashboard/common.py)に衝突する。修正前は両方が
common.md にマップされ、views/dashboard/common.py 用に手動で作成された
disambiguation名の仕様書 docs/specifications/MY_HOME_SYSTEM/dashboard_common.md
は(逆方向の探索が stem "dashboard_common" のファイルを探すだけだったため)
一切対応付けられず、常に検知対象外になっていた。

本ファイルは `.github/scripts/` に置かれた通常のPythonスクリプト用の
回帰テストであり、MY_HOME_SYSTEM/tests・DDDいずれのpytest実行対象にも
含まれない。`pytest .github/scripts/test_check_spec_drift.py` のように
直接指定して実行する。
"""
import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent / "check_spec_drift.py"

_spec = importlib.util.spec_from_file_location("check_spec_drift", SCRIPT_PATH)
module = importlib.util.module_from_spec(_spec)
sys.modules["check_spec_drift"] = module
_spec.loader.exec_module(module)  # type: ignore[union-attr]


def test_nested_common_py_maps_to_disambiguated_doc_first():
    """
    MY_HOME_SYSTEM/views/dashboard/common.py は、フラット規約の
    common.md ではなく、実在する dashboard_common.md を最有力候補として
    返すこと(=既存のdashboard_common.mdが最初に選ばれること)を確認する。
    """
    rel_path = Path("MY_HOME_SYSTEM/views/dashboard/common.py")
    candidates = module.source_to_doc_candidates(rel_path)

    assert candidates[0] == module.SPEC_ROOT / "MY_HOME_SYSTEM" / "dashboard_common.md"
    assert module.SPEC_ROOT / "MY_HOME_SYSTEM" / "common.md" in candidates

    existing = [c for c in candidates if c.exists()]
    assert existing, "候補となる仕様書がリポジトリ上に1つも存在しない"
    assert existing[0].name == "dashboard_common.md", (
        f"existing[0]={existing[0].name} だった。"
        "views/dashboard/common.py の変更が誤って common.md 側のドリフトとして"
        "扱われてしまう(=Issue #105の再発)。"
    )


def test_top_level_common_py_still_maps_to_flat_doc_name():
    """
    回帰防止: ソースディレクトリ直下(2階層に満たない)のファイルは、
    disambiguation名を試みず、従来どおりフラットな <stem>.md のみを
    候補とすること。
    """
    rel_path = Path("MY_HOME_SYSTEM/common.py")
    candidates = module.source_to_doc_candidates(rel_path)

    assert candidates == [module.SPEC_ROOT / "MY_HOME_SYSTEM" / "common.md"]


def test_dashboard_common_doc_resolves_back_to_nested_source():
    """
    doc_to_source_candidates(dashboard_common.md) が、実際の
    MY_HOME_SYSTEM/views/dashboard/common.py を候補として返すこと
    (修正前は候補0件で、孤立ドキュメント判定からも完全に除外されていた)。
    """
    doc_path = module.SPEC_ROOT / "MY_HOME_SYSTEM" / "dashboard_common.md"
    candidates = module.doc_to_source_candidates(doc_path)

    expected = Path("MY_HOME_SYSTEM/views/dashboard/common.py")
    assert expected in candidates
    assert any((module.REPO_ROOT / c).exists() for c in candidates), (
        "dashboard_common.md に対応する実ソースファイルが見つからず、"
        "孤立ドキュメントとして誤検知されうる状態のままになっている。"
    )


def test_plain_common_doc_does_not_gain_extra_matches_from_disambiguation_logic():
    """
    回帰防止: common.md のstem("common")にはアンダースコアが無いため、
    今回追加したdisambiguation探索(アンダースコア分割によるparent_dir/remainder
    パターンでの追加rglob)は一切発火しないこと。

    なお、素のstemによるrglob(f"{stem}.py")自体は本修正の対象外の既存挙動であり、
    "common.py"という名前がMY_HOME_SYSTEM配下に複数階層で存在する場合はそれらを
    全て拾う(=候補が複数になりうる)。これは doc_to_source_candidates の唯一の
    実用途であるcmd_full()の孤立ドキュメント判定が「候補のいずれかが実在するか」
    のみを見る existence ベースのチェックであるため、実害はない。
    """
    doc_path = module.SPEC_ROOT / "MY_HOME_SYSTEM" / "common.md"
    candidates = module.doc_to_source_candidates(doc_path)

    # disambiguation探索を追加する前と同じ候補集合であること
    # (今回のdisambiguation分岐が"common"というstemに対しては無効であることの確認)。
    assert set(candidates) == {
        Path("MY_HOME_SYSTEM/common.py"),
        Path("MY_HOME_SYSTEM/views/dashboard/common.py"),
    }


def test_full_audit_no_longer_ignores_dashboard_common_doc():
    """
    cmd_full()の孤立ドキュメント検知(セクション2)が、dashboard_common.mdを
    正しく「対応ソースあり」として扱い、孤立ドキュメントとして誤検知しないこと
    (=修正前の「candidatesが空なので孤立判定自体がスキップされ、検知の
    どちらのカテゴリにも一切現れない」状態を脱していること)を、実際の
    リポジトリに対してcmd_full()を実行して確認する。
    """
    report = module.cmd_full()
    orphaned_str = "\n".join(report.orphaned)
    assert "dashboard_common.md" not in orphaned_str


# --- Issue #124の回帰テスト ---
#
# MY_HOME_SYSTEMのテストは tests/ ディレクトリ配下に置かれるため
# EXCLUDE_PARTS のディレクトリ名ベースの除外で捕捉できるが、DDDには
# pytest基盤となる tests/ ディレクトリが無く、DDD直下に test_*.py を
# フラット配置する規約になっている。ディレクトリ名だけでは検知できず、
# is_tracked_source が DDD の test_*.py を「仕様書があるべきソース」と
# 誤認して、未文書化・ドリフトの偽陽性を報告していた。


def test_ddd_flat_test_file_is_not_a_tracked_source():
    """
    DDD直下にフラット配置されたtest_*.pyは、tests/ディレクトリ配下に
    置かれていなくてもis_tracked_sourceの対象外(=仕様書不要)になること。
    修正前は True を返し、「仕様書が見つからないファイル」として
    誤検知されていた。
    """
    rel_path = Path("DDD/test_newface_monitor_datamanager.py")
    assert module.is_tracked_source(rel_path) is False


def test_my_home_system_nested_test_file_is_still_not_tracked():
    """
    回帰防止: MY_HOME_SYSTEM/tests/配下のテストファイルは、従来通り
    EXCLUDE_PARTSのディレクトリ名ベースの除外で対象外のままであること
    (今回の命名規則ベースの判定を追加しても、既存の除外経路を壊していない)。
    """
    rel_path = Path("MY_HOME_SYSTEM/tests/test_backup_service.py")
    assert module.is_tracked_source(rel_path) is False


def test_non_test_ddd_source_file_is_still_tracked():
    """
    回帰防止: test_*.py以外のDDDソースファイルは、引き続き
    is_tracked_sourceの対象(=仕様書が必要)のままであること。
    """
    rel_path = Path("DDD/extract_youtube_urls.py")
    assert module.is_tracked_source(rel_path) is True


def test_doc_to_source_candidates_still_resolves_existing_test_doc_to_its_source():
    """
    is_test_fileによる除外はis_tracked_source側のみに適用され、
    doc_to_source_candidates(仕様書→ソースの逆引き、孤立ドキュメント判定に
    使われる)には影響しないこと。これが壊れると、既存の
    docs/specifications/DDD/test_*.md が対応ソースを見失い、
    孤立ドキュメントとして誤検知されるようになってしまう。
    """
    doc_path = module.SPEC_ROOT / "DDD" / "test_newface_monitor_lock.md"
    candidates = module.doc_to_source_candidates(doc_path)

    expected = Path("DDD/test_newface_monitor_lock.py")
    assert expected in candidates
    assert any((module.REPO_ROOT / c).exists() for c in candidates), (
        "test_newface_monitor_lock.md に対応する実ソースファイルが見つからず、"
        "孤立ドキュメントとして誤検知されうる状態になっている。"
    )


def test_full_audit_no_longer_flags_ddd_test_files_as_undocumented():
    """
    cmd_full()を実行しても、DDD配下のtest_*.pyが「仕様書が見つからない
    ファイル」として報告されないこと(Issue #124で報告された実際の症状の
    回帰確認)。
    """
    report = module.cmd_full()
    undocumented_str = "\n".join(report.undocumented)
    assert "DDD/test_" not in undocumented_str


def test_full_audit_no_longer_flags_existing_ddd_test_docs_as_orphaned():
    """
    既存のdocs/specifications/DDD/test_*.md群が、cmd_full()の孤立ドキュメント
    検知で誤って「対応ソースが見つからない」と報告されないこと。
    """
    report = module.cmd_full()
    orphaned_str = "\n".join(report.orphaned)
    assert "DDD/test_" not in orphaned_str


# --- Issue #188の回帰テスト ---
#
# doc_to_source_candidates は MY_HOME_SYSTEM/DDD 側では rglob で実在する
# ファイルのみを候補として返すため、ソースが本当に削除された仕様書では
# 候補が常に空リストになっていた。cmd_full() の孤立判定は
# `if candidates and not any(exists)` であるため、候補0件の仕様書は
# 孤立判定自体がスキップされ、どのカテゴリにも一切現れなかった
# (family-quest側のsource_to_doc_candidatesは実在確認前の「あるべき
# パス」を候補として構築するため、この非対称は発生しない)。
#
# docs/specifications/MY_HOME_SYSTEM/ai_logic.md と bounty_router.md は、
# 対応するソース(MY_HOME_SYSTEM/ai_logic.py, bounty_router.py)がリポジトリ
# 上のどこにも存在しない実例であり、本Issueの実測結果でも使われている。


def test_doc_to_source_candidates_returns_nonempty_for_source_that_no_longer_exists():
    """
    対応ソースが本当に削除されている仕様書(ai_logic.md)でも、
    doc_to_source_candidates が空リストを返さないこと(=孤立判定の
    `if candidates and ...` が誤ってスキップされない)ことを確認する。
    """
    doc_path = module.SPEC_ROOT / "MY_HOME_SYSTEM" / "ai_logic.md"
    candidates = module.doc_to_source_candidates(doc_path)

    assert candidates, (
        "対応ソースが存在しない仕様書でも候補は空であってはならない"
        "(空だとcmd_full()の孤立判定自体がスキップされてしまう)"
    )
    assert not any((module.REPO_ROOT / c).exists() for c in candidates), (
        "この仕様書に対応する実ソースは存在しないはずなのに、候補のいずれかが"
        "実在してしまっている(テスト前提が崩れている)"
    )


def test_full_audit_detects_orphaned_py_docs_with_no_matching_source():
    """
    cmd_full()の孤立ドキュメント検知が、対応ソースの存在しない
    ai_logic.md / bounty_router.md を実際に「孤立ドキュメント」として
    報告すること(修正前はcandidates=[]により検知自体がスキップされ、
    週次監査のIssueに永久に現れなかった)。
    """
    report = module.cmd_full()
    orphaned_str = "\n".join(report.orphaned)
    assert "MY_HOME_SYSTEM/ai_logic.md" in orphaned_str
    assert "MY_HOME_SYSTEM/bounty_router.md" in orphaned_str


# --- Issue #283の回帰テスト ---
#
# PR #278で導入されたfamily-questのVitestテスト(*.test.ts、src/test/配下の
# セットアップファイル)は、"tests"(複数形)ディレクトリ名にもtest_*.pyという
# 命名にも一致しないため、既存の除外経路(EXCLUDE_PARTSのディレクトリ名判定・
# is_test_file)のどちらにも引っかからず、is_tracked_sourceがTrueを返して
# 「仕様書が見つからないファイル」として誤検知されていた。


def test_test_ts_suffix_file_is_not_a_tracked_source():
    """
    *.test.ts は仕様書不要(is_tracked_source=False)であること。
    修正前はFQ_EXTENSIONSの.tsに一致し、Trueを返していた。
    """
    rel_path = Path("family-quest/src/hooks/useOnlineStatus.test.ts")
    assert module.is_tracked_source(rel_path) is False


def test_nested_test_ts_suffix_file_is_not_a_tracked_source():
    """回帰防止: ネストした*.test.tsも同様に対象外であること。"""
    rel_path = Path("family-quest/src/features/quest/hooks/useQuestStatus.test.ts")
    assert module.is_tracked_source(rel_path) is False


def test_src_test_dir_file_is_not_a_tracked_source():
    """
    src/test/配下のファイル(例: setup.ts)は、ファイル名自体は*.test.tsに
    一致しなくても、"test"ディレクトリ配下にあることをもって対象外になること。
    """
    rel_path = Path("family-quest/src/test/setup.ts")
    assert module.is_tracked_source(rel_path) is False


def test_non_test_fq_source_file_is_still_tracked():
    """
    回帰防止: *.test.ts(x)でもsrc/test/配下でもない通常のfamily-quest
    ソースファイルは、引き続きis_tracked_sourceの対象(=仕様書が必要)のままであること。
    """
    rel_path = Path("family-quest/src/lib/utils.ts")
    assert module.is_tracked_source(rel_path) is True


def test_full_audit_no_longer_flags_family_quest_test_files_as_undocumented():
    """
    cmd_full()を実行しても、family-questのVitestテストファイルが
    「仕様書が見つからないファイル」として報告されないこと
    (Issue #283で報告された実際の症状の回帰確認)。
    """
    report = module.cmd_full()
    undocumented_str = "\n".join(report.undocumented)
    assert "useOnlineStatus.test.ts" not in undocumented_str
    assert "useQuestStatus.test.ts" not in undocumented_str
    assert "src/test/setup.ts" not in undocumented_str
    assert "utils.test.ts" not in undocumented_str
