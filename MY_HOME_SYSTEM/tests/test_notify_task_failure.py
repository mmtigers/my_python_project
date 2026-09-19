# MY_HOME_SYSTEM/tests/test_notify_task_failure.py
"""tools/notify_task_failure.py (Issue #751 / AUDIT-022) のテスト。

run_task.sh は失敗を exit code とログファイルに書くだけで通知していなかった。
crontab に MAILTO= も無く、全エントリの出力はログファイルへリダイレクトされている
ため、自前で通知しないタスクの失敗は完全に無音だった。とくに「Python が起動する前に
失敗する場合」(ImportError・.venv の破損)は logger.error 経由の Discord 通知にも
乗らないため、Issue #736 の依存欠落と組み合わさると「数か月気づかない」が現実的な
シナリオになる。

通知の実体は logger.error(= core.logger.DiscordErrorHandler)だが、cron は毎回
別プロセスなので DiscordErrorHandler 側の重複排除(Issue #759)は効かない。
そのためタスクごとのクールダウンを状態ファイルで持つ。
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from tools import notify_task_failure


@pytest.fixture(autouse=True)
def _isolated_log_dir(tmp_path, monkeypatch):
    """状態ファイルの置き場をテストごとの一時ディレクトリへ逃がす。"""
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture()
def fake_logger(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(notify_task_failure, "logger", logger)
    return logger


class TestCooldown:
    def test_first_failure_is_notified(self, fake_logger):
        assert notify_task_failure.notify("monitors/log_analyzer.py", "1", "", now=1000.0) is True
        assert fake_logger.error.called

    def test_repeated_failure_within_the_window_is_suppressed(self, fake_logger):
        """keep_alive_anker.sh(5分毎)が失敗し続けても洪水にならないこと。"""
        assert notify_task_failure.notify("tools/keep_alive_anker.sh", "1", "", now=1000.0) is True
        fake_logger.reset_mock()

        # 5分後(= cron の次回起動)
        assert notify_task_failure.notify("tools/keep_alive_anker.sh", "1", "", now=1300.0) is False
        assert not fake_logger.error.called

    def test_failure_after_the_window_is_notified_again(self, fake_logger):
        notify_task_failure.notify("tools/keep_alive_anker.sh", "1", "", now=1000.0)
        fake_logger.reset_mock()

        later = 1000.0 + notify_task_failure.COOLDOWN_SEC + 1
        assert notify_task_failure.notify("tools/keep_alive_anker.sh", "1", "", now=later) is True
        assert fake_logger.error.called

    def test_cooldown_is_per_task(self, fake_logger):
        """あるタスクの抑制が別タスクの通知を巻き添えにしないこと。"""
        notify_task_failure.notify("monitors/log_analyzer.py", "1", "", now=1000.0)
        fake_logger.reset_mock()

        assert notify_task_failure.notify("weekly_analyze_report.py", "1", "", now=1001.0) is True
        assert fake_logger.error.called

    def test_unreadable_state_falls_back_to_notifying(self, fake_logger, _isolated_log_dir):
        """状態ファイルが壊れていたら「通知する側」に倒すこと。

        抑制の誤りで無音になるより、多めに通知するほうが安全
        (memory_monitor.check_cooldown と同じ方針)。
        """
        path = notify_task_failure._state_path("monitors/log_analyzer.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not-a-number")
        assert notify_task_failure.should_notify("monitors/log_analyzer.py", now=1000.0) is True


class TestMessageShape:
    def test_variable_log_tail_goes_into_the_format_args(self, fake_logger, tmp_path):
        """変動するログ末尾は %s の引数側に置くこと(Issue #759 の重複排除キー対策)。

        DiscordErrorHandler の重複排除キーはフォーマット**前**の record.msg で決まる。
        ログ末尾を f-string へ埋め込むと毎回別のキーになり、束ねられなくなる。
        """
        log = tmp_path / "task.log"
        log.write_text("line1\nline2\nboom\n", encoding="utf-8")

        notify_task_failure.notify("monitors/log_analyzer.py", "1", str(log), now=1000.0)

        msg = fake_logger.error.call_args[0][0]
        args = fake_logger.error.call_args[0][1:]
        assert "monitors/log_analyzer.py" in msg
        assert "exit=1" in msg
        assert msg.endswith("%s"), "ログ末尾は引数側に置くこと"
        assert "boom" in args[0]
        assert "boom" not in msg

    def test_missing_log_file_still_notifies(self, fake_logger):
        assert notify_task_failure.notify("monitors/log_analyzer.py", "127", "/no/such.log", now=1000.0) is True
        assert "(ログなし)" in fake_logger.error.call_args[0][1]

    def test_log_tail_is_truncated(self, fake_logger, tmp_path):
        log = tmp_path / "task.log"
        log.write_text("\n".join("x" * 200 for _ in range(200)), encoding="utf-8")

        notify_task_failure.notify("monitors/log_analyzer.py", "1", str(log), now=1000.0)
        assert len(fake_logger.error.call_args[0][1]) <= notify_task_failure.LOG_TAIL_LIMIT


class TestCli:
    def test_missing_arguments_returns_usage_error(self):
        assert notify_task_failure.main([]) == 2

    def test_never_changes_the_task_exit_code(self, fake_logger):
        """通知の成否で cron タスク自体の終了コードを変えないこと。"""
        assert notify_task_failure.main(["monitors/log_analyzer.py", "1"]) == 0
        # 2回目はクールダウンで抑制されるが、戻り値は変わらない
        assert notify_task_failure.main(["monitors/log_analyzer.py", "1"]) == 0


class TestRunTaskShIntegration:
    RUN_TASK_SH = os.path.join(os.path.dirname(__file__), "..", "run_task.sh")

    def _read(self) -> str:
        with open(self.RUN_TASK_SH, "r", encoding="utf-8") as f:
            return f.read()

    def test_failure_branch_invokes_the_notifier(self):
        script = self._read()
        assert "tools/notify_task_failure.py" in script
        failure_block = script[script.index("ERROR: Exit Code"):script.index("Success ---")]
        assert "notify_task_failure.py" in failure_block, "失敗分岐の中で呼ぶこと"

    def test_notification_failure_does_not_change_the_exit_code(self):
        """venv が壊れていれば通知も失敗する。そのとき本体の exit code を壊さないこと。"""
        script = self._read()
        assert "|| true" in script
        assert script.rstrip().endswith("exit ${EXIT_CODE}")
