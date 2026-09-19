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
        for name in ("check_api_responsive", "check_disk_usage", "check_memory_usage", "check_nas_mount", "check_deploy_config_drift"):
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
    """check_deploy_config_drift(チェック8: 実機構成ドリフト検知)のテスト。

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
        for name in ("check_service_active", "check_api_responsive", "check_disk_usage", "check_memory_usage", "check_nas_mount"):
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


class TestHealthWatchMainLock:
    """Issue #651: 毎時 cron 起動の多重起動防止(flock LOCK_NB)と、外部コマンドの timeout。"""

    def test_main_runs_checks_when_lock_is_free(self, tmp_path, monkeypatch):
        monkeypatch.setattr(health_watch, "LOCK_FILE", str(tmp_path / "hw.lock"))
        monkeypatch.setattr(health_watch, "run_checks", lambda: 0)
        assert health_watch.main() == 0

    def test_main_skips_when_previous_run_still_holds_lock(self, tmp_path, monkeypatch):
        import fcntl

        lock_path = tmp_path / "hw.lock"
        monkeypatch.setattr(health_watch, "LOCK_FILE", str(lock_path))
        called = []
        monkeypatch.setattr(health_watch, "run_checks", lambda: called.append(True) or 0)

        holder = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert health_watch.main() == 0  # 前回実行中はスキップ(異常ではない)
            assert called == []
        finally:
            os.close(holder)

        # ロック解放後は実行される
        assert health_watch.main() == 0
        assert called == [True]

    def test_external_commands_have_timeout(self, monkeypatch):
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append((cmd[0], kwargs.get("timeout")))
            return health_watch.subprocess.CompletedProcess(cmd, returncode=0, stdout="active\nMem: 1 1 1 1 1 1\n", stderr="")

        monkeypatch.setattr(health_watch.subprocess, "run", fake_run)
        health_watch.check_service_active()
        health_watch.check_journal_errors(datetime.datetime(2026, 9, 16, 0, 0, 0))
        try:
            health_watch.check_memory_usage()
        except Exception:
            pass  # 出力の解析はこのテストの対象外
        assert seen, "subprocess.run が呼ばれていない"
        assert all(timeout == health_watch.SUBPROCESS_TIMEOUT_SEC for _, timeout in seen), seen


