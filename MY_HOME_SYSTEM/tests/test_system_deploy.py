# MY_HOME_SYSTEM/tests/test_system_deploy.py
"""システムページの「最新に更新して再起動」(services/system_maintenance_service.py の
trigger_deploy_async / get_deploy_state)と、routers/system_router.py の /deploy のテスト。

実際の git・systemctl・Discord送信は行わず、`_git`・`restart_home_system`・`send_push` を
差し替えて検証する。
"""
import os
import subprocess
import sys
import threading
import time

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from routers import system_router
from services import system_maintenance_service as svc


class _ProcessKilled(BaseException):
    """`systemctl restart` でこのプロセス自身が停止させられることの模擬(戻らない)。"""


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeGit:
    """`_git(*args)` の差し替え。pull の前後で HEAD を切り替える。"""

    def __init__(self, before="aaa1111", after="bbb2222", pull=None, head_error=False):
        self.heads = [before, after]
        self.pull_result = pull if pull is not None else _cp(0, "Updating")
        self.head_error = head_error
        self.calls = []

    def __call__(self, *args, timeout):
        self.calls.append(args)
        if args[0] == "rev-parse":
            if self.head_error:
                return _cp(128, "", "fatal: not a git repository")
            return _cp(0, self.heads.pop(0) + "\n")
        if args[0] == "pull":
            if isinstance(self.pull_result, BaseException):
                raise self.pull_result
            if self.pull_result.returncode == 0:
                return self.pull_result
            self.heads = [self.heads[0], self.heads[0]]
            return self.pull_result
        if args[0] == "rev-list":
            return _cp(0, "3\n")
        raise AssertionError(f"想定外の git 呼び出し: {args}")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "DEPLOY_STATE_FILE", str(tmp_path / "deploy_state.json"))
    monkeypatch.setattr(svc, "_deploy_running", False)
    monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time())
    restarts = []
    monkeypatch.setattr(svc, "restart_home_system", lambda: restarts.append(1) or (True, "ok"))
    pushes = []
    monkeypatch.setattr(svc, "send_push", lambda *a, **k: pushes.append((a, k)) or True)
    return restarts, pushes


