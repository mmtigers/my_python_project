# MY_HOME_SYSTEM/tests/test_server_watchdog_is_process_alive.py
"""
monitors/server_watchdog.py の is_process_alive の回帰テスト (#411 S-L11)。

以前は `pgrep -f process_keyword` の単純な部分文字列マッチだったため、
`cat unified_server.py` のような無関係なコマンドの引数にキーワードが含まれる
だけでもヒットしてしまい、本来のサーバープロセスが落ちていても誤って
「生きている」と判定しうる誤検知の余地があった。
"""
import os
import re
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import server_watchdog


def _matches(cmdline: str) -> bool:
    """is_process_alive内部で組み立てる正規表現が、実際のpgrep(POSIX拡張正規表現互換の
    re.search)相当でcmdlineにマッチするかどうかを直接検証する(pgrep自体は起動しない)。"""
    pattern = rf"python[0-9.]*\s+\S*{re.escape('unified_server.py')}(\s|$)"
    return re.search(pattern, cmdline) is not None


def test_matches_real_server_invocation():
    assert _matches("/usr/bin/python3 /home/masahiro/develop/MY_HOME_SYSTEM/unified_server.py")
    assert _matches("python unified_server.py")


def test_does_not_match_unrelated_commands_referencing_the_filename():
    # 無関係なコマンドの引数にファイル名が含まれるだけではヒットしない
    assert not _matches("cat unified_server.py")
    assert not _matches("vim unified_server.py")
    assert not _matches("cp unified_server.py unified_server.py.bak")
    assert not _matches("git diff unified_server.py")


def test_is_process_alive_returns_false_when_pgrep_finds_nothing(monkeypatch):
    import subprocess

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(server_watchdog.subprocess, "run", _fake_run)
    assert server_watchdog.is_process_alive("unified_server.py") is False


def test_is_process_alive_passes_a_regex_pattern_to_pgrep(monkeypatch):
    import subprocess

    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(server_watchdog.subprocess, "run", _fake_run)
    assert server_watchdog.is_process_alive("unified_server.py") is True
    assert captured["cmd"][:2] == ["pgrep", "-f"]
    assert "unified_server" in captured["cmd"][2]
    assert captured["cmd"][2] != "unified_server.py"


# ---------------------------------------------------------------------------
# Issue #758 (AUDIT-029): check_health / check_throttling_status の状態遷移。
#
# 「サーバーが落ちたら Discord に通知する」という機能そのもの(check_health 本体)が
# 未到達で、通知の抑制ロジック(ロックファイルの mtime 比較)にバグがあっても
# 気づけない状態だった。get_service_status / is_process_alive / send_push を
# monkeypatch し、純粋な状態遷移として検証する(systemctl/pgrep は起動しない)。
# ---------------------------------------------------------------------------
import subprocess as _subprocess
import time

import pytest


@pytest.fixture
def watchdog_env(tmp_path, monkeypatch):
    """LOCK_FILE / 状態ファイルを tmp_path へ逃がし、send_push を記録に差し替える。"""
    lock_file = tmp_path / "watchdog_alert_sent.lock"
    monkeypatch.setattr(server_watchdog, "LOCK_FILE", lock_file)
    monkeypatch.setattr(
        server_watchdog, "THROTTLE_STATE_FILE", tmp_path / "watchdog_throttle_history.state"
    )
    sent = []
    monkeypatch.setattr(
        server_watchdog, "send_push",
        lambda messages, target=None, channel=None: sent.append(
            (messages[0]["text"], target, channel)
        ),
    )
    return lock_file, sent


def _set_health(monkeypatch, *, status: str, alive: bool) -> None:
    monkeypatch.setattr(server_watchdog, "get_service_status", lambda name: status)
    monkeypatch.setattr(server_watchdog, "is_process_alive", lambda name: alive)