class TestQuestMasterDrift:
    """Issue #700 チェック9: quest_data.QUESTS と実機DBの quest_master の乖離検知。

    2026-09-19の棚卸しで、退役済みクエスト6件が quest_master に残って移設先の
    すごろくステップ報酬と二重取得になっていた(かつコードにある id=1023 は
    DB未登録で遊べていなかった)。**検知のみで自動修正はしない**ことも含めて固定する。
    """

    @staticmethod
    def _patch_master_quests(monkeypatch, ids):
        fake = type("FakeQuestData", (), {"QUESTS": [{"id": i} for i in ids]})
        monkeypatch.setattr(health_watch, "quest_data", fake)

    @staticmethod
    def _seed_quest_master(quest_ids):
        from core.database import get_db_cursor

        with get_db_cursor(commit=True) as cur:
            for quest_id in quest_ids:
                cur.execute(
                    "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (quest_id, f"クエスト{quest_id}", "daily", 10, 5),
                )

    def test_returns_none_when_master_and_db_match(self, isolated_db, monkeypatch):
        self._patch_master_quests(monkeypatch, [1, 2, 3])
        self._seed_quest_master([1, 2, 3])
        assert health_watch.check_quest_master_drift() is None

    def test_reports_retired_quests_left_in_db(self, isolated_db, monkeypatch):
        self._patch_master_quests(monkeypatch, [1])
        self._seed_quest_master([1, 1100, 1105])

        result = health_watch.check_quest_master_drift()
        assert result is not None
        assert "2件" in result
        assert "1100" in result and "1105" in result

    def test_reports_quests_missing_from_db(self, isolated_db, monkeypatch):
        self._patch_master_quests(monkeypatch, [1, 1023])
        self._seed_quest_master([1])

        result = health_watch.check_quest_master_drift()
        assert result is not None
        assert "1023" in result
        assert "未登録" in result

    def test_reports_both_directions_at_once(self, isolated_db, monkeypatch):
        self._patch_master_quests(monkeypatch, [1, 1023])
        self._seed_quest_master([1, 1100])

        result = health_watch.check_quest_master_drift()
        assert result is not None
        assert "1100" in result and "1023" in result

    def test_empty_master_is_reported_without_listing_every_id(self, isolated_db, monkeypatch):
        """quest_data.py の読み込み失敗を「全件が退役済み」と誤報しないこと。"""
        self._patch_master_quests(monkeypatch, [])
        self._seed_quest_master([1, 2, 3])

        result = health_watch.check_quest_master_drift()
        assert result is not None
        assert "quest_data.QUESTS が空" in result

    def test_long_id_lists_are_truncated(self, isolated_db, monkeypatch):
        stale = list(range(100, 100 + health_watch.QUEST_ID_LIST_LIMIT + 3))
        self._patch_master_quests(monkeypatch, [1])
        self._seed_quest_master([1] + stale)

        result = health_watch.check_quest_master_drift()
        assert result is not None
        assert "ほか3件" in result

    def test_check_does_not_modify_the_database(self, isolated_db, monkeypatch):
        """検知のみ = 同期(DELETE/UPSERT)は行わないこと。書き込みはDB側(mode=ro)も拒否する。"""
        from core.database import get_db_cursor

        self._patch_master_quests(monkeypatch, [1])
        self._seed_quest_master([1, 9999])

        health_watch.check_quest_master_drift()

        with get_db_cursor() as cur:
            rows = {r["quest_id"] for r in cur.execute("SELECT quest_id FROM quest_master")}
        assert rows == {1, 9999}

    def test_missing_table_is_skipped_not_reported_as_anomaly(self, tmp_path, monkeypatch):
        """DB/テーブルがまだ無い環境では、毎時の異常通知にせずスキップすること。"""
        self._patch_master_quests(monkeypatch, [1])
        empty_db = tmp_path / "empty.db"
        empty_db.touch()
        monkeypatch.setattr(config, "SQLITE_DB_PATH", str(empty_db))

        assert health_watch.check_quest_master_drift() is None

    def test_drift_is_registered_as_a_health_check(self, monkeypatch, tmp_path):
        """run_checks のチェック一覧に組み込まれ、既存の通知経路へ載ること。"""
        for name in (
            "check_service_active", "check_api_responsive", "check_disk_usage", "check_memory_usage",
            "check_nas_mount", "check_deploy_config_drift",
        ):
            monkeypatch.setattr(health_watch, name, lambda: None)
        monkeypatch.setattr(health_watch, "check_journal_errors", lambda since: None)
        monkeypatch.setattr(health_watch, "check_app_logs", lambda since: None)
        monkeypatch.setattr(
            health_watch, "check_quest_master_drift", lambda: "quest_master が一致しません"
        )
        monkeypatch.setattr(
            health_watch, "_read_marker",
            lambda: datetime.datetime.fromisoformat("2026-09-19T09:00:00"),
        )
        monkeypatch.setattr(health_watch, "_write_marker", lambda dt: None)
        monkeypatch.setattr(health_watch, "_should_notify", lambda keys, now: True)
        monkeypatch.setattr(health_watch, "_fire_investigate_hook", lambda anomalies, now: None)

        sent = []

        def fake_send_push(messages, target=None, channel=None):
            sent.append((messages, target, channel))
            return True

        monkeypatch.setattr(health_watch, "send_push", fake_send_push)

        assert health_watch.run_checks() == 0
        assert sent, "異常が通知されていない"
        assert "quest_master が一致しません" in sent[0][0][0]["text"]
        assert (sent[0][1], sent[0][2]) == ("discord", "error")


