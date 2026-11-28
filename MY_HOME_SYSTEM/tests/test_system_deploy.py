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

    def __init__(self, before="aaa1111", after="bbb2222", pull=None, head_error=False,
                 branch="master", fetch=None, remote_branch_exists=True):
        self.heads = [before, after]
        self.pull_result = pull if pull is not None else _cp(0, "Updating")  # merge の結果
        self.fetch_result = fetch if fetch is not None else _cp(0)
        self.branch = branch
        self.remote_branch_exists = remote_branch_exists
        self.head_error = head_error
        self.calls = []

    def __call__(self, *args, timeout):
        self.calls.append(args)
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return _cp(0, self.branch + "\n")
        if args[0] == "rev-parse" and "--verify" in args:
            return _cp(0 if self.remote_branch_exists else 1)
        if args[0] == "rev-parse":
            if self.head_error:
                return _cp(128, "", "fatal: not a git repository")
            return _cp(0, self.heads.pop(0) + "\n")
        if args[0] == "fetch":
            if isinstance(self.fetch_result, BaseException):
                raise self.fetch_result
            return self.fetch_result
        if args[0] == "merge":
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

        assert ("fetch", "--prune", "origin") in git.calls
        assert ("merge", "--ff-only", "origin/master") in git.calls
        assert not any(c[0] == "pull" for c in git.calls)
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
        assert st["last_result"]["message"].splitlines()[1].startswith("fast-forward で取り込めませんでした")

    def test_deleted_work_branch_reports_cause_in_japanese_first(self, env, monkeypatch):
        """実機が削除済みの作業ブランチを向いたままでも、原因を日本語で示し再起動しない。"""
        restarts, _ = env
        git = FakeGit(branch="docs/discord-audit-recheck-997", remote_branch_exists=False)
        monkeypatch.setattr(svc, "_git", git)
        svc._deploy_running = True
        svc._run_deploy(1)
        st = svc.get_deploy_state()
        assert restarts == []
        msg = st["last_result"]["message"]
        assert st["last_result"]["success"] is False
        assert "docs/discord-audit-recheck-997" in msg and "削除されています" in msg
        assert "git checkout master" in msg
        assert not any(c[0] == "merge" for c in git.calls), "master 以外では取り込まない"

    def test_non_master_branch_with_live_remote_is_not_deleted_message(self, env, monkeypatch):
        restarts, _ = env
        monkeypatch.setattr(svc, "_git", FakeGit(branch="feature-x"))
        svc._deploy_running = True
        svc._run_deploy(1)
        msg = svc.get_deploy_state()["last_result"]["message"]
        assert restarts == [] and "feature-x" in msg and "削除されています" not in msg

    def test_detached_head_is_rejected(self, env, monkeypatch):
        restarts, _ = env
        monkeypatch.setattr(svc, "_git", FakeGit(branch="HEAD"))
        svc._deploy_running = True
        svc._run_deploy(1)
        assert restarts == []
        assert "detached HEAD" in svc.get_deploy_state()["last_result"]["message"]

    def test_fetch_failure_does_not_restart(self, env, monkeypatch):
        restarts, _ = env
        monkeypatch.setattr(svc, "_git", FakeGit(fetch=_cp(128, "", "fatal: unable to access")))
        svc._deploy_running = True
        svc._run_deploy(1)
        msg = svc.get_deploy_state()["last_result"]["message"]
        assert restarts == [] and "取得できませんでした" in msg and "unable to access" in msg

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
        """ブランチ・コマンドを呼び出し側から受け取らない(固定の fetch + merge --ff-only のみ)。"""
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