class TestRunDeploy:
    def test_new_commits_are_pulled_then_restarts(self, env, monkeypatch):
        restarts, pushes = env
        git = FakeGit()
        monkeypatch.setattr(svc, "_git", git)

        def restart_kills_us():
            restarts.append(1)
            raise _ProcessKilled()
        monkeypatch.setattr(svc, "restart_home_system", restart_kills_us)

        svc._deploy_running = True
        with pytest.raises(_ProcessKilled):
            svc._run_deploy(1)

        assert ("pull", "--ff-only") in git.calls
        assert len(restarts) == 1
        assert len(pushes) == 1, "実行の記録をDiscordへ残す"
        state = svc._read_deploy_state()
        assert state["phase"] == svc.PHASE_RESTARTING
        assert (state["from"], state["to"]) == ("aaa1111", "bbb2222")

    def test_restart_returning_normally_is_reported_as_failure(self, env, monkeypatch):
        """正常ならプロセスごと入れ替わるため戻らない。戻ってきたら「実行中」のまま待たせない。"""
        restarts, _ = env
        monkeypatch.setattr(svc, "_git", FakeGit())
        svc._deploy_running = True
        svc._run_deploy(1)
        st = svc.get_deploy_state()
        assert len(restarts) == 1
        assert st["running"] is False
        assert st["last_result"]["success"] is False
        assert "入れ替わりませんでした" in st["last_result"]["message"]

    def test_pull_failure_does_not_restart_and_reports_reason(self, env, monkeypatch):
        restarts, _ = env
        monkeypatch.setattr(svc, "_git", FakeGit(pull=_cp(128, "", "fatal: Not possible to fast-forward, aborting.")))

        svc._deploy_running = True
        svc._run_deploy(1)
        st = svc.get_deploy_state()

        assert restarts == []
        assert st["running"] is False
        assert st["last_result"]["success"] is False
        assert "再起動はしていません" in st["last_result"]["message"]
        assert "fast-forward" in st["last_result"]["message"]

    def test_pull_timeout_does_not_restart(self, env, monkeypatch):
        restarts, _ = env
        monkeypatch.setattr(svc, "_git", FakeGit(pull=subprocess.TimeoutExpired("git", 1)))
        svc._deploy_running = True
        svc._run_deploy(1)
        st = svc.get_deploy_state()
        assert restarts == []
        assert st["last_result"]["success"] is False

    def test_already_up_to_date_does_not_restart(self, env, monkeypatch):
        restarts, pushes = env
        monkeypatch.setattr(svc, "_git", FakeGit(before="aaa1111", after="aaa1111"))
        svc._deploy_running = True
        svc._run_deploy(1)
        st = svc.get_deploy_state()
        assert restarts == [] and pushes == []
        assert st["last_result"]["success"] is True
        assert "すでに最新" in st["last_result"]["message"]

    def test_git_unavailable_is_reported_as_failure(self, env, monkeypatch):
        restarts, _ = env
        monkeypatch.setattr(svc, "_git", FakeGit(head_error=True))
        svc._deploy_running = True
        svc._run_deploy(1)
        assert restarts == []
        assert svc.get_deploy_state()["last_result"]["success"] is False

    def test_restart_command_failure_is_reported(self, env, monkeypatch):
        monkeypatch.setattr(svc, "_git", FakeGit())
        monkeypatch.setattr(svc, "restart_home_system", lambda: (False, "エラー: sudo"))
        svc._deploy_running = True
        svc._run_deploy(1)
        st = svc.get_deploy_state()
        assert st["running"] is False
        assert st["last_result"]["success"] is False
        assert "再起動に失敗" in st["last_result"]["message"]

    def test_discord_notification_failure_does_not_stop_the_update(self, env, monkeypatch):
        restarts, _ = env
        monkeypatch.setattr(svc, "_git", FakeGit())

        def boom(*a, **k):
            raise RuntimeError("discord down")
        monkeypatch.setattr(svc, "send_push", boom)
        svc._deploy_running = True
        svc._run_deploy(1)
        assert len(restarts) == 1

    def test_git_never_prompts_for_credentials(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs, cmd=cmd)
            return _cp(0, "x")
        monkeypatch.setattr(svc.subprocess, "run", fake_run)
        svc._git("fetch", timeout=5)
        assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert captured["cmd"][:3] == ["git", "-C", svc.REPO_ROOT]
        assert captured["timeout"] == 5


class TestStateAcrossRestart:
    def test_new_process_after_restart_request_reports_success(self, env, monkeypatch):
        monkeypatch.setattr(svc, "_git", FakeGit())

        def restart_kills_us():
            raise _ProcessKilled()
        monkeypatch.setattr(svc, "restart_home_system", restart_kills_us)
        svc._deploy_running = True
        with pytest.raises(_ProcessKilled):
            svc._run_deploy(1)

        # 再起動後の新プロセスを模す: 起動時刻が要求より後で、メモリ上の実行フラグは空
        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() + 5)
        monkeypatch.setattr(svc, "_deploy_running", False)
        st = svc.get_deploy_state()

        assert st["running"] is False
        assert st["last_result"]["success"] is True
        assert "aaa1111 → bbb2222" in st["last_result"]["message"]
        # 確定した結果は保存され、次回以降も同じ
        assert svc.get_deploy_state()["last_result"]["run_id"] == 1

    def test_old_process_still_alive_during_restart_reports_running(self, env, monkeypatch):
        """再起動を要求した後、プロセスが入れ替わるまでは「実行中」のまま(成功にしない)。"""
        svc._write_deploy_state({
            "run_id": 1, "phase": svc.PHASE_RESTARTING, "success": None, "message": "x",
            "from": "a", "to": "b", "restart_requested_at": time.time(), "finished_at": None,
        })
        monkeypatch.setattr(svc, "_deploy_running", True)
        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() - 100)
        st = svc.get_deploy_state()
        assert st["running"] is True and st["last_result"] is None

    def test_running_state_left_by_a_dead_process_is_reported_as_interrupted(self, env):
        svc._write_deploy_state({
            "run_id": 4, "phase": svc.PHASE_RUNNING, "success": None, "message": "",
            "from": "", "to": "", "restart_requested_at": 0.0, "finished_at": None,
        })
        st = svc.get_deploy_state()
        assert st["running"] is False
        assert st["last_result"]["success"] is False
        assert st["last_result"]["run_id"] == 4

    def test_no_history_reports_idle(self, env):
        assert svc.get_deploy_state() == {"running": False, "run_id": 0, "last_result": None}

    def test_corrupted_state_file_is_treated_as_idle(self, env):
        with open(svc.DEPLOY_STATE_FILE, "w") as f:
            f.write("{not json")
        assert svc.get_deploy_state()["running"] is False


