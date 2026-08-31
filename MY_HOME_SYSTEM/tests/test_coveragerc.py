# MY_HOME_SYSTEM/tests/test_coveragerc.py
"""
.coveragerc の omit リストが実在するパスを指しているかの回帰テスト。

Issue #189: omit の `train_service.py` は coverage.py の prep_patterns に
よりcwd(MY_HOME_SYSTEM/)基準で絶対化されるため、実際には
MY_HOME_SYSTEM/services/train_service.py に配置されているファイルには
一切マッチしなかった。マッチしない除外パターンは黙って無効になるため、
CIの `--cov-fail-under=45` の分母に本来除外すべきファイルが入り続け、
除外意図と食い違っていた(閾値を実態より押し下げる方向)。
"""
import configparser
import os

MY_HOME_SYSTEM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _literal_omit_entries():
    """.coveragerc の omit リストのうち、ワイルドカード(*)を含まない
    (=coverage.pyによりcwd基準の絶対パスへ単純に解決される)エントリのみを返す。"""
    config = configparser.ConfigParser()
    config.read(os.path.join(MY_HOME_SYSTEM_DIR, ".coveragerc"))
    omit_raw = config.get("run", "omit")
    entries = [line.strip() for line in omit_raw.splitlines() if line.strip()]
    return [e for e in entries if "*" not in e]


def test_coveragerc_literal_omit_entries_point_to_real_files():
    """omitのうちワイルドカードを含まない各エントリが、MY_HOME_SYSTEM/を
    基準に実在するファイルを指していること。存在しないパスはcoverage.pyの
    prep_patternsによりcwd基準で絶対化された際にどのソースファイルとも
    マッチせず、除外設定が黙って無効になる(Issue #189)。"""
    entries = _literal_omit_entries()
    assert entries, "テスト対象のリテラルomitエントリが1件も検出できなかった"

    missing = [e for e in entries if not os.path.isfile(os.path.join(MY_HOME_SYSTEM_DIR, e))]
    assert not missing, f"以下のomitエントリは実在するファイルを指していない: {missing}"