class TestPostStartCheck:
    """Issue #1158: 更新して再起動した後、子プロセスの生存と全カメラの接続を確かめて結果に載せる。"""

    @pytest.fixture
    def cams(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(svc, "CAMERA_STATUS_FILE", str(tmp_path / "cam.json"))
        monkeypatch.setattr(config, "CAMERAS", [
            {"name": "玄関カメラ"}, {"name": "庭カメラ"}, {"name": "旧カメラ", "enabled": False},
        ])
        monkeypatch.setattr(svc, "_child_status_provider", lambda: {"camera_monitor": True, "scheduler": True})
        return tmp_path / "cam.json"

    @staticmethod
    def _write_connected(path, **times):
        import json
        path.write_text(json.dumps({"connected_at": times}), encoding="utf-8")

    def test_pending_until_one_minute_after_start(self, env, cams):
        assert svc.post_start_check(now=svc._PROCESS_STARTED_AT + 30)["state"] == "pending"

    def test_ok_when_children_alive_and_all_cameras_connected_after_start(self, env, cams):
        t = svc._PROCESS_STARTED_AT + 5
        self._write_connected(cams, 玄関カメラ=t, 庭カメラ=t)
        assert svc.post_start_check(now=svc._PROCESS_STARTED_AT + 61) == {"state": "ok", "text": "起動後チェック: OK"}

    def test_ng_names_camera_not_connected_since_start(self, env, cams):
        self._write_connected(cams, 玄関カメラ=svc._PROCESS_STARTED_AT - 100, 庭カメラ=svc._PROCESS_STARTED_AT + 5)
        result = svc.post_start_check(now=svc._PROCESS_STARTED_AT + 61)
        assert result["state"] == "ng"
        assert "玄関カメラが未接続" in result["text"] and "庭カメラ" not in result["text"]
        assert "旧カメラ" not in result["text"]  # 無効なカメラは数えない

    def test_ng_when_scheduler_died(self, env, cams, monkeypatch):
        t = svc._PROCESS_STARTED_AT + 5
        self._write_connected(cams, 玄関カメラ=t, 庭カメラ=t)
        monkeypatch.setattr(svc, "_child_status_provider", lambda: {"camera_monitor": True, "scheduler": False})
        result = svc.post_start_check(now=svc._PROCESS_STARTED_AT + 61)
        assert result["state"] == "ng" and "Schedulerが停止" in result["text"]

    def test_attached_only_to_restart_confirmed_by_this_process(self, env, cams, monkeypatch):
        monkeypatch.setattr(svc, "_git", FakeGit())

        def restart_kills_us():
            raise _ProcessKilled()
        monkeypatch.setattr(svc, "restart_home_system", restart_kills_us)
        svc._deploy_running = True
        with pytest.raises(_ProcessKilled):
            svc._run_deploy(1)

        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() + 5)
        monkeypatch.setattr(svc, "_deploy_running", False)
        st = svc.get_deploy_state()
        assert st["last_result"]["success"] is True
        assert st["last_result"]["post_check"]["state"] == "pending"

        # さらに後のプロセス(別の再起動)では、古い更新の結果に起動後チェックを付けない
        monkeypatch.setattr(svc, "_PROCESS_STARTED_AT", time.time() + 100)
        assert "post_check" not in svc.get_deploy_state()["last_result"]

    def test_no_post_check_for_up_to_date_result(self, env, cams, monkeypatch):
        monkeypatch.setattr(svc, "_git", FakeGit(before="aaa1111", after="aaa1111"))
        svc._deploy_running = True
        svc._run_deploy(1)
        assert "post_check" not in svc.get_deploy_state()["last_result"]


class TestCameraMonitorRecordsConnections:
    def test_record_connected_keeps_other_cameras(self, tmp_path, monkeypatch):
        from monitors import camera_monitor
        path = tmp_path / "cam.json"
        monkeypatch.setattr(camera_monitor, "CAMERA_STATUS_FILE", str(path))
        camera_monitor._record_connected("玄関カメラ")
        camera_monitor._record_connected("庭カメラ")
        import json
        data = json.loads(path.read_text(encoding="utf-8"))["connected_at"]
        assert set(data) == {"玄関カメラ", "庭カメラ"}

    def test_status_file_paths_match(self):
        from monitors import camera_monitor
        assert camera_monitor.CAMERA_STATUS_FILE == svc.CAMERA_STATUS_FILE


class TestChildAliveStatus:
    def test_reports_each_child(self, monkeypatch):
        import unified_server

        class _P:
            def __init__(self, rc):
                self.rc = rc

            def poll(self):
                return self.rc

        monkeypatch.setattr(unified_server, "camera_process", _P(None))
        monkeypatch.setattr(unified_server, "scheduler_process", _P(1))
        assert unified_server._child_alive_status() == {"camera_monitor": True, "scheduler": False}