class TestCheckHealthStateTransitions:
    """停止検知 → 通知 → ロック作成 → リマインダー → 復旧 → 復旧通知 → ロック削除。"""

    def test_healthy_without_lock_sends_nothing(self, watchdog_env, monkeypatch):
        lock_file, sent = watchdog_env
        _set_health(monkeypatch, status="active", alive=True)

        server_watchdog.check_health()

        assert sent == []
        assert not lock_file.exists()

    def test_activating_is_treated_as_healthy(self, watchdog_env, monkeypatch):
        """起動途中(activating)を「落ちている」と誤報しないこと。"""
        lock_file, sent = watchdog_env
        _set_health(monkeypatch, status="activating", alive=True)

        server_watchdog.check_health()

        assert sent == []
        assert not lock_file.exists()

    def test_service_active_but_process_dead_is_unhealthy(self, watchdog_env, monkeypatch):
        """systemd 上は active でも実プロセスが居なければ異常として通知する。"""
        lock_file, sent = watchdog_env
        _set_health(monkeypatch, status="active", alive=False)

        server_watchdog.check_health()

        assert len(sent) == 1
        assert sent[0] == (server_watchdog.MSG_STOPPED, "discord", "error")
        assert lock_file.exists()

    def test_stop_alert_is_sent_once_and_creates_the_lock(self, watchdog_env, monkeypatch):
        lock_file, sent = watchdog_env
        _set_health(monkeypatch, status="inactive", alive=False)

        server_watchdog.check_health()
        # 2回目はロックがあり、リマインダー間隔にも達していないので通知しない
        server_watchdog.check_health()

        assert [s[0] for s in sent] == [server_watchdog.MSG_STOPPED]
        assert lock_file.exists()

    def test_reminder_is_sent_after_the_reminder_interval(self, watchdog_env, monkeypatch):
        """ロックの mtime が REMINDER_INTERVAL_SEC より古ければリマインダーを送り、
        ロックを touch し直して次のリマインダーまでの起点を更新する。"""
        lock_file, sent = watchdog_env
        _set_health(monkeypatch, status="failed", alive=False)

        lock_file.touch()
        old_mtime = time.time() - server_watchdog.REMINDER_INTERVAL_SEC - 60
        os.utime(lock_file, (old_mtime, old_mtime))

        server_watchdog.check_health()

        assert [s[0] for s in sent] == [server_watchdog.MSG_REMINDER]
        assert lock_file.stat().st_mtime > old_mtime

    def test_recovery_notification_is_sent_and_lock_is_removed(self, watchdog_env, monkeypatch):
        lock_file, sent = watchdog_env
        lock_file.touch()
        _set_health(monkeypatch, status="active", alive=True)

        server_watchdog.check_health()

        assert [s[0] for s in sent] == [server_watchdog.MSG_RECOVERED]
        assert sent[0][2] == "notify", "復旧通知は error ではなく notify チャンネル"
        assert not lock_file.exists()

    def test_full_cycle_stop_reminder_recover(self, watchdog_env, monkeypatch):
        """停止 → リマインダー → 復旧の一連の遷移で、通知が期待どおりの順に出ること。"""
        lock_file, sent = watchdog_env

        _set_health(monkeypatch, status="inactive", alive=False)
        server_watchdog.check_health()

        old_mtime = time.time() - server_watchdog.REMINDER_INTERVAL_SEC - 1
        os.utime(lock_file, (old_mtime, old_mtime))
        server_watchdog.check_health()

        _set_health(monkeypatch, status="active", alive=True)
        server_watchdog.check_health()

        assert [s[0] for s in sent] == [
            server_watchdog.MSG_STOPPED,
            server_watchdog.MSG_REMINDER,
            server_watchdog.MSG_RECOVERED,
        ]
        assert not lock_file.exists()

    def test_unexpected_exception_is_swallowed(self, watchdog_env, monkeypatch):
        """cron から10分毎に回る前提なので、内部例外でプロセスを落とさない。"""
        _lock_file, sent = watchdog_env

        def _boom(name):
            raise RuntimeError("systemctl exploded")

        monkeypatch.setattr(server_watchdog, "get_service_status", _boom)

        server_watchdog.check_health()  # 例外が漏れないこと

        assert sent == []


class TestGetServiceStatus:
    def test_returns_stripped_stdout(self, monkeypatch):
        monkeypatch.setattr(
            server_watchdog.subprocess, "run",
            lambda cmd, **kw: _subprocess.CompletedProcess(cmd, 0, stdout="active\n", stderr=""),
        )
        assert server_watchdog.get_service_status("home_system.service") == "active"

    def test_returns_error_when_systemctl_raises(self, monkeypatch):
        def _boom(cmd, **kwargs):
            raise _subprocess.TimeoutExpired(cmd=cmd, timeout=30)

        monkeypatch.setattr(server_watchdog.subprocess, "run", _boom)
        assert server_watchdog.get_service_status("home_system.service") == "error"


