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
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, BrokenExecutor

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import scheduler_boot


class _FakeCompletedProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def test_two_tasks_execute_concurrently_not_serially(monkeypatch):
    barrier = threading.Barrier(parties=2, timeout=5)
    real_exists = os.path.exists

    def _fake_exists(path):
        if "fake_task_a.py" in str(path) or "fake_task_b.py" in str(path):
            return True
        return real_exists(path)

    def _fake_subprocess_run(cmd, **kwargs):
        # 両方のタスクがここに同時に到達しない限りタイムアウトで例外になる。
        # = 直列実行に戻ってしまった場合はこのテストがタイムアウトで失敗する。
        barrier.wait()
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(scheduler_boot.os.path, "exists", _fake_exists)
    monkeypatch.setattr(scheduler_boot.subprocess, "run", _fake_subprocess_run)

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
    monkeypatch.setattr(scheduler_boot.subprocess, "run", lambda *a, **kw: calls.append(1))

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

    def _raise_timeout(cmd, **kwargs):
        raise subprocess_module.TimeoutExpired(cmd="fake", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(scheduler_boot.subprocess, "run", _raise_timeout)

    result = scheduler_boot.run_script("monitors/fake_timeout_task.py", [])
    assert result is False
