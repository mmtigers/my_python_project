# MY_HOME_SYSTEM/tests/test_scheduler_concurrency.py
"""
scheduler_boot.py の並列実行の回帰テスト (CODE_REVIEW_REPORT.md 4.1)。

修正前は TASKS を for ループで直列に subprocess.run(..., 完了待ち) していたため、
1タスクの遅延(例: 長時間かかるNAS監視)が server_watchdog 等の重要な監視タスクの
実行まで丸ごとブロックしていた。修正後は ThreadPoolExecutor 経由で各タスクを
並列実行する設計になっている。

main() 自体は無限ループ(while True + time.sleep(10))のため直接呼び出してテストは
できない。そのため、main() が内部で使っているのと同じ実行単位である run_script() を、
main() と同じ ThreadPoolExecutor 経由で2つ同時にsubmitし、
threading.Barrier で「両方が同時に実行中の状態に到達できるか」を検証する。
もし将来 run_script が何らかのグローバルロックで直列化されてしまった場合、
2つ目のタスクが Barrier に到達できずタイムアウトし、このテストが失敗する。
"""
import io
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, BrokenExecutor
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import scheduler_boot


class _FakePopen:
    """#360: run_script は subprocess.run ではなく Popen(+wait)で子プロセスを
    起動し PID を保持するようになったため、テストのフェイクも Popen 互換にする。
    #411 S-L5: communicate()による一括読み取りから、proc.wait()+stderrの
    別スレッド逐次読み取りに変更されたため、stderrはイテレート可能な
    ファイルオブジェクト(io.StringIO)として持たせる。"""

    def __init__(self, returncode=0, on_wait=None, stderr_text=""):
        self.returncode = returncode
        self._on_wait = on_wait
        self.killed = False
        self.stderr = io.StringIO(stderr_text)

    def wait(self, timeout=None):
        if self._on_wait:
            self._on_wait()
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True


def test_two_tasks_execute_concurrently_not_serially(monkeypatch):
    barrier = threading.Barrier(parties=2, timeout=5)
    real_exists = os.path.exists

    def _fake_exists(path):
        if "fake_task_a.py" in str(path) or "fake_task_b.py" in str(path):
            return True
        return real_exists(path)

    def _fake_popen(cmd, **kwargs):
        # 両方のタスクが wait() に同時に到達しない限りタイムアウトで例外になる。
        # = 直列実行に戻ってしまった場合はこのテストがタイムアウトで失敗する。
        return _FakePopen(returncode=0, on_wait=barrier.wait)

    monkeypatch.setattr(scheduler_boot.os.path, "exists", _fake_exists)
    monkeypatch.setattr(scheduler_boot.subprocess, "Popen", _fake_popen)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-scheduler") as executor:
        future_a = executor.submit(scheduler_boot.run_script, "monitors/fake_task_a.py", [])
        future_b = executor.submit(scheduler_boot.run_script, "monitors/fake_task_b.py", [])

        try:
            result_a = future_a.result(timeout=6)
            result_b = future_b.result(timeout=6)
        except (threading.BrokenBarrierError, BrokenExecutor):
            pytest.fail("2つのタスクが同時に実行されなかった(直列実行に回帰している可能性)")

    assert result_a is True
    assert result_b is True


def test_missing_script_returns_false_without_calling_subprocess(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler_boot.subprocess, "Popen", lambda *a, **kw: calls.append(1))

    result = scheduler_boot.run_script("monitors/definitely_does_not_exist_12345.py", [])

    assert result is False
    assert calls == []


def test_subprocess_timeout_is_treated_as_failure_not_crash(monkeypatch):
    import subprocess as subprocess_module

    real_exists = os.path.exists
    monkeypatch.setattr(
        scheduler_boot.os.path, "exists",
        lambda path: True if "fake_timeout_task.py" in str(path) else real_exists(path),
    )

    class _TimeoutPopen(_FakePopen):
        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess_module.TimeoutExpired(cmd="fake", timeout=timeout)
            return self.returncode

    monkeypatch.setattr(scheduler_boot.subprocess, "Popen", lambda *a, **kw: _TimeoutPopen())

    result = scheduler_boot.run_script("monitors/fake_timeout_task.py", [])
    assert result is False



