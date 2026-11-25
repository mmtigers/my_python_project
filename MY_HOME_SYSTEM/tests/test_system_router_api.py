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
import logging
import os
import subprocess
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
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


def test_git_status_returns_the_service_result(api_client, monkeypatch):
    payload = {"ok": True, "head": {"sha": "abc1234", "date": "", "subject": "s"}, "recent": []}
    monkeypatch.setattr(system_router.system_info_service, "get_git_status", lambda: payload)
    res = api_client.get("/api/system/git/status")
    assert res.status_code == 200
    assert res.json() == payload


def test_git_status_failure_is_200_with_error(api_client, monkeypatch):
    """git が使えなくても画面に理由を出せるよう、500にせず ok:false で返す。"""
    monkeypatch.setattr(
        system_router.system_info_service, "get_git_status",
        lambda: {"ok": False, "error": "Gitの情報を取得できませんでした", "recent": []},
    )
    res = api_client.get("/api/system/git/status")
    assert res.status_code == 200 and res.json()["ok"] is False


@pytest.fixture
def _restart_allowed(monkeypatch):
    """#1143: 起動から十分経っていて、更新も実行中でない状態(手動の再起動を受け付ける状態)。"""
    svc = system_router.system_maintenance_service
    monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() - svc.RESTART_MIN_UPTIME_SEC - 60)
    monkeypatch.setattr(svc, "_deploy_running", False)


def test_restart_success_returns_200(api_client, monkeypatch, _restart_allowed):
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


def test_restart_failure_returns_500_with_message(api_client, monkeypatch, _restart_allowed):
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


class TestRestartGuard:
    """#1143: 短時間の連続再起動・更新中の再起動を拒否する。

    13分間に5回再起動され(起動の16秒後に再起動された例もある)、そのたびに
    Scheduler の子タスク・見守りタイマー・カメラ購読が中断された。
    systemctl は実行せず、`subprocess.run` が呼ばれないことで「再起動しなかった」を確かめる。
    """

    @pytest.fixture
    def svc(self, monkeypatch, tmp_path):
        svc = system_router.system_maintenance_service
        monkeypatch.setattr(svc, "RESTART_MARKER_FILE", str(tmp_path / "restart_marker.json"))
        monkeypatch.setattr(svc, "_deploy_running", False)
        self.runs = []
        monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: self.runs.append(a) or subprocess.CompletedProcess(a, 0))
        return svc

    def test_within_min_uptime_returns_409_without_restarting(self, api_client, monkeypatch, svc):
        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() - 16)
        res = api_client.post("/api/system/restart")
        assert res.status_code == 409
        detail = res.json()["detail"]
        assert detail["can_force"] is True
        assert "120秒" in detail["message"]
        assert self.runs == []

    def test_force_overrides_the_min_uptime(self, api_client, monkeypatch, svc):
        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() - 16)
        res = api_client.post("/api/system/restart?force=true")
        assert res.status_code == 200
        assert len(self.runs) == 1

    def test_while_deploy_running_returns_409_even_with_force(self, api_client, monkeypatch, svc):
        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() - 3600)
        monkeypatch.setattr(svc, "_deploy_running", True)
        for url in ("/api/system/restart", "/api/system/restart?force=true"):
            res = api_client.post(url)
            assert res.status_code == 409
            assert res.json()["detail"]["can_force"] is False
        assert self.runs == []

    def test_normal_restart_is_200_and_logs_the_client_ip(self, api_client, monkeypatch, svc, caplog):
        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() - 3600)
        with caplog.at_level(logging.INFO):
            res = api_client.post("/api/system/restart")
        assert res.status_code == 200
        assert len(self.runs) == 1
        assert any("POST /api/system/restart from testclient" in r.getMessage() for r in caplog.records)

    def test_deploy_internal_restart_is_not_guarded(self, monkeypatch, svc):
        """更新が自分で呼ぶ再起動(起動直後・_deploy_running 中)は拒否しない。"""
        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time())
        monkeypatch.setattr(svc, "_deploy_running", True)
        assert svc.restart_home_system()[0] is True
        assert len(self.runs) == 1
