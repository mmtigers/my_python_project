# MY_HOME_SYSTEM/tests/test_system_info_service.py
"""services/system_info_service.py(起動履歴・Gitの状態)のテスト。

実際の git・ネットワーク・systemctl は使わず、`system_maintenance_service._git` を差し替える。
"""
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
from services import system_info_service as svc
from services import system_maintenance_service as maint

SEP = svc._FIELD_SEP


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeGit:
    """`maintenance._git(*args, timeout=...)` の差し替え。引数列の先頭一致で応答を返す。"""

    def __init__(self, responses=None, fail=()):
        self.responses = responses or {}
        self.fail = set(fail)
        self.calls = []

    def __call__(self, *args, timeout):
        self.calls.append(args)
        key = " ".join(args)
        for prefix in self.fail:
            if key.startswith(prefix):
                return _cp(1, "", "fatal: boom")
        for prefix, out in self.responses.items():
            if key.startswith(prefix):
                return _cp(0, out + "\n")
        return _cp(1, "", f"unexpected: {key}")


def _log(*commits):
    return "\n".join(f"{sha}{SEP}{date}{SEP}{subject}" for sha, date, subject in commits)


@pytest.fixture
def env(tmp_path, monkeypatch, isolated_db):
    monkeypatch.setattr(maint, "DEPLOY_STATE_FILE", str(tmp_path / "deploy_state.json"))
    monkeypatch.setattr(maint, "RESTART_MARKER_FILE", str(tmp_path / "restart_marker.json"))
    monkeypatch.setattr(svc, "_git_cache", None)
    return tmp_path


def _use_git(monkeypatch, fake):
    monkeypatch.setattr(maint, "_git", fake)
    return fake


GIT_OK = {
    "rev-parse --short HEAD": "abc1234",
    "log -1 --format=%s": "ダッシュボードを直した",
    "rev-parse --abbrev-ref HEAD": "master",
}


