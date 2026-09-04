# MY_HOME_SYSTEM/tests/test_coveragerc.py
"""
.coveragerc の omit リストが実在するパスを指しているかの回帰テスト。

Issue #189: omit の `train_service.py` は coverage.py の prep_patterns に
よりcwd(MY_HOME_SYSTEM/)基準で絶対化されるため、実際には
MY_HOME_SYSTEM/services/train_service.py に配置されているファイルには
一切マッチしなかった。マッチしない除外パターンは黙って無効になるため、
CIの `--cov-fail-under` の分母に本来除外すべきファイルが入り続け、
除外意図と食い違っていた(閾値を実態より押し下げる方向)。

Issue #367: 旧テストはワイルドカード(*)付きエントリを検査対象外にしていた
ため、存在しないディレクトリを指す `old/*` がすり抜けていた。ワイルドカード
付きエントリも glob で「実在するパスに1件以上一致すること」を検証する。
また omit は「本当に実行不能なもの(Streamlit UI)」だけに絞る方針になった
ため、専用テストが存在するモジュールが再び omit されないよう、許可リスト
との一致も検証する。
"""
import configparser
import glob
import os

MY_HOME_SYSTEM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Issue #367 で確定した omit の許可リスト。ここに無いエントリを追加すると
# テストが落ちる(=「テスト済みモジュールを omit で隠す」退行の防止)。
# 追加が本当に必要な場合(単体テストで実行不能なUI等)は、このリストと
# .coveragerc の両方を同じPRで更新すること。
ALLOWED_OMIT_ENTRIES = {
    "tests/*",
    "*/__init__.py",
    "dashboard.py",
    "views/dashboard/*",
}


def _omit_entries():
    """.coveragerc の omit リストの全エントリ(ワイルドカード付きを含む)を返す。"""
    config = configparser.ConfigParser()
    config.read(os.path.join(MY_HOME_SYSTEM_DIR, ".coveragerc"))
    omit_raw = config.get("run", "omit")
    return [line.strip() for line in omit_raw.splitlines() if line.strip()]


def _matches_existing_path(entry: str) -> bool:
    """エントリ(cwd=MY_HOME_SYSTEM/基準)が実在するパスに1件以上一致するか。

    coverage.py は omit の各パターンを cwd 基準で絶対化してから fnmatch する
    ため、`*/__init__.py` のような先頭ワイルドカードは「直下の1階層」だけに
    一致する glob と等価になる。ここでも同じ意味で glob する。
    """
    if "*" in entry:
        return bool(glob.glob(os.path.join(MY_HOME_SYSTEM_DIR, entry)))
    return os.path.isfile(os.path.join(MY_HOME_SYSTEM_DIR, entry))


def test_coveragerc_omit_entries_point_to_existing_paths():
    """omit の各エントリ(ワイルドカード付きも含む)が、MY_HOME_SYSTEM/ を
    基準に実在するパスへ1件以上一致すること。一致しないパターンは
    coverage.py により黙って無効になる(Issue #189)か、既に消えたディレクトリを
    指したまま放置される(Issue #367 の `old/*`)。"""
    entries = _omit_entries()
    assert entries, "テスト対象のomitエントリが1件も検出できなかった"

    missing = [e for e in entries if not _matches_existing_path(e)]
    assert not missing, f"以下のomitエントリは実在するパスに一致しない: {missing}"


def test_coveragerc_omit_is_limited_to_allowed_entries():
    """omit が Issue #367 で確定した許可リストと完全に一致すること。
    専用テストが存在するモジュール(monitors/*, sync_strict.py 等)を再び
    omit に加えると CI のカバレッジ値が水増しされ、閾値が退行検知として
    機能しなくなる。"""
    entries = set(_omit_entries())
    assert entries == ALLOWED_OMIT_ENTRIES, (
        f"omit が許可リストと一致しない: 余分={entries - ALLOWED_OMIT_ENTRIES}, "
        f"不足={ALLOWED_OMIT_ENTRIES - entries}"
    )