class TestJournalStdioErrors:
    """2026-09-19: サービスの標準出力/標準エラー経由のエラーを journal から検知する。

    home_system.service を Type=simple(Issue #646)へ移行した後、未捕捉例外の
    トレースバックや basicConfig のままのライブラリログは journal にしか残らなくなった。
    journald はこれらを priority info で記録するため、従来の `-p err..emerg` では
    一切拾えていなかった(実機で `ERROR:zeep...` と `Traceback` が info で残っていた)。
    """

    SINCE = datetime.datetime.fromisoformat("2026-09-19T14:00:00")

    def _fake_journal(self, monkeypatch, *, err_priority="", stdio=""):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            out = err_priority if "-p" in cmd else stdio
            return health_watch.subprocess.CompletedProcess(cmd, returncode=0, stdout=out, stderr="")

        monkeypatch.setattr(health_watch.subprocess, "run", fake_run)
        return calls

    def test_traceback_and_library_error_on_stderr_are_detected(self, monkeypatch):
        self._fake_journal(monkeypatch, stdio=(
            "INFO:     127.0.0.1:0 - \"GET /health HTTP/1.1\" 200 OK\n"
            "ERROR:zeep.xsd.types.simple:Error during xml -> python translation\n"
            "Traceback (most recent call last):\n"
            "  File \"/x/camera_monitor.py\", line 10, in <module>\n"
            "RuntimeError: boom\n"
        ))
        result = health_watch.check_journal_errors(self.SINCE)
        assert result is not None
        assert "標準出力/標準エラー" in result
        assert "RuntimeError: boom" in result

    def test_core_logger_lines_are_left_to_check_app_logs(self, monkeypatch):
        # core.logger の書式の行は logs/*.log にも出るため、journal 側では数えない(二重計上防止)
        self._fake_journal(monkeypatch, stdio=(
            "2026-09-19 14:10:00 [ERROR] newface_monitor: Failed to load data\n"
            "2026-09-19 14:10:01 [WARNING] camera: PullMessages failed: Unknown error\n"
        ))
        assert health_watch.check_journal_errors(self.SINCE) is None

    def test_warnings_and_access_logs_are_not_errors(self, monkeypatch):
        self._fake_journal(monkeypatch, stdio=(
            "INFO:     127.0.0.1:0 - \"GET /api/quest/data HTTP/1.1\" 200 OK\n"
            "WARNING:  Invalid HTTP request received.\n"
            "/x/site-packages/y.py:1: DeprecationWarning: something\n"
        ))
        assert health_watch.check_journal_errors(self.SINCE) is None

    def test_systemd_err_priority_is_still_reported(self, monkeypatch):
        self._fake_journal(monkeypatch, err_priority=(
            "Sep 19 14:05:00 raspberrypi systemd[1]: home_system.service: Main process exited, "
            "code=exited, status=1/FAILURE\n"
        ))
        result = health_watch.check_journal_errors(self.SINCE)
        assert result is not None and "err 以上" in result

    def test_both_queries_use_the_same_since_and_timeout(self, monkeypatch):
        calls = self._fake_journal(monkeypatch)
        assert health_watch.check_journal_errors(self.SINCE) is None
        assert len(calls) == 2
        for cmd in calls:
            assert cmd[cmd.index("--since") + 1] == "2026-09-19 14:00:00"
            assert cmd[cmd.index("-u") + 1] == health_watch.WATCH_SERVICE_NAME


