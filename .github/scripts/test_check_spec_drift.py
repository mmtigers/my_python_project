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
