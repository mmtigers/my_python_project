# MY_HOME_SYSTEM/tests/test_camera_monitor_cleanup.py
"""
Issue #1161: 終了時(`cleanup_handler`)の購読解除が、親の kill 猶予に収まることを固定する。

親(`unified_server.py` の lifespan 終了処理)は `camera_process.terminate()` の後
`wait(timeout=5)` で待ち、超えたら `kill()` する。以前の `cleanup_handler` は
`active_pullpoints` を**順番に** `Unsubscribe()` しており(1台あたり最大
`ONVIF_OPERATION_TIMEOUT_SEC`=10秒)、1台でも応答しないと5秒で SIGKILL され、
残りのカメラの購読がカメラ側に残っていた。例外も `pass` で捨てていたため、
解除できなかったことがログに残らなかった。

`os._exit` は実際には呼ばず、例外で置き換えて「終了しようとした」ことを観測する。
"""
import logging
import os
import sys
import threading
import time

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor

# 親(unified_server.py)が SIGTERM 後に待つ秒数
PARENT_KILL_GRACE_SEC = 5


class _Exited(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _raise_exit(code):
    raise _Exited(code)


class _BlockingPullPoint:
    """Unsubscribe が応答しない(タイムアウトまで戻らない)カメラ。"""

    def __init__(self, block_sec):
        self.block_sec = block_sec
        self.released = threading.Event()

    def Unsubscribe(self):
        # テスト終了後にスレッドが残り続けないよう、release で早く抜けられるようにする
        self.released.wait(self.block_sec)
        raise TimeoutError("Read timed out")


class _OkPullPoint:
    def __init__(self):
        self.calls = 0

    def Unsubscribe(self):
        self.calls += 1


class _FailingPullPoint:
    def Unsubscribe(self):
        raise RuntimeError("SOAP Fault: Action Failed")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(camera_monitor.os, "_exit", _raise_exit)
    camera_monitor.active_pullpoints.clear()
    camera_monitor._pullpoint_names.clear()
    yield
    camera_monitor.active_pullpoints.clear()
    camera_monitor._pullpoint_names.clear()


def _run_cleanup():
    with pytest.raises(_Exited) as exc:
        camera_monitor.cleanup_handler(15, None)
    assert exc.value.code == 0


def test_three_blocking_cameras_finish_within_parent_grace():
    """10秒ブロックする Unsubscribe が3台あっても、親の猶予(5秒)以内に終了へ進む。"""
    pps = [_BlockingPullPoint(10) for _ in range(3)]
    for i, pp in enumerate(pps):
        camera_monitor._add_pullpoint(pp, f"cam{i}")
    try:
        start = time.monotonic()
        _run_cleanup()
        elapsed = time.monotonic() - start
    finally:
        for pp in pps:
            pp.released.set()
    assert elapsed < PARENT_KILL_GRACE_SEC
    assert camera_monitor.CLEANUP_UNSUBSCRIBE_BUDGET_SEC < PARENT_KILL_GRACE_SEC


def test_fast_cameras_are_all_unsubscribed_even_if_one_blocks():
    """1台が応答しなくても、ほかのカメラの解除は待たされずに実行される(順番待ちにならない)。"""
    blocking = _BlockingPullPoint(10)
    ok1, ok2 = _OkPullPoint(), _OkPullPoint()
    camera_monitor._add_pullpoint(blocking, "駐車場")
    camera_monitor._add_pullpoint(ok1, "玄関")
    camera_monitor._add_pullpoint(ok2, "庭")
    try:
        _run_cleanup()
    finally:
        blocking.released.set()
    assert ok1.calls == 1 and ok2.calls == 1


def test_each_result_is_logged_with_the_camera_name(caplog):
    blocking = _BlockingPullPoint(10)
    camera_monitor._add_pullpoint(_OkPullPoint(), "玄関")
    camera_monitor._add_pullpoint(_FailingPullPoint(), "庭")
    camera_monitor._add_pullpoint(blocking, "駐車場")
    try:
        with caplog.at_level(logging.INFO, logger=camera_monitor.logger.name):
            _run_cleanup()
    finally:
        blocking.released.set()

    records = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert any(lv == logging.INFO and "[玄関]" in m and "Unsubscribed" in m for lv, m in records)
    assert any(lv == logging.WARNING and "[庭]" in m and "Action Failed" in m for lv, m in records)
    assert any(lv == logging.WARNING and "[駐車場]" in m and "did not finish" in m for lv, m in records)


def test_service_wrapper_form_is_still_unsubscribed():
    """`Unsubscribe` を直接持たず `.service.Unsubscribe` を持つ形(従来の分岐)も解除する。"""
    calls = []

    class _Svc:
        def Unsubscribe(self, _soapheaders=None):
            calls.append(_soapheaders)

    class _Wrapper:
        service = _Svc()

    camera_monitor._add_pullpoint(_Wrapper(), "玄関")
    _run_cleanup()
    assert calls == [None]


def test_no_subscriptions_exits_immediately():
    start = time.monotonic()
    _run_cleanup()
    assert time.monotonic() - start < 1


def test_discard_forgets_the_camera_name():
    pp = _OkPullPoint()
    camera_monitor._add_pullpoint(pp, "玄関")
    camera_monitor._discard_pullpoint(pp)
    camera_monitor._discard_pullpoint(pp)  # 二重に呼んでも例外にならない
    assert camera_monitor.active_pullpoints == []
    assert camera_monitor._pullpoint_names == {}