def test_sigterm_terminates_running_children(monkeypatch):
    """#360: SIGTERM 受信時に実行中の子プロセスが terminate されること。"""
    import threading

    started = threading.Event()
    release = threading.Event()

    class _BlockingPopen(_FakePopen):
        def __init__(self):
            super().__init__(returncode=None)
            self.terminated = False

        def wait(self, timeout=None):
            started.set()
            release.wait(timeout=5)
            self.returncode = 0
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            release.set()

    proc = _BlockingPopen()
    real_exists = os.path.exists
    monkeypatch.setattr(
        scheduler_boot.os.path, "exists",
        lambda path: True if "fake_long_task.py" in str(path) else real_exists(path),
    )
    monkeypatch.setattr(scheduler_boot.subprocess, "Popen", lambda *a, **kw: proc)

    t = threading.Thread(target=scheduler_boot.run_script, args=("monitors/fake_long_task.py", []))
    t.start()
    assert started.wait(timeout=5)

    stopped = scheduler_boot.terminate_running_children()
    t.join(timeout=5)

    assert stopped == 1
    assert proc.terminated is True
    assert "monitors/fake_long_task.py" not in scheduler_boot._running_children


def test_intentional_terminate_logs_info_not_error(monkeypatch):
    """#575回帰防止: terminate_running_children による意図的な停止は、
    run_scriptがreturncode<0(SIGTERM由来の-15等)を「タスク失敗」としてERROR
    (→Discord通知)にせず、INFOでログを残すこと。"""
    started = threading.Event()
    release = threading.Event()

    class _BlockingPopen(_FakePopen):
        def __init__(self):
            super().__init__(returncode=None)
            self.terminated = False

        def wait(self, timeout=None):
            started.set()
            release.wait(timeout=5)
            self.returncode = -15
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            release.set()

    proc = _BlockingPopen()
    real_exists = os.path.exists
    monkeypatch.setattr(
        scheduler_boot.os.path, "exists",
        lambda path: True if "fake_long_task.py" in str(path) else real_exists(path),
    )
    monkeypatch.setattr(scheduler_boot.subprocess, "Popen", lambda *a, **kw: proc)

    mock_logger = MagicMock()
    monkeypatch.setattr(scheduler_boot, "logger", mock_logger)

    result_holder = {}

    def _run():
        result_holder["result"] = scheduler_boot.run_script("monitors/fake_long_task.py", [])

    t = threading.Thread(target=_run)
    t.start()
    assert started.wait(timeout=5)

    scheduler_boot.terminate_running_children()
    t.join(timeout=5)

    assert result_holder["result"] is False
    mock_logger.error.assert_not_called()
    assert any("terminated intentionally" in call.args[0] for call in mock_logger.info.call_args_list)


class TestTaskDueDecisionUsesMonotonicClock:
    """Issue #749 (AUDIT-020): 実行判定が壁時計の巻き戻りに影響されないこと。

    以前はループが time.time() 基準で、Raspberry Pi(RTC なし)の NTP 同期で
    時刻が後方へジャンプすると now - last_run が負になり、interval(300〜3600秒)を
    超えるまで6つの監視タスクがすべて沈黙していた。しかも health_watch は
    「タスクが実行されていない」ことを見ないため沈黙自体が検知されない。
    """

    def _task(self, interval=300, last_run=0):
        return {"script": "monitors/dummy.py", "interval": interval, "last_run": last_run, "args": []}

    def test_main_loop_reads_the_monotonic_clock(self):
        """ループ本体が time.monotonic() を読むこと(time.time() へ戻さない)。"""
        import inspect

        source = inspect.getsource(scheduler_boot.main)
        # コメント本文にも "time.time()" という語が出るため、代入文そのものを見る
        assert "now: float = time.monotonic()" in source
        assert "now: float = time.time()" not in source

    def test_never_run_task_runs_immediately(self):
        """last_run の初期値 0 は「未実行」の番兵。monotonic 基準では
        「起動時刻より前の値」にすぎないため、明示的に扱わないと起動直後
        (uptime < interval)の初回実行が interval 秒遅れる。"""
        assert scheduler_boot._is_task_due(self._task(), now=10.0) is True

    def test_task_is_not_due_before_the_interval_elapses(self):
        assert scheduler_boot._is_task_due(self._task(interval=300, last_run=1000.0), now=1100.0) is False

    def test_task_is_due_once_the_interval_elapses(self):
        assert scheduler_boot._is_task_due(self._task(interval=300, last_run=1000.0), now=1300.0) is True

    def test_monotonic_clock_is_unaffected_by_a_wall_clock_rollback(self, monkeypatch):
        """壁時計が1日巻き戻っても、monotonic は進み続けるため判定は変わらない。"""
        real_monotonic_values = iter([1000.0, 1300.0])
        monkeypatch.setattr(
            scheduler_boot.time, "time", lambda: 0.0  # 壁時計が1970年へ巻き戻った状況
        )
        monkeypatch.setattr(
            scheduler_boot.time, "monotonic", lambda: next(real_monotonic_values)
        )
        task = self._task(interval=300, last_run=scheduler_boot.time.monotonic())
        assert scheduler_boot._is_task_due(task, now=scheduler_boot.time.monotonic()) is True
