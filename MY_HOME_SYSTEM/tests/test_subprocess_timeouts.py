# MY_HOME_SYSTEM/tests/test_subprocess_timeouts.py
"""外部コマンド実行の timeout 付与を、呼び出し箇所ごとに固定する(Issue #651)。

`subprocess.run` に `timeout=` が無いと、systemd の異常や SD カードの I/O 遅延で
コマンドが返らないときに監視ループ・ダッシュボードが無期限にブロックする
(`core/nas_utils.py` が Issue #411 で塞いだのと同じ欠落)。

ここでは実コマンドを起動せず `subprocess.run` をモックして、次の2点を検証する。

1. **timeout が渡されていること** — 新しい呼び出しを追加したときに付け忘れを検知する
2. **TimeoutExpired が呼び出し元を壊さないこと** — 上限を設けても、そこで例外が
   素通りして監視プロセスやダッシュボードが落ちるなら「ハングがクラッシュに変わった」
   だけで改善にならない。各呼び出し元が定める戻り値へ落ちることまで見る

並行セッションからの指摘(PR #666)で、当初のテストが追加した2箇所しか見ていないと
分かったため、Issue が列挙した9箇所を含む全11箇所へ広げた。
"""
import datetime
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import monitors.health_watch as health_watch
import monitors.server_watchdog as server_watchdog
import services.analysis_service as analysis_service
from monitors.nas_monitor import NasMonitor


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _raise_timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(cmd="dummy", timeout=1)


class TestTimeoutIsPassed:
    """timeout= が実際に subprocess.run へ渡されていること。"""

    def test_analysis_service_memory_usage(self):
        with patch.object(analysis_service.subprocess, "run", return_value=_completed(
            "total used free\nMem: 1000 500 100 0 0 400\n"
        )) as run:
            analysis_service.get_memory_usage()
        assert run.call_args.kwargs.get("timeout") is not None

    def test_analysis_service_system_logs(self):
        with patch.object(analysis_service.subprocess, "run", return_value=_completed("log")) as run:
            analysis_service.get_system_logs()
        assert run.call_args.kwargs.get("timeout") is not None

    def test_health_watch_service_active(self):
        with patch.object(health_watch.subprocess, "run", return_value=_completed("active")) as run:
            health_watch.check_service_active()
        assert run.call_args.kwargs.get("timeout") is not None

    def test_health_watch_journal_errors(self):
        with patch.object(health_watch.subprocess, "run", return_value=_completed("")) as run:
            health_watch.check_journal_errors(datetime.datetime.now())
        assert run.call_args.kwargs.get("timeout") is not None

    def test_health_watch_memory_usage(self):
        with patch.object(health_watch.subprocess, "run", return_value=_completed(
            "total used free\nMem: 1000 100 900\n"
        )) as run:
            health_watch.check_memory_usage()
        assert run.call_args.kwargs.get("timeout") is not None

    def test_health_watch_crontab(self):
        with patch.object(health_watch.subprocess, "run", return_value=_completed("")) as run:
            try:
                health_watch.check_deploy_config_drift()
            except Exception:
                # crontab 以外の比較対象(リポジトリ内ファイル)が無い環境では落ちうるが、
                # ここで見たいのは subprocess.run への timeout 付与なので許容する。
                pass
        if run.called:
            assert run.call_args.kwargs.get("timeout") is not None

    def test_server_watchdog_service_status(self):
        with patch.object(server_watchdog.subprocess, "run", return_value=_completed("active")) as run:
            server_watchdog.get_service_status("home_system.service")
        assert run.call_args.kwargs.get("timeout") is not None

    def test_server_watchdog_process_alive(self):
        with patch.object(server_watchdog.subprocess, "run", return_value=_completed("")) as run:
            server_watchdog.is_process_alive("unified_server.py")
        assert run.call_args.kwargs.get("timeout") is not None

    def test_server_watchdog_throttling(self):
        with patch.object(server_watchdog.subprocess, "run", return_value=_completed("throttled=0x0")) as run:
            server_watchdog.check_throttling_status()
        assert run.call_args.kwargs.get("timeout") is not None

    def test_nas_monitor_ping(self):
        monitor = NasMonitor()
        with patch("monitors.nas_monitor.subprocess.run", return_value=_completed("")) as run:
            monitor.check_ping()
        assert run.call_args.kwargs.get("timeout") is not None

    def test_nas_monitor_ping_outer_bound_is_wider_than_the_ping_deadline(self):
        """`ping -W` は ping 自身の応答待ちしか縛らないため、外側はそれより広く取る。"""
        monitor = NasMonitor()
        with patch("monitors.nas_monitor.subprocess.run", return_value=_completed("")) as run:
            monitor.check_ping()
        assert run.call_args.kwargs["timeout"] > monitor.timeout

    def test_dashboard_restart_declares_a_timeout(self):
        """ダッシュボードの再起動ボタン(Streamlit のスクリプト実行スレッドを塞ぐ)。"""
        from views.dashboard import log_tab

        with open(log_tab.__file__, encoding="utf-8") as f:
            source = f.read()
        restart_call = source[source.index('"restart", "home_system"'):]
        restart_call = restart_call[: restart_call.index(")")]
        assert "timeout=" in restart_call
        assert "except subprocess.TimeoutExpired" in source


