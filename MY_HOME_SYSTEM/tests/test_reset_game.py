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


def _seed_reset_target(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, medal_count, role) VALUES "
            "('son', 'Son', 'Novice', 5, 120, 300, 2, 'role_child'), "
            "('daughter', 'Daughter', 'Novice', 3, 40, 80, 1, 'role_child')"
        )
        conn.execute("INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (1, 'Juice', 10)")
        for uid in ("son", "daughter"):
            conn.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES (?, 1, 'Q', 10, 5, '2026-09-07T08:00:00+09:00', 'approved')", (uid,)
            )
            conn.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES (?, 2, 'Q2', 10, 5, '2026-09-07T09:00:00+09:00', 'pending')", (uid,)
            )
            conn.execute(
                "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
                "VALUES (?, 1, 'owned', '2026-09-07T08:30:00+09:00')", (uid,)
            )
            conn.execute(
                "INSERT INTO reward_history (user_id, reward_id, reward_title, cost_gold, redeemed_at) "
                "VALUES (?, 1, 'Juice', 10, '2026-09-07T08:30:00+09:00')", (uid,)
            )
        conn.commit()
    finally:
        conn.close()


def _count(db_path, sql, params=()):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def test_reset_user_data_clears_history_and_inventory_in_one_transaction(isolated_db, monkeypatch, capsys):
    """Issue #544: 以前は quest_users のみゼロ化し、approved の quest_history と user_inventory を
    残していたため、リセット後も「本日完了済み」が続き、リセット前履歴の取消が #356 のガードで
    拒否されていた。履歴・インベントリも同一トランザクションで削除されること、他ユーザーの
    データには触れないこと、購入ログ(reward_history)は残ることを検証する。"""
    monkeypatch.setattr(reset_game, "DB_PATH", isolated_db)
    _seed_reset_target(isolated_db)

    reset_game.reset_user_data({"label": "智矢", "db_id": "son"})

    assert _count(isolated_db, "SELECT level + exp + gold + medal_count FROM quest_users WHERE user_id='son'") == 1
    assert _count(isolated_db, "SELECT COUNT(*) FROM quest_history WHERE user_id='son'") == 0
    assert _count(isolated_db, "SELECT COUNT(*) FROM user_inventory WHERE user_id='son'") == 0
    assert _count(isolated_db, "SELECT COUNT(*) FROM reward_history WHERE user_id='son'") == 1
    # 他ユーザーは無傷
    assert _count(isolated_db, "SELECT gold FROM quest_users WHERE user_id='daughter'") == 80
    assert _count(isolated_db, "SELECT COUNT(*) FROM quest_history WHERE user_id='daughter'") == 2
    assert _count(isolated_db, "SELECT COUNT(*) FROM user_inventory WHERE user_id='daughter'") == 1
    out = capsys.readouterr().out
    assert "クエスト履歴 2件削除" in out and "インベントリ 1件削除" in out


def test_reset_user_data_unknown_user_changes_nothing(isolated_db, monkeypatch, capsys):
    monkeypatch.setattr(reset_game, "DB_PATH", isolated_db)
    _seed_reset_target(isolated_db)

    reset_game.reset_user_data({"label": "nobody", "db_id": "nobody"})

    assert _count(isolated_db, "SELECT COUNT(*) FROM quest_history") == 4
    assert _count(isolated_db, "SELECT COUNT(*) FROM user_inventory") == 2
    assert "データが見つかりませんでした" in capsys.readouterr().out
