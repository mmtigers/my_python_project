# MY_HOME_SYSTEM/tests/test_system_router_api.py
"""
routers/system_router.py (手動バックアップトリガー・サービス再起動)のテスト。

backup_service.trigger_manual_backup_async / system_maintenance_service.restart_home_system は
実際にはNAS I/O・systemctl呼び出しを伴うため、ここではrouter層の
「どうHTTPレスポンスへ変換するか」のみをモックして検証する。

**(不具合修正)** バックアップは以前`perform_backup()`の完了を待ってから応答して
おり、成功/失敗をレスポンスにそのまま反映していた。完了(NAS転送・場合によっては
オフサイト複製で最大30分)を待たせると「タップしても完了したか分からない」原因に
なっていたため、`trigger_manual_backup_async`でバックグラウンド実行に切り替えた。
そのため、このエンドポイントは常に「開始した」旨を返し、実際の成功/失敗は
(`services/backup_service.py`側のテストが検証する)Discord通知で伝える。

なお、このエンドポイントには認可チェックが一切なく、誰でもバックアップを
トリガーできる(CODE_REVIEW_REPORT.md 2.1で指摘済み・未対応)。
本テストはその機能テストのみを目的とし、認可欠如を許容するものではない
(最終報告書の「残っているリスク」に明記する)。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from routers import system_router


def test_backup_returns_200_and_starts_the_background_backup(api_client, monkeypatch):
    calls = []
    monkeypatch.setattr(system_router.backup_service, "trigger_manual_backup_async", lambda: calls.append(1) or True)

    res = api_client.post("/api/system/backup")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "started"
    assert "run_id" in body
    assert len(calls) == 1


def test_backup_endpoint_currently_requires_no_authentication(api_client, monkeypatch):
    """
    既知のリスク(2.1)の記録用テスト: このエンドポイントはuser_id等の
    パラメータすら要求せず、誰でも呼び出せる。これは「安全」という意味ではなく、
    現状の挙動を明示的に固定して回帰検知するためのテスト。
    """
    calls = []
    monkeypatch.setattr(system_router.backup_service, "trigger_manual_backup_async", lambda: calls.append(1) or True)
    res = api_client.post("/api/system/backup")
    assert res.status_code == 200
    assert len(calls) == 1


def test_restart_success_returns_200(api_client, monkeypatch):
    monkeypatch.setattr(
        system_router.system_maintenance_service,
        "restart_home_system",
        lambda: (True, "再起動コマンドを送信しました"),
    )
    res = api_client.post("/api/system/restart")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["message"] == "再起動コマンドを送信しました"


def test_restart_failure_returns_500_with_message(api_client, monkeypatch):
    monkeypatch.setattr(
        system_router.system_maintenance_service,
        "restart_home_system",
        lambda: (False, "エラー: [Errno 2] No such file or directory: 'sudo'"),
    )
    res = api_client.post("/api/system/restart")
    assert res.status_code == 500
    assert res.json()["detail"] == "エラー: [Errno 2] No such file or directory: 'sudo'"


def test_backup_while_running_does_not_start_a_second_one(api_client, monkeypatch):
    monkeypatch.setattr(system_router.backup_service, "trigger_manual_backup_async", lambda: False)
    res = api_client.post("/api/system/backup")
    assert res.status_code == 200
    assert res.json()["status"] == "running"


def test_backup_status_reports_state_and_latest_backup(api_client, monkeypatch):
    monkeypatch.setattr(
        system_router.backup_service, "get_manual_backup_state",
        lambda: {"running": False, "run_id": 3,
                 "last_result": {"run_id": 3, "success": True, "message": "ok", "size_mb": 1.5,
                                 "finished_at": "2026-10-02T04:00:00"}},
    )
    monkeypatch.setattr(
        system_router.backup_service, "get_latest_backup_info",
        lambda: {"filename": "home_system_20261002_040000.db",
                 "created_at": "2026-10-02T04:00:00", "size_mb": 1.5},
    )
    body = api_client.get("/api/system/backup/status").json()
    assert body["running"] is False
    assert body["run_id"] == 3
    assert body["last_result"]["success"] is True
    assert body["latest_backup"]["created_at"] == "2026-10-02T04:00:00"