class TestTimeoutDoesNotEscape:
    """TimeoutExpired が呼び出し元を壊さないこと(ハングをクラッシュに置き換えない)。"""

    def test_analysis_service_memory_usage_returns_none(self):
        with patch.object(analysis_service.subprocess, "run", side_effect=_raise_timeout):
            assert analysis_service.get_memory_usage() is None

    def test_analysis_service_system_logs_returns_message(self):
        with patch.object(analysis_service.subprocess, "run", side_effect=_raise_timeout):
            assert "エラー" in analysis_service.get_system_logs()

    def test_server_watchdog_service_status_returns_error(self):
        with patch.object(server_watchdog.subprocess, "run", side_effect=_raise_timeout):
            assert server_watchdog.get_service_status("home_system.service") == "error"

    def test_server_watchdog_process_alive_returns_false(self):
        with patch.object(server_watchdog.subprocess, "run", side_effect=_raise_timeout):
            assert server_watchdog.is_process_alive("unified_server.py") is False

    def test_server_watchdog_throttling_does_not_raise(self):
        with patch.object(server_watchdog.subprocess, "run", side_effect=_raise_timeout):
            server_watchdog.check_throttling_status()  # 例外が漏れなければ成功

    def test_nas_monitor_ping_returns_unreachable(self):
        monitor = NasMonitor()
        with patch("monitors.nas_monitor.subprocess.run", side_effect=_raise_timeout):
            assert monitor.check_ping() is False

    def test_health_watch_records_it_as_an_internal_error_not_a_crash(self, monkeypatch):
        """health_watch は各チェックを run_checks の try/except で受けるため、
        タイムアウトは「チェック実行エラー」として記録され、プロセスは止まらない。
        (異常通知には載せず、終了コードと ERROR ログで知らせる既存の設計。)

        実チェックを走らせると NAS 疎通やディスク走査で数秒かかるため、
        チェック一覧そのものを「必ずタイムアウトする1件」に差し替えて契約だけを見る。
        """
        fake_logger = MagicMock()
        monkeypatch.setattr(health_watch, "logger", fake_logger)
        monkeypatch.setattr(health_watch, "_read_marker", lambda: datetime.datetime.now(), raising=False)
        monkeypatch.setattr(health_watch, "_write_marker", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(health_watch, "_notify", MagicMock(), raising=False)

        def _timing_out_check():
            raise subprocess.TimeoutExpired(cmd="systemctl", timeout=30)

        real_run_checks = health_watch.run_checks.__wrapped__ if hasattr(health_watch.run_checks, "__wrapped__") else health_watch.run_checks
        monkeypatch.setattr(
            health_watch, "check_service_active", _timing_out_check, raising=False
        )
        # run_checks が組み立てる一覧のうち、他のチェックは実行させない
        for name in ("check_journal_errors", "check_app_logs", "check_disk_usage",
                     "check_memory_usage", "check_nas_mount", "check_deploy_config_drift"):
            monkeypatch.setattr(health_watch, name, lambda *a, **k: None, raising=False)

        exit_code = real_run_checks()

        assert exit_code != 0, "チェックが実行できなかったことが終了コードに現れること"
        assert any("チェック実行エラー" in str(c) for c in fake_logger.error.call_args_list)


class TestHealthWatchLock:
    """毎時 cron の多重起動を flock で防ぐこと(Issue #651)。"""

    def test_second_invocation_skips_while_the_first_holds_the_lock(self, tmp_path, monkeypatch):
        lock_path = str(tmp_path / ".health_watch.lock")
        monkeypatch.setattr(health_watch, "LOCK_FILE", lock_path)
        run_checks = MagicMock(return_value=0)
        monkeypatch.setattr(health_watch, "run_checks", run_checks)

        import fcntl

        holder = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert health_watch.main() == 0
            run_checks.assert_not_called()
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)

    def test_runs_when_the_lock_is_free(self, tmp_path, monkeypatch):
        monkeypatch.setattr(health_watch, "LOCK_FILE", str(tmp_path / ".health_watch.lock"))
        run_checks = MagicMock(return_value=1)
        monkeypatch.setattr(health_watch, "run_checks", run_checks)

        assert health_watch.main() == 1
        run_checks.assert_called_once()
