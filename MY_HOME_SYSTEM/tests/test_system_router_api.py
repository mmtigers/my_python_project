# MY_HOME_SYSTEM/tests/test_system_router_api.py
"""
routers/system_router.py (手動バックアップトリガー)のテスト。

backup_service.perform_backup は実際にはNAS I/Oを伴うため、
ここではrouter層の「成功/失敗をどうHTTPレスポンスへ変換するか」のみを
services.backup_service.perform_backup をモックして検証する。

なお、このエンドポイントには認可チェックが一切なく、誰でもバックアップを
トリガーできる(CODE_REVIEW_REPORT.md 2.1で指摘済み・未対応)。
本テストはその機能テストのみを目的とし、認可欠如を許容するものではない
(最終報告書の「残っているリスク」に明記する)。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from routers import system_router


def test_backup_success_returns_200_with_size(api_client, monkeypatch):
    monkeypatch.setattr(
        system_router.backup_service, "perform_backup", lambda: (True, "バックアップ完了", 12.5)
    )
    res = api_client.post("/api/system/backup")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["size_mb"] == 12.5


def test_backup_failure_returns_500_with_message(api_client, monkeypatch):
    monkeypatch.setattr(
        system_router.backup_service,
        "perform_backup",
        lambda: (False, "NAS転送後の整合性確認に失敗しました。", 0.0),
    )
    res = api_client.post("/api/system/backup")
    assert res.status_code == 500
    # #408: 生の失敗メッセージ(NASパス等の内部情報を含みうる)はログにのみ残し、
    # クライアントには固定の要約のみを返す。
    assert res.json()["detail"] == "バックアップに失敗しました。サーバーログを確認してください。"
    assert "整合性確認に失敗" not in res.json()["detail"]


def test_backup_endpoint_currently_requires_no_authentication(api_client, monkeypatch):
    """
    既知のリスク(2.1)の記録用テスト: このエンドポイントはuser_id等の
    パラメータすら要求せず、誰でも呼び出せる。これは「安全」という意味ではなく、
    現状の挙動を明示的に固定して回帰検知するためのテスト。
    """
    calls = []

    def _fake_backup():
        calls.append(1)
        return True, "ok", 1.0

    monkeypatch.setattr(system_router.backup_service, "perform_backup", _fake_backup)
    res = api_client.post("/api/system/backup")
    assert res.status_code == 200
    assert len(calls) == 1
