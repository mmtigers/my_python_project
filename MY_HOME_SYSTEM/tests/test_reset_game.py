# MY_HOME_SYSTEM/tests/test_reset_game.py
"""
reset_game.py の回帰テスト。

Issue #186: 以前はDB_PATHがCWD相対の"home_system.db"に直接sqlite3.connectして
おり、他のDBアクセス経路(config.SQLITE_DB_PATH = BASE_DIR/home_system.db、
環境変数SQLITE_DB_PATHで上書き可)と食い違っていた。MY_HOME_SYSTEM/以外のCWD
から実行するとファイル不在で終了する、あるいは同名ファイルが存在すれば別のDB
を誤って操作する、SQLITE_DB_PATH環境変数での差し替え運用時に本番と異なる
ファイルをリセットする、といったリスクがあった。
"""
import importlib
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import reset_game


def test_db_path_uses_config_sqlite_db_path_not_cwd_relative_literal(monkeypatch):
    """DB_PATHがCWD相対のハードコード文字列ではなく、他のDBアクセス経路と
    同じconfig.SQLITE_DB_PATHから導出されていること。DB_PATHはモジュール
    import時に一度だけ評価される値のため、他のテストファイルによる
    config.SQLITE_DB_PATHの書き換えの影響を受けないよう、本テスト内で
    明示的な値に固定したうえでモジュールをリロードして検証する。"""
    original_db_path = config.SQLITE_DB_PATH
    sentinel = "/tmp/reset_game_test_sentinel_db_path.db"
    monkeypatch.setattr(config, "SQLITE_DB_PATH", sentinel)
    try:
        importlib.reload(reset_game)
        assert reset_game.DB_PATH == sentinel
    finally:
        # 後続テストに影響しないよう、実際のconfig.SQLITE_DB_PATHへ戻してからreloadする
        config.SQLITE_DB_PATH = original_db_path
        importlib.reload(reset_game)