class TestCheckApiResponsive:
    """Issue #735 (AUDIT-005): チェック2(HTTPプローブ)。

    systemctl / pgrep ベースの監視では見分けられない「プロセスは生きているが
    全APIが500」を検知するため、health_watch から実際にHTTPを投げる。
    """

    def _responses(self, monkeypatch, handler):
        monkeypatch.setattr(config, "HEALTH_WATCH_PROBE_BASE_URL", "http://127.0.0.1:8000", raising=False)
        monkeypatch.setattr(config, "HEALTH_WATCH_PROBE_TIMEOUT_SEC", 1, raising=False)
        monkeypatch.setattr(config, "HEALTH_WATCH_PROBE_DB_TIMEOUT_SEC", 1, raising=False)
        monkeypatch.setattr(health_watch.requests, "get", handler)

    def test_returns_none_when_both_probes_return_200(self, monkeypatch):
        called = []

        def fake_get(url, timeout=None):
            called.append((url, timeout))
            return MagicMock(status_code=200, text="{}")

        self._responses(monkeypatch, fake_get)
        assert health_watch.check_api_responsive() is None
        assert [url for url, _ in called] == [
            "http://127.0.0.1:8000/health",
            "http://127.0.0.1:8000/api/quest/data",
        ]

    def test_detects_503_readiness_from_health(self, monkeypatch):
        """マイグレーション失敗時に /health が返す 503 を異常として拾うこと。"""
        def fake_get(url, timeout=None):
            return MagicMock(status_code=503, text='{"status": "unhealthy", "reason": "migration_failed"}')

        self._responses(monkeypatch, fake_get)
        result = health_watch.check_api_responsive()
        assert result is not None
        assert "/health" in result and "503" in result and "migration_failed" in result

    def test_detects_db_path_failure_even_when_health_is_ok(self, monkeypatch):
        """/health は DB を触らないため、DB まで到達する経路も別に確認すること。"""
        def fake_get(url, timeout=None):
            if url.endswith("/health"):
                return MagicMock(status_code=200, text="{}")
            return MagicMock(status_code=500, text="Internal Server Error")

        self._responses(monkeypatch, fake_get)
        result = health_watch.check_api_responsive()
        assert result is not None
        assert "/api/quest/data" in result and "500" in result

    def test_connection_error_is_reported_not_raised(self, monkeypatch):
        """接続不能(サーバー停止・ポート未 listen)は例外ではなく異常文字列で返すこと
        (例外にすると run_checks が internal_errors 扱いにして通知本文へ載らない)。"""
        def fake_get(url, timeout=None):
            raise health_watch.requests.exceptions.ConnectionError("refused")

        self._responses(monkeypatch, fake_get)
        result = health_watch.check_api_responsive()
        assert result is not None
        assert "ConnectionError" in result

    def test_registered_in_run_checks(self, monkeypatch, tmp_path):
        """run_checks のチェック一覧に組み込まれ、既存の通知経路へ載ること。"""
        for name in (
            "check_service_active", "check_disk_usage", "check_memory_usage",
            "check_nas_mount", "check_deploy_config_drift", "check_quest_master_drift",
            "check_recording_stalled",
        ):
            monkeypatch.setattr(health_watch, name, lambda: None)
        monkeypatch.setattr(health_watch, "check_journal_errors", lambda since: None)
        monkeypatch.setattr(health_watch, "check_app_logs", lambda since: None)
        monkeypatch.setattr(
            health_watch, "check_api_responsive", lambda: "GET /health が 503 を返しました"
        )
        monkeypatch.setattr(health_watch, "MARKER_FILE", str(tmp_path / "marker"))
        monkeypatch.setattr(health_watch, "NOTIFY_STATE_FILE", str(tmp_path / "state"))
        monkeypatch.setattr(health_watch, "_should_notify", lambda keys, now: True)
        monkeypatch.setattr(health_watch, "_fire_investigate_hook", lambda anomalies, now: None)

        sent = []
        monkeypatch.setattr(
            health_watch, "send_push", lambda messages, **kw: sent.append(messages) or True
        )

        assert health_watch.run_checks() == 0
        assert sent, "異常が通知されていない"
        assert "GET /health が 503 を返しました" in sent[0][0]["text"]