class TestRecordBoot:
    def test_records_commit_branch_pid_and_unknown_reason(self, env, monkeypatch):
        _use_git(monkeypatch, FakeGit(GIT_OK))
        assert svc.record_boot() is True
        rows = svc.get_boot_history()
        assert len(rows) == 1
        row = rows[0]
        assert (row["commit_sha"], row["commit_subject"], row["branch"]) == ("abc1234", "ダッシュボードを直した", "master")
        assert row["pid"] == os.getpid()
        assert row["reason"] == svc.REASON_UNKNOWN

    def test_still_records_when_git_is_unavailable(self, env, monkeypatch):
        _use_git(monkeypatch, FakeGit(fail=("rev-parse",)))
        assert svc.record_boot() is True
        row = svc.get_boot_history()[0]
        assert row["commit_sha"] is None and row["commit_subject"] is None

    def test_git_exception_does_not_break_boot_recording(self, env, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(maint, "_git", boom)
        assert svc.record_boot() is True
        assert svc.get_boot_history()[0]["commit_sha"] is None

    def test_db_failure_returns_false_without_raising(self, env, monkeypatch):
        _use_git(monkeypatch, FakeGit(GIT_OK))
        monkeypatch.setattr(svc, "get_db_cursor", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
        assert svc.record_boot() is False

    def test_history_is_newest_first_and_limited(self, env, monkeypatch):
        _use_git(monkeypatch, FakeGit(GIT_OK))
        with get_db_cursor(commit=True) as cur:
            for i in range(15):
                cur.execute(
                    "INSERT INTO server_boot_events (booted_at, commit_sha, reason) VALUES (?, ?, 'unknown')",
                    (f"2026-10-{i + 1:02d}T10:00:00+09:00", f"sha{i:04d}"),
                )
        rows = svc.get_boot_history(10)
        assert len(rows) == 10
        assert rows[0]["commit_sha"] == "sha0014" and rows[-1]["commit_sha"] == "sha0005"


class TestBootReason:
    def _write_deploy(self, phase, requested_at):
        maint._write_deploy_state({
            "run_id": 1, "phase": phase, "success": None, "message": "", "from": "a", "to": "b",
            "restart_requested_at": requested_at, "finished_at": None,
        })

    def test_recent_deploy_restart_is_update(self, env):
        self._write_deploy(maint.PHASE_RESTARTING, time.time() - 60)
        assert svc._determine_boot_reason(time.time()) == svc.REASON_UPDATE

    def test_old_deploy_restart_is_not_attributed(self, env):
        self._write_deploy(maint.PHASE_RESTARTING, time.time() - svc.BOOT_REASON_WINDOW_SEC - 10)
        assert svc._determine_boot_reason(time.time()) == svc.REASON_UNKNOWN

    def test_finished_deploy_is_not_attributed(self, env):
        self._write_deploy(maint.PHASE_DONE, time.time() - 60)
        assert svc._determine_boot_reason(time.time()) == svc.REASON_UNKNOWN

    def test_recent_manual_restart_marker_is_manual(self, env):
        (env / "restart_marker.json").write_text(json.dumps({"requested_at": time.time() - 30}))
        assert svc._determine_boot_reason(time.time()) == svc.REASON_MANUAL

    def test_deploy_wins_over_manual_marker(self, env):
        self._write_deploy(maint.PHASE_RESTARTING, time.time() - 60)
        (env / "restart_marker.json").write_text(json.dumps({"requested_at": time.time() - 30}))
        assert svc._determine_boot_reason(time.time()) == svc.REASON_UPDATE

    def test_corrupt_marker_is_unknown(self, env):
        (env / "restart_marker.json").write_text("{not json")
        assert svc._determine_boot_reason(time.time()) == svc.REASON_UNKNOWN

    def test_restart_home_system_writes_the_marker(self, env, monkeypatch):
        monkeypatch.setattr(maint.subprocess, "run", lambda *a, **k: None)
        assert maint.restart_home_system()[0] is True
        assert svc._determine_boot_reason(time.time()) == svc.REASON_MANUAL


FMT = f"--format=%h{SEP}%cd{SEP}%s"


def _status_git(*, behind="2", ahead="0", fetch_fails=False, upstream=True):
    responses = {
        "rev-parse --short HEAD": "bbb2222",
        "rev-parse --abbrev-ref HEAD": "master",
        f"log -1 {FMT} --date=format:%m/%d %H:%M HEAD": _log(("bbb2222", "10/09 12:00", "手元の最新")),
        f"log -1 {FMT} --date=format:%m/%d %H:%M @{{u}}": _log(("ddd4444", "10/10 09:00", "GitHubの最新")),
        "rev-list --count HEAD..@{u}": behind,
        "rev-list --count @{u}..HEAD": ahead,
        "rev-list --abbrev-commit HEAD..@{u}": "ddd4444\nccc3333",
        f"log -n{svc.RECENT_COMMITS_LIMIT} {FMT} --date=format:%m/%d %H:%M @{{u}}": _log(
            ("ddd4444", "10/10 09:00", "GitHubの最新"),
            ("ccc3333", "10/09 18:00", "途中のコミット"),
            ("bbb2222", "10/09 12:00", "手元の最新"),
            ("aaa1111", "10/08 12:00", "古いコミット"),
        ),
    }
    if upstream:
        responses["rev-parse --abbrev-ref @{u}"] = "origin/master"
    if not fetch_fails:
        responses["fetch --quiet"] = ""
    return FakeGit(responses, fail=("fetch --quiet",) if fetch_fails else ())


def _boot(sha, subject="x"):
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO server_boot_events (booted_at, commit_sha, commit_subject, reason) VALUES (?, ?, ?, 'unknown')",
            ("2026-10-09T13:00:00+09:00", sha, subject),
        )


class TestGitStatus:
    def test_three_levels_and_commit_marks(self, env, monkeypatch):
        _use_git(monkeypatch, _status_git())
        _boot("aaa1111", "古いコミット")
        st = svc.get_git_status()
        assert st["ok"] is True
        assert st["head"]["sha"] == "bbb2222" and st["remote"]["sha"] == "ddd4444"
        assert st["running"]["sha"] == "aaa1111"
        assert st["pending_restart"] is True            # 起動中(aaa1111) != 手元(bbb2222)
        assert (st["behind"], st["ahead"]) == (2, 0)
        marks = {c["sha"]: (c["pulled"], c["running"]) for c in st["recent"]}
        assert marks == {
            "ddd4444": (False, False), "ccc3333": (False, False),
            "bbb2222": (True, False), "aaa1111": (True, True),
        }

    def test_running_equals_head_means_no_pending_restart(self, env, monkeypatch):
        _use_git(monkeypatch, _status_git(behind="0"))
        _boot("bbb2222")
        assert svc.get_git_status()["pending_restart"] is False

    def test_no_boot_record_leaves_running_empty(self, env, monkeypatch):
        _use_git(monkeypatch, _status_git())
        st = svc.get_git_status()
        assert st["running"] is None and st["pending_restart"] is None

    def test_fetch_failure_still_reports_local_comparison(self, env, monkeypatch):
        _use_git(monkeypatch, _status_git(fetch_fails=True))
        st = svc.get_git_status()
        assert st["ok"] is True and st["fetch_ok"] is False
        assert "問い合わせできませんでした" in st["error"]
        assert st["remote"] is not None

    def test_no_upstream_reports_error_without_fetching(self, env, monkeypatch):
        fake = _use_git(monkeypatch, _status_git(upstream=False))
        # `rev-parse --abbrev-ref @{u}` が失敗する(追跡先なし)
        fake.fail.add("rev-parse --abbrev-ref @{u}")
        st = svc.get_git_status()
        assert st["ok"] is True and st["remote"] is None
        assert "追跡先" in st["error"]
        assert not any(c[0] == "fetch" for c in fake.calls)

    def test_not_a_repository_is_ok_false(self, env, monkeypatch):
        _use_git(monkeypatch, FakeGit(fail=("rev-parse",)))
        st = svc.get_git_status()
        assert st["ok"] is False and "見つかりません" in st["error"]

    def test_success_is_cached_and_force_refreshes(self, env, monkeypatch):
        fake = _use_git(monkeypatch, _status_git())
        svc.get_git_status()
        n = len(fake.calls)
        svc.get_git_status()
        assert len(fake.calls) == n
        svc.get_git_status(force=True)
        assert len(fake.calls) > n

    def test_failure_is_not_cached(self, env, monkeypatch):
        _use_git(monkeypatch, FakeGit(fail=("rev-parse",)))
        svc.get_git_status()
        fake = _use_git(monkeypatch, _status_git())
        assert svc.get_git_status()["ok"] is True
        assert fake.calls

    def test_unexpected_exception_returns_error_dict(self, env, monkeypatch):
        monkeypatch.setattr(svc, "_compute_git_status", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        st = svc.get_git_status()
        assert st["ok"] is False and st["recent"] == []


class TestRetentionAndSchema:
    def test_boot_events_is_a_retention_target(self):
        from services import db_retention_service as retention
        targets = {t.table: t for t in retention.RETENTION_TARGETS}
        assert targets["server_boot_events"].timestamp_column == "booted_at"
        assert targets["server_boot_events"].retention_days_attr == "DB_ROW_RETENTION_EVENT_DAYS"