class TestTrigger:
    def test_second_trigger_while_running_does_not_start_another(self, env, monkeypatch):
        gate = threading.Event()
        started = []

        def slow_run(run_id):
            started.append(run_id)
            gate.wait(5)
            svc._finish(run_id, True, "done")
            with svc._deploy_lock:
                svc._deploy_running = False
        monkeypatch.setattr(svc, "_run_deploy", slow_run)

        assert svc.trigger_deploy_async() is True
        assert svc.trigger_deploy_async() is False
        assert svc.get_deploy_state()["running"] is True
        gate.set()
        for _ in range(100):
            if not svc._deploy_running:
                break
            time.sleep(0.02)
        assert started == [1]
        assert svc.get_deploy_state()["last_result"]["run_id"] == 1

    def test_run_id_increments_across_runs(self, env, monkeypatch):
        monkeypatch.setattr(svc, "_run_deploy", lambda run_id: svc._finish(run_id, True, "ok") or setattr(svc, "_deploy_running", False))
        svc.trigger_deploy_async()
        for _ in range(100):
            if not svc._deploy_running:
                break
            time.sleep(0.02)
        svc.trigger_deploy_async()
        for _ in range(100):
            if not svc._deploy_running:
                break
            time.sleep(0.02)
        assert svc.get_deploy_state()["run_id"] == 2


class TestRouter:
    def test_deploy_starts_in_background_and_returns_immediately(self, api_client, monkeypatch):
        calls = []
        monkeypatch.setattr(system_router.system_maintenance_service, "trigger_deploy_async", lambda: calls.append(1) or True)
        monkeypatch.setattr(system_router.system_maintenance_service, "get_deploy_state",
                            lambda: {"running": True, "run_id": 7, "last_result": None})
        res = api_client.post("/api/system/deploy")
        assert res.status_code == 200
        assert res.json()["status"] == "started"
        assert res.json()["run_id"] == 7
        assert calls == [1]

    def test_deploy_while_running_does_not_start_a_second_one(self, api_client, monkeypatch):
        monkeypatch.setattr(system_router.system_maintenance_service, "trigger_deploy_async", lambda: False)
        monkeypatch.setattr(system_router.system_maintenance_service, "get_deploy_state",
                            lambda: {"running": True, "run_id": 7, "last_result": None})
        res = api_client.post("/api/system/deploy")
        assert res.json()["status"] == "running"

    def test_deploy_accepts_no_parameters_from_the_caller(self, api_client, monkeypatch):
        """ブランチ・コマンドを呼び出し側から受け取らない(固定の `git pull --ff-only` のみ)。"""
        seen = []
        monkeypatch.setattr(system_router.system_maintenance_service, "trigger_deploy_async",
                            lambda *a, **k: seen.append((a, k)) or True)
        monkeypatch.setattr(system_router.system_maintenance_service, "get_deploy_state",
                            lambda: {"running": True, "run_id": 1, "last_result": None})
        api_client.post("/api/system/deploy?branch=evil&cmd=rm", json={"branch": "evil", "cmd": "rm"})
        assert seen == [((), {})]

    def test_deploy_status_returns_state(self, api_client, monkeypatch):
        state = {"running": False, "run_id": 2,
                 "last_result": {"run_id": 2, "success": True, "message": "ok", "finished_at": "2026-10-02T10:00:00"}}
        monkeypatch.setattr(system_router.system_maintenance_service, "get_deploy_state", lambda: state)
        assert api_client.get("/api/system/deploy/status").json() == state
