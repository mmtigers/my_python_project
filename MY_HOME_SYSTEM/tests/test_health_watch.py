# MY_HOME_SYSTEM/tests/test_health_watch.py
"""
monitors/health_watch.py の層2フック発火(_fire_investigate_hook、Issue #339)のテスト。

層1のチェック関数群(systemctl/journalctl/free等の実コマンド依存)はここでは
モックし、フック発火のガード条件(未設定・実行不能・通知抑制との連動)と
fire-and-forget起動の内容(引数・標準入力・detach)のみを検証する。
実際のclaude -p呼び出し(scripts/claude_investigate.sh)は実機依存のためテスト対象外。
"""
import datetime
import os
import stat
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import monitors.health_watch as health_watch


class TestFireInvestigateHook:
    def test_noop_when_hook_unset(self, monkeypatch):
        """既定(未設定)では何も起動しない = 従来挙動と完全に同一であること。"""
        monkeypatch.setattr(config, "HEALTH_WATCH_INVESTIGATE_HOOK", None, raising=False)
        with patch.object(health_watch.subprocess, "Popen") as popen:
            health_watch._fire_investigate_hook(["異常A"], datetime.datetime.now())
        popen.assert_not_called()

    def test_noop_with_error_log_when_hook_not_executable(self, tmp_path, monkeypatch):
        """パスが存在しない/実行権限がない場合はエラーログのみで起動しないこと。"""
        monkeypatch.setattr(
            config, "HEALTH_WATCH_INVESTIGATE_HOOK", str(tmp_path / "missing.sh"), raising=False
        )
        fake_logger = MagicMock()
        monkeypatch.setattr(health_watch, "logger", fake_logger)
        with patch.object(health_watch.subprocess, "Popen") as popen:
            health_watch._fire_investigate_hook(["異常A"], datetime.datetime.now())
        popen.assert_not_called()
        assert fake_logger.error.called

    def test_hook_launched_detached_with_summary_on_stdin(self, tmp_path, monkeypatch):
        hook = tmp_path / "hook.sh"
        hook.write_text("#!/bin/sh\ncat > /dev/null\n", encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setattr(config, "HEALTH_WATCH_INVESTIGATE_HOOK", str(hook), raising=False)
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path), raising=False)

        fake_proc = MagicMock(pid=12345)
        with patch.object(health_watch.subprocess, "Popen", return_value=fake_proc) as popen:
            health_watch._fire_investigate_hook(
                ["ディスク使用率が 95.0% です", "NAS がマウントされていません"],
                datetime.datetime(2026, 1, 1, 12, 0, 0),
            )

        popen.assert_called_once()
        assert popen.call_args.args[0] == [str(hook)]
        # fire-and-forget: 層1終了後もフックが生きるようdetachされていること
        assert popen.call_args.kwargs["start_new_session"] is True
        # 異常サマリが標準入力へ書き込まれ、closeされていること(script側はcatで受ける)
        written = fake_proc.stdin.write.call_args.args[0].decode("utf-8")
        assert "ディスク使用率が 95.0% です" in written
        assert "NAS がマウントされていません" in written
        assert "2026-01-01T12:00:00" in written
        fake_proc.stdin.close.assert_called_once()

    def test_popen_failure_is_swallowed_with_error_log(self, tmp_path, monkeypatch):
        """フック起動の失敗が層1本体(検知・通知)を巻き込まないこと。"""
        hook = tmp_path / "hook.sh"
        hook.write_text("#!/bin/sh\n", encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setattr(config, "HEALTH_WATCH_INVESTIGATE_HOOK", str(hook), raising=False)
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path), raising=False)
        fake_logger = MagicMock()
        monkeypatch.setattr(health_watch, "logger", fake_logger)

        with patch.object(
            health_watch.subprocess, "Popen", side_effect=OSError("spawn failed")
        ):
            # 例外が外へ漏れないこと
            health_watch._fire_investigate_hook(["異常A"], datetime.datetime.now())
        assert fake_logger.error.called


class TestRunChecksHookGating:
    """run_checks内でのフック発火が通知抑制(_should_notify)と連動していること。"""

    def _patch_checks(self, monkeypatch, tmp_path, anomaly: bool):
        # 実コマンド依存のチェック群を決定的な結果に差し替える
        monkeypatch.setattr(
            health_watch, "check_service_active",
            (lambda: "home_system.service が active ではありません") if anomaly else (lambda: None),
        )
        for name in ("check_disk_usage", "check_memory_usage", "check_nas_mount"):
            monkeypatch.setattr(health_watch, name, lambda: None)
        monkeypatch.setattr(health_watch, "check_journal_errors", lambda since: None)
        monkeypatch.setattr(health_watch, "check_app_logs", lambda since: None)
        # 状態ファイルをテスト用ディレクトリへ隔離
        monkeypatch.setattr(health_watch, "MARKER_FILE", str(tmp_path / "marker"))
        monkeypatch.setattr(health_watch, "NOTIFY_STATE_FILE", str(tmp_path / "state"))
        monkeypatch.setattr(health_watch, "send_push", MagicMock(return_value=True))

    def test_hook_fires_on_new_anomaly_and_not_while_suppressed(self, tmp_path, monkeypatch):
        self._patch_checks(monkeypatch, tmp_path, anomaly=True)
        fire = MagicMock()
        monkeypatch.setattr(health_watch, "_fire_investigate_hook", fire)

        health_watch.run_checks()
        assert fire.call_count == 1  # 新規異常セット → 発火

        health_watch.run_checks()
        assert fire.call_count == 1  # 同一異常の継続 → 通知抑制と同様に発火しない

    def test_hook_does_not_fire_without_anomaly(self, tmp_path, monkeypatch):
        self._patch_checks(monkeypatch, tmp_path, anomaly=False)
        fire = MagicMock()
        monkeypatch.setattr(health_watch, "_fire_investigate_hook", fire)

        assert health_watch.run_checks() == 0
        fire.assert_not_called()