class TestCheckRecordingStalled:
    """常時録画の停止検知(チェック10)。録画ファイル名の時刻で判定する。"""

    NOW = "2026-09-19 21:00:00+09:00"

    def _setup(self, tmp_path, monkeypatch, cameras):
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path))
        monkeypatch.setattr(config, "CAMERAS", cameras)
        monkeypatch.setattr(health_watch.os.path, "ismount", lambda path: True)
        for cam in cameras:
            (tmp_path / (cam.get("nas_folder") or cam["name"])).mkdir()

    def _touch(self, tmp_path, folder, name):
        (tmp_path / folder / name).write_bytes(b"")

    def test_recent_segments_are_healthy(self, tmp_path, monkeypatch):
        from freezegun import freeze_time
        self._setup(tmp_path, monkeypatch, [
            {"name": "玄関", "nas_folder": "entrance"},
            {"name": "庭", "nas_folder": "garden"},
        ])
        self._touch(tmp_path, "entrance", "20260919_205104.mp4")
        self._touch(tmp_path, "garden", "20260919_204330.mp4")
        with freeze_time(self.NOW):
            assert health_watch.check_recording_stalled() is None

    def test_reports_camera_whose_latest_segment_is_old(self, tmp_path, monkeypatch):
        from freezegun import freeze_time
        self._setup(tmp_path, monkeypatch, [
            {"name": "玄関", "nas_folder": "entrance"},
            {"name": "庭", "nas_folder": "garden"},
        ])
        self._touch(tmp_path, "entrance", "20260919_205104.mp4")
        self._touch(tmp_path, "garden", "20260919_180000.mp4")
        self._touch(tmp_path, "garden", "20260919_174000.mp4")
        with freeze_time(self.NOW):
            result = health_watch.check_recording_stalled()
        assert result is not None
        assert "庭(garden)" in result and "09/19 18:00" in result and "180分前" in result
        assert "玄関" not in result

    def test_reports_camera_without_any_recent_file(self, tmp_path, monkeypatch):
        from freezegun import freeze_time
        self._setup(tmp_path, monkeypatch, [{"name": "駐車場", "nas_folder": "parking"}])
        self._touch(tmp_path, "parking", "20260915_120000.mp4")
        with freeze_time(self.NOW):
            result = health_watch.check_recording_stalled()
        assert result is not None and "駐車場(parking)" in result and "ありません" in result

    def test_previous_day_file_counts_just_after_midnight(self, tmp_path, monkeypatch):
        from freezegun import freeze_time
        self._setup(tmp_path, monkeypatch, [{"name": "玄関", "nas_folder": "entrance"}])
        self._touch(tmp_path, "entrance", "20260919_235500.mp4")
        with freeze_time("2026-09-20 00:05:00+09:00"):
            assert health_watch.check_recording_stalled() is None

    def test_falls_back_to_name_and_skips_disabled_camera(self, tmp_path, monkeypatch):
        from freezegun import freeze_time
        self._setup(tmp_path, monkeypatch, [
            {"name": "entrance"},
            {"name": "retired", "enabled": False},
        ])
        self._touch(tmp_path, "entrance", "20260919_205104.mp4")
        with freeze_time(self.NOW):
            assert health_watch.check_recording_stalled() is None

    def test_skipped_when_nas_not_mounted(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, [{"name": "玄関", "nas_folder": "entrance"}])
        monkeypatch.setattr(health_watch.os.path, "ismount", lambda path: False)
        assert health_watch.check_recording_stalled() is None

    def test_registered_as_a_health_check(self, monkeypatch):
        """run_checks のチェック一覧に 'recording' として組み込まれていること。"""
        for name in (
            "check_service_active", "check_disk_usage", "check_memory_usage",
            "check_nas_mount", "check_deploy_config_drift", "check_quest_master_drift",
            "check_api_responsive",
        ):
            monkeypatch.setattr(health_watch, name, lambda: None)
        monkeypatch.setattr(health_watch, "check_journal_errors", lambda since: None)
        monkeypatch.setattr(health_watch, "check_app_logs", lambda since: None)
        monkeypatch.setattr(health_watch, "check_recording_stalled", lambda: "常時録画が止まっている可能性があります")
        monkeypatch.setattr(
            health_watch, "_read_marker",
            lambda: datetime.datetime.fromisoformat("2026-09-19T09:00:00"),
        )
        monkeypatch.setattr(health_watch, "_write_marker", lambda dt: None)
        keys_seen = []
        monkeypatch.setattr(health_watch, "_should_notify", lambda keys, now: keys_seen.extend(keys) or True)
        monkeypatch.setattr(health_watch, "_fire_investigate_hook", lambda anomalies, now: None)
        sent = []
        monkeypatch.setattr(
            health_watch, "send_push",
            lambda messages, target=None, channel=None: sent.append(messages) or True,
        )

        health_watch.run_checks()

        assert keys_seen == ["recording"]
        assert "常時録画が止まっている" in sent[0][0]["text"]