class TestThrottlingHistoryState:
    """履歴ビット(再起動までスティッキー)をブートごとに1回だけ通知する compare-and-set。"""

    @pytest.fixture(autouse=True)
    def _state_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            server_watchdog, "THROTTLE_STATE_FILE", tmp_path / "throttle.state"
        )
        monkeypatch.setattr(server_watchdog, "_get_boot_id", lambda: "boot-aaa")

    def test_new_bits_are_reported_only_once_per_boot(self):
        assert server_watchdog._is_new_history(0x10000) is True
        assert server_watchdog._is_new_history(0x10000) is False

    def test_additional_bits_are_reported_again(self):
        assert server_watchdog._is_new_history(0x10000) is True
        assert server_watchdog._is_new_history(0x30000) is True
        assert server_watchdog._is_new_history(0x30000) is False

    def test_reboot_resets_the_notified_bits(self, monkeypatch):
        assert server_watchdog._is_new_history(0x10000) is True
        monkeypatch.setattr(server_watchdog, "_get_boot_id", lambda: "boot-bbb")
        assert server_watchdog._is_new_history(0x10000) is True

    def test_corrupt_state_file_is_treated_as_unnotified(self):
        server_watchdog.THROTTLE_STATE_FILE.write_text("garbage", encoding="utf-8")
        assert server_watchdog._is_new_history(0x10000) is True


class TestCheckThrottlingStatus:
    @pytest.fixture(autouse=True)
    def _state_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server_watchdog, "THROTTLE_STATE_FILE", tmp_path / "throttle.state")
        monkeypatch.setattr(server_watchdog, "_get_boot_id", lambda: "boot-ccc")
        # vcgencmd が失敗したときの代替経路(ブート毎1回の通知の記録・hwmon の読み取り)も
        # 一時ディレクトリへ向ける。向けないと、テストのたびに実機の MY_HOME_SYSTEM/ 直下へ
        # 状態ファイルを書き、本物の /sys/class/hwmon を読んでしまう。
        monkeypatch.setattr(server_watchdog, "VCGENCMD_UNAVAILABLE_STATE_FILE", tmp_path / "vcgencmd.state")
        empty_hwmon = tmp_path / "hwmon"
        empty_hwmon.mkdir()
        monkeypatch.setattr(server_watchdog, "HWMON_DIR", empty_hwmon)

    def _run_with_vcgencmd(self, monkeypatch, *, stdout: str, returncode: int = 0):
        monkeypatch.setattr(
            server_watchdog.subprocess, "run",
            lambda cmd, **kw: _subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=""),
        )
        records = []
        for level in ("error", "warning", "debug"):
            monkeypatch.setattr(
                server_watchdog.logger, level,
                lambda msg, *a, _lv=level: records.append((_lv, msg % a if a else msg)),
            )
        server_watchdog.check_throttling_status()
        return records

    def test_normal_value_logs_nothing(self, monkeypatch):
        records = self._run_with_vcgencmd(monkeypatch, stdout="throttled=0x0\n")
        assert [r for r in records if r[0] in ("error", "warning")] == []

    def test_active_throttling_is_logged_as_error(self, monkeypatch):
        records = self._run_with_vcgencmd(monkeypatch, stdout="throttled=0x5\n")
        assert any(r[0] == "error" for r in records)

    def test_history_only_is_warned_once_per_boot(self, monkeypatch):
        first = self._run_with_vcgencmd(monkeypatch, stdout="throttled=0x50000\n")
        second = self._run_with_vcgencmd(monkeypatch, stdout="throttled=0x50000\n")
        assert any(r[0] == "warning" for r in first)
        assert not any(r[0] == "warning" for r in second)

    def test_non_zero_exit_status_is_reported_instead_of_silently_skipped(self, monkeypatch):
        """vcgencmd が失敗したら、監視が止まっていることを ERROR で知らせる。

        以前はこのテストが「error も warning も出さないこと」を固定していた。つまり
        **監視が黙って止まること自体**を検査していた。2026-09 の OS 更新で userland だけが
        新しくなり vcgencmd が失敗し続けた際、スロットリング・電圧低下の監視が誰にも
        知られず止まっていた。ブート毎1回の通知の詳細は test_server_watchdog_hardware.py。
        """
        records = self._run_with_vcgencmd(monkeypatch, stdout="", returncode=1)
        errors = [r for r in records if r[0] == "error"]
        assert len(errors) == 1

    def test_missing_vcgencmd_does_not_crash(self, monkeypatch):
        def _missing(cmd, **kwargs):
            raise FileNotFoundError("vcgencmd")

        monkeypatch.setattr(server_watchdog.subprocess, "run", _missing)
        server_watchdog.check_throttling_status()

    def test_unexpected_exception_is_downgraded_to_warning(self, monkeypatch):
        def _boom(cmd, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(server_watchdog.subprocess, "run", _boom)
        server_watchdog.check_throttling_status()
