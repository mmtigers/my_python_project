# MY_HOME_SYSTEM/tests/test_reset_game.py
"""
reset_game.py の回帰テスト。

Issue #186: 以前はDB_PATHがCWD相対の"home_system.db"に直接sqlite3.connectして
おり、他のDBアクセス経路(config.SQLITE_DB_PATH = BASE_DIR/home_system.db、
環境変数SQLITE_DB_PATHで上書き可)と食い違っていた。MY_HOME_SYSTEM/以外のCWD
から実行するとファイル不在で終了する、あるいは同名ファイルが存在すれば別のDB
を誤って操作する、SQLITE_DB_PATH環境変数での差し替え運用時に本番と異なる
ファイルをリセットする、といったリスクがあった。ユーザー一覧の取得(読み取りの
みで交錯の危険がない)は引き続きこのDB_PATHを直接読むため、本回帰テストは
そのまま有効。

Issue #547: リセット処理自体は、reset_game.pyがunified_serverとは別プロセス
で動くためサービス層のユーザー単位ロックを共有できず、稼働中サーバーの承認処理
(read-modify-write)と交錯するとリセット結果が上書きされうる欠陥があった。
直接DBを書き換える実装(旧reset_user_data、BEGIN IMMEDIATE使用)を、サーバーの
リセットAPI(POST /api/quest/admin/reset_user)を呼ぶ薄いクライアントに置き換えた。
以下のテストはrequests.postをmonkeypatchし、reset_game.py側の呼び出し内容・
レスポンス種別ごとの挙動(成功・404・接続失敗・管理者不在)を検証する。
API自体(サーバー側の権限チェック・DB更新の原子性)の回帰テストは
tests/test_quest_router_endpoints.py::TestAdminResetUser、
tests/test_balance_lock_cross_path_concurrency.py::TestResetVersusApproveCrossPathConcurrency
を参照。
"""
import importlib
import os
import sys

import pytest

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


def test_fetch_users_includes_role_for_admin_resolution(isolated_db, monkeypatch):
    """#547: リセットAPI呼び出し時のadmin_id自動選択にroleが必要なため、
    fetch_usersがrole列も返すこと。"""
    monkeypatch.setattr(reset_game, "DB_PATH", isolated_db)
    _seed_reset_target(isolated_db)

    users = reset_game.fetch_users()

    roles = {u["id"]: u["role"] for u in users}
    assert roles == {"son": "role_child", "daughter": "role_child"}


class _FakeResponse:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self._json_body = json_body or {}
        self.text = text

    def json(self):
        return self._json_body


def test_reset_user_data_posts_to_admin_reset_api_with_resolved_admin_id(monkeypatch, capsys):
    """#547: role_adultの最初のユーザーをadmin_idとして自動選択し、
    設定されたベースURLへPOSTすること。"""
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(200, {"status": "reset", "deletedHistoryCount": 2, "deletedInventoryCount": 1})

    monkeypatch.setattr(reset_game.requests, "post", fake_post)
    monkeypatch.setattr(config, "RESET_GAME_API_BASE_URL", "http://example-server:8000")

    users_info = [
        {"id": "dad", "name": "将博", "role": "role_adult"},
        {"id": "son", "name": "智矢", "role": "role_child"},
    ]
    reset_game.reset_user_data({"label": "智矢", "db_id": "son"}, users_info)

    assert captured["url"] == "http://example-server:8000/api/quest/admin/reset_user"
    assert captured["json"] == {"admin_id": "dad", "target_user_id": "son"}

    out = capsys.readouterr().out
    assert "クエスト履歴 2件削除" in out and "インベントリ 1件削除" in out


def test_reset_user_data_prints_warning_on_404_without_exiting(monkeypatch, capsys):
    monkeypatch.setattr(reset_game.requests, "post", lambda *a, **k: _FakeResponse(404))
    users_info = [{"id": "dad", "name": "将博", "role": "role_adult"}]

    reset_game.reset_user_data({"label": "nobody", "db_id": "nobody"}, users_info)

    assert "データが見つかりませんでした" in capsys.readouterr().out


def test_reset_user_data_exits_when_server_unreachable(monkeypatch, capsys):
    def fake_post(*a, **k):
        raise reset_game.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(reset_game.requests, "post", fake_post)
    users_info = [{"id": "dad", "name": "将博", "role": "role_adult"}]

    with pytest.raises(SystemExit):
        reset_game.reset_user_data({"label": "智矢", "db_id": "son"}, users_info)

    assert "サーバーに接続できませんでした" in capsys.readouterr().out


def test_reset_user_data_exits_when_server_returns_unexpected_error(monkeypatch, capsys):
    monkeypatch.setattr(
        reset_game.requests, "post",
        lambda *a, **k: _FakeResponse(500, text='{"detail": "internal error"}'),
    )
    users_info = [{"id": "dad", "name": "将博", "role": "role_adult"}]

    with pytest.raises(SystemExit):
        reset_game.reset_user_data({"label": "智矢", "db_id": "son"}, users_info)

    assert "エラーが発生しました" in capsys.readouterr().out


def test_reset_user_data_exits_when_no_admin_found(monkeypatch, capsys):
    users_info = [{"id": "son", "name": "智矢", "role": "role_child"}]

    with pytest.raises(SystemExit):
        reset_game.reset_user_data({"label": "智矢", "db_id": "son"}, users_info)

    assert "管理者" in capsys.readouterr().out
