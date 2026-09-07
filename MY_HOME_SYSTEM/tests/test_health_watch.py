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


class TestCheckAppLogs:
    """check_app_logs のIssue #339層2調査で発覚した誤検知バグの回帰テスト。

    pip_install.log のように行に一切タイムスタンプが無いファイルは、
    LogAnalyzer._analyze_file 内で effective_dt が常に None になり、
    start_date によるフィルタが効かないため、ファイルの中身が更新されて
    いなくても毎回無条件に「新規エラー」として再カウントされてしまっていた。
    """

    def test_ignores_stale_file_with_no_timestamped_lines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
        stale_log = tmp_path / "pip_install.log"
        stale_log.write_text(
            "ERROR: pip's dependency resolver does not currently take into account...\n"
        )
        # 「前回チェック以降」より古い更新日時にする(=中身は更新されていない)
        old_time = (datetime.datetime.now() - datetime.timedelta(hours=1)).timestamp()
        os.utime(stale_log, (old_time, old_time))

        since = datetime.datetime.now() - datetime.timedelta(minutes=30)
        result = health_watch.check_app_logs(since)

        assert result is None

    def test_still_detects_error_in_recently_updated_file_without_timestamps(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
        recent_log = tmp_path / "pip_install.log"
        recent_log.write_text("ERROR: something just failed\n")
        # ファイル自体は since より新しく更新されている
        recent_time = datetime.datetime.now().timestamp()
        os.utime(recent_log, (recent_time, recent_time))

        since = datetime.datetime.now() - datetime.timedelta(minutes=30)
        result = health_watch.check_app_logs(since)

        assert result is not None
        assert "pip_install.log" in result


class TestRunChecksHookGating:
    """run_checks内でのフック発火が通知抑制(_should_notify)と連動していること。"""

    def _patch_checks(self, monkeypatch, tmp_path, anomaly: bool):
        # 実コマンド依存のチェック群を決定的な結果に差し替える
        monkeypatch.setattr(
            health_watch, "check_service_active",
            (lambda: "home_system.service が active ではありません") if anomaly else (lambda: None),
        )
        for name in ("check_disk_usage", "check_memory_usage", "check_nas_mount", "check_deploy_config_drift"):
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


class TestCheckAppLogsIgnoresInvestigationHookOutput:
    """層2フック(claude_investigate.sh)の出力先 claude_investigate.log には調査結果として
    "ERROR"/"Traceback" 等の語が常に含まれる。これを check_app_logs が読むと、翌回の
    チェックで新規エラー扱い→再発報→フック再起動→さらに出力、の自己増殖ループになる。"""

    def test_claude_investigate_log_is_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
        hook_log = tmp_path / "claude_investigate.log"
        hook_log.write_text(
            f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [INFO] hook: 調査結果\n"
            "Traceback (most recent call last):\n"
            "RuntimeError: ERROR found in home_system.log\n"
        )
        since = datetime.datetime.now() - datetime.timedelta(minutes=30)
        assert health_watch.check_app_logs(since) is None

    def test_other_logs_are_still_checked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
        (tmp_path / "home_system.log").write_text(
            f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [ERROR] quest_router: boom\n"
        )
        since = datetime.datetime.now() - datetime.timedelta(minutes=30)
        result = health_watch.check_app_logs(since)
        assert result is not None and "home_system.log" in result


class TestCheckDeployConfigDrift:
    """check_deploy_config_drift(チェック7: 実機構成ドリフト検知)のテスト。

    crontab -l / 実機側ファイル(/etc/...)は tmp_path 上の疑似ファイルと
    subprocess.run のモックで代替し、コメント・空行の無視、差分・未導入・未登録の
    報告、異常なし時の None を検証する。
    """

    REPO_CRONTAB = (
        "# ラズパイ一次ヘルスチェック\n"
        "10 * * * * /home/masahiro/develop/MY_HOME_SYSTEM/run_task.sh monitors/health_watch.py\n"
        "\n"
        "0 4 * * * /home/masahiro/develop/MY_HOME_SYSTEM/run_task.sh services/backup_service.py\n"
    )
    REPO_UNIT = "[Unit]\nDescription=My Home System Server\n\n[Service]\nExecStart=/x/start_all.sh\n"

    def _setup_repo(self, tmp_path, monkeypatch, crontab=None, unit=None, logrotate="/x/logs/*.log {\n  daily\n}\n"):
        repo_cron = tmp_path / "repo" / "deploy" / "cron" / "crontab"
        repo_cron.parent.mkdir(parents=True)
        repo_cron.write_text(crontab if crontab is not None else self.REPO_CRONTAB, encoding="utf-8")
        systemd_dir = tmp_path / "repo" / "MY_HOME_SYSTEM" / "deploy" / "systemd"
        systemd_dir.mkdir(parents=True)
        (systemd_dir / "home_system.service").write_text(unit if unit is not None else self.REPO_UNIT, encoding="utf-8")
        (systemd_dir / "README.md").write_text("# README\n", encoding="utf-8")
        logrotate_dir = tmp_path / "repo" / "MY_HOME_SYSTEM" / "deploy" / "logrotate"
        logrotate_dir.mkdir(parents=True)
        (logrotate_dir / "home_system").write_text(logrotate, encoding="utf-8")
        (logrotate_dir / "README.md").write_text("# README\n", encoding="utf-8")

        host_systemd = tmp_path / "host" / "systemd"
        host_logrotate = tmp_path / "host" / "logrotate.d"
        host_systemd.mkdir(parents=True)
        host_logrotate.mkdir(parents=True)

        monkeypatch.setattr(health_watch, "TRACKED_CRONTAB", str(repo_cron))
        monkeypatch.setattr(health_watch, "TRACKED_CONFIG_DIRS", [
            (str(systemd_dir), "*.service", str(host_systemd)),
            (str(logrotate_dir), "*", str(host_logrotate)),
        ])
        return host_systemd, host_logrotate

    @staticmethod
    def _mock_crontab(monkeypatch, stdout, returncode=0, stderr=""):
        def fake_run(cmd, **kwargs):
            assert cmd == ["crontab", "-l"]
            return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
        monkeypatch.setattr(health_watch.subprocess, "run", fake_run)

    def test_no_drift_ignores_comments_and_blank_lines(self, tmp_path, monkeypatch):
        host_systemd, host_logrotate = self._setup_repo(tmp_path, monkeypatch)
        # crontab -e で付くヘッダ・コメント違い・空行の有無は差分とみなさない
        self._mock_crontab(monkeypatch,
            "# DO NOT EDIT THIS FILE - edit the master and reinstall.\n"
            "# (crontab installed on Mon Sep  7 10:00:00 2026)\n"
            "10 * * * * /home/masahiro/develop/MY_HOME_SYSTEM/run_task.sh monitors/health_watch.py\n"
            "0 4 * * * /home/masahiro/develop/MY_HOME_SYSTEM/run_task.sh services/backup_service.py   \n")
        (host_systemd / "home_system.service").write_text(
            "# installed 2026-09\n" + self.REPO_UNIT, encoding="utf-8")
        (host_logrotate / "home_system").write_text("/x/logs/*.log {\n  daily\n}\n", encoding="utf-8")

        assert health_watch.check_deploy_config_drift() is None

    def test_reports_crontab_job_difference(self, tmp_path, monkeypatch):
        host_systemd, host_logrotate = self._setup_repo(tmp_path, monkeypatch)
        self._mock_crontab(monkeypatch,
            "10 * * * * /home/masahiro/develop/MY_HOME_SYSTEM/run_task.sh monitors/health_watch.py\n"
            "0 5 * * * /home/masahiro/develop/MY_HOME_SYSTEM/run_task.sh services/backup_service.py\n")
        (host_systemd / "home_system.service").write_text(self.REPO_UNIT, encoding="utf-8")
        (host_logrotate / "home_system").write_text("/x/logs/*.log {\n  daily\n}\n", encoding="utf-8")

        result = health_watch.check_deploy_config_drift()

        assert result is not None
        assert "crontab: 差分 2行" in result
        assert "-0 4 * * *" in result and "+0 5 * * *" in result
        assert "home_system.service" not in result

    def test_reports_crontab_not_registered(self, tmp_path, monkeypatch):
        host_systemd, host_logrotate = self._setup_repo(tmp_path, monkeypatch)
        self._mock_crontab(monkeypatch, "", returncode=1, stderr="no crontab for masahiro\n")
        (host_systemd / "home_system.service").write_text(self.REPO_UNIT, encoding="utf-8")
        (host_logrotate / "home_system").write_text("/x/logs/*.log {\n  daily\n}\n", encoding="utf-8")

        result = health_watch.check_deploy_config_drift()

        assert result is not None
        assert "crontab: 実機に未登録です (no crontab for masahiro)" in result

    def test_reports_missing_and_differing_host_files(self, tmp_path, monkeypatch):
        host_systemd, host_logrotate = self._setup_repo(tmp_path, monkeypatch)
        self._mock_crontab(monkeypatch, self.REPO_CRONTAB)
        # systemd ユニットは未導入、logrotate は内容が異なる
        (host_logrotate / "home_system").write_text("/x/logs/*.log {\n  weekly\n}\n", encoding="utf-8")

        result = health_watch.check_deploy_config_drift()

        assert result is not None
        assert f"{host_systemd / 'home_system.service'}: 実機に未導入です" in result
        assert f"{host_logrotate / 'home_system'}: 差分 2行" in result
        # README は導入対象ではないため未導入として報告しない
        assert "README.md" not in result
        assert "crontab" not in result.replace("実機構成がリポジトリ", "")

    def test_diff_summary_is_truncated(self, tmp_path, monkeypatch):
        many_jobs = "".join(f"{i} * * * * /bin/job{i}\n" for i in range(10))
        host_systemd, host_logrotate = self._setup_repo(tmp_path, monkeypatch, crontab=many_jobs)
        self._mock_crontab(monkeypatch, "")  # 実機は空(登録はある)
        (host_systemd / "home_system.service").write_text(self.REPO_UNIT, encoding="utf-8")
        (host_logrotate / "home_system").write_text("/x/logs/*.log {\n  daily\n}\n", encoding="utf-8")

        result = health_watch.check_deploy_config_drift()

        assert "crontab: 差分 10行" in result
        assert "ほか7行" in result

    def test_skips_when_repo_crontab_absent(self, tmp_path, monkeypatch):
        """リポジトリ側に crontab が無い環境ではチェックを失敗させず、比較対象外にする。"""
        host_systemd, host_logrotate = self._setup_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(health_watch, "TRACKED_CRONTAB", str(tmp_path / "missing"))
        monkeypatch.setattr(health_watch.subprocess, "run",
                            MagicMock(side_effect=AssertionError("crontab -l を呼んではいけない")))
        (host_systemd / "home_system.service").write_text(self.REPO_UNIT, encoding="utf-8")
        (host_logrotate / "home_system").write_text("/x/logs/*.log {\n  daily\n}\n", encoding="utf-8")

        assert health_watch.check_deploy_config_drift() is None

    def test_run_checks_includes_deploy_config_check(self, tmp_path, monkeypatch):
        """run_checks のチェック一覧に組み込まれ、異常時は通知本文に含まれること。"""
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(health_watch, "MARKER_FILE", str(tmp_path / "marker"))
        monkeypatch.setattr(health_watch, "NOTIFY_STATE_FILE", str(tmp_path / "state"))
        for name in ("check_service_active", "check_disk_usage", "check_memory_usage", "check_nas_mount"):
            monkeypatch.setattr(health_watch, name, lambda: None)
        monkeypatch.setattr(health_watch, "check_journal_errors", lambda since: None)
        monkeypatch.setattr(health_watch, "check_app_logs", lambda since: None)
        monkeypatch.setattr(health_watch, "check_deploy_config_drift",
                            lambda: "実機構成がリポジトリ(deploy/)と一致しません:\n  - crontab: 差分 1行 (x)")
        monkeypatch.setattr(config, "HEALTH_WATCH_INVESTIGATE_HOOK", None, raising=False)
        sent = []
        monkeypatch.setattr(health_watch, "send_push", lambda msgs, **kw: sent.append(msgs) or True)

        assert health_watch.run_checks() == 0
        assert sent and "crontab: 差分 1行" in sent[0][0]["text"]
