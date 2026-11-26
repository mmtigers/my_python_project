# MY_HOME_SYSTEM/tests/test_camera_monitor_failure_reporting.py
"""
camera_monitor の障害時の分類・通知・解放順序を固定する。

- #1146: 障害からの復旧を INFO に出し、障害通知を送っていたときだけ復旧も通知する
- #1147: 例外メッセージ別のヒント(内容とレベル)
- #1151: 緊急診断は設定上の ONVIF ポートと RTSP(554)だけを調べる
- #1152: requests の ConnectionError は一時的障害。ただし続けば通知は出る
- #1162: 障害時は待機(time.sleep)より前に Unsubscribe・セッション close を済ませる
- #1144: SOAP Fault の subcodes をログに出す
"""
import os
import sys
from http.client import RemoteDisconnected

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor


class _StopLoop(Exception):
    """monitor_single_camera の無限ループを打ち切る番兵。"""


CAM_CONF = {
    "id": "cam1",
    "name": "テストカメラ",
    "ip": "192.0.2.10",
    "port": 80,
    "user": "u",
    "pass": "p",
    "enabled": True,
}


class _Clock:
    """time.sleep / time.monotonic の代役。sleep した分だけ時計が進む。"""

    def __init__(self, stop_after, events):
        self.now = 1000.0
        self.calls = []
        self._stop_after = stop_after
        self._events = events

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self._events.append(("sleep", seconds))
        self.calls.append(seconds)
        self.now += seconds
        if len(self.calls) >= self._stop_after:
            raise _StopLoop()


@pytest.fixture
def env(monkeypatch):
    """通知・診断・ログを記録用に差し替える。"""
    events = []
    state = {"events": events, "pushes": [], "info": [], "warning": [], "error": [], "diagnosed": []}
    monkeypatch.setattr(camera_monitor, "is_camera_enabled", lambda cam_conf: True)
    monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: True)
    monkeypatch.setattr(camera_monitor, "find_wsdl_path", lambda: "unused-wsdl-dir")
    monkeypatch.setattr(
        camera_monitor, "perform_emergency_diagnosis",
        lambda ip, ports=None: state["diagnosed"].append((ip, ports)) or {},
    )
    monkeypatch.setattr(
        camera_monitor, "send_push", lambda messages, **k: state["pushes"].append(messages[0]["text"])
    )
    monkeypatch.setattr(
        camera_monitor, "force_close_session", lambda obj: events.append(("close", obj))
    )
    for level in ("info", "warning", "error"):
        monkeypatch.setattr(
            camera_monitor.logger, level,
            lambda message, *a, _lv=level, **k: state[_lv].append(str(message)),
        )
    yield state
    camera_monitor.active_pullpoints.clear()


def _run(monkeypatch, env, stop_after, cam_conf=None):
    clock = _Clock(stop_after, env["events"])
    monkeypatch.setattr(camera_monitor.time, "sleep", clock.sleep)
    monkeypatch.setattr(camera_monitor.time, "monotonic", clock.monotonic)
    with pytest.raises(_StopLoop):
        camera_monitor.monitor_single_camera(dict(cam_conf or CAM_CONF))
    return clock.calls


class _FakeZeepClient:
    def __init__(self):
        self.transport = type("T", (), {"session": type("S", (), {"auth": None})()})()


class _FakeRawPullpoint:
    def __init__(self, events):
        self._events = events
        self.SubscriptionReference = type(
            "Ref", (), {"Address": type("A", (), {"_value_1": "http://192.0.2.10/sub"})}
        )()

    def Unsubscribe(self):
        self._events.append(("unsubscribe", self))


class _FakePullpointService:
    def __init__(self, events, pull_error):
        self._events = events
        self._pull_error = pull_error
        self.zeep_client = _FakeZeepClient()

    def PullMessages(self, params):
        raise self._pull_error

    def Unsubscribe(self):
        self._events.append(("unsubscribe", self))


def _install_camera(monkeypatch, env, connect_errors=None, pull_error=None, subscribe_error=None):
    """
    ONVIF の接続を偽装する。connect_errors は接続ごとに投げる例外の列(None は成功)。
    成功した接続は pull_error を PullMessages で投げて監視ループを抜ける。
    """
    plan = list(connect_errors or [])

    class _FakeService:
        def __init__(self):
            self.zeep_client = _FakeZeepClient()

        def GetDeviceInformation(self):
            return type("Info", (), {"Model": "FAKE-1"})()

        def CreatePullPointSubscription(self):
            if subscribe_error is not None:
                raise subscribe_error
            return _FakeRawPullpoint(env["events"])

    class _FakeCamera:
        def __init__(self, *args, **kwargs):
            if plan:
                err = plan.pop(0)
                if err is not None:
                    raise err

        def create_devicemgmt_service(self):
            return _FakeService()

        def create_events_service(self):
            return _FakeService()

    monkeypatch.setattr(camera_monitor, "ONVIFCamera", _FakeCamera)
    monkeypatch.setattr(camera_monitor, "check_camera_time", lambda dm, name: True)
    monkeypatch.setattr(
        camera_monitor, "ONVIFService",
        lambda **kwargs: _FakePullpointService(env["events"], RuntimeError("unused")),
    )

    # 監視ループ内の例外(process_camera_event の想定外の例外や切断)を外側へ送出させる。
    # PullMessages 自体の失敗はループ内で握られて return するため、ここで差し替える。
    def _pull(cam_conf, pullpoint):
        if pull_error is not None:
            raise pull_error

    monkeypatch.setattr(camera_monitor, "_pull_events_until_reconnect", _pull)


class TestRecoveryReporting:
    """#1146"""

    def test_recovery_after_persistent_failure_logs_and_notifies_once(self, monkeypatch, env):
        # 5回失敗(5回目で障害通知) → 6回目で接続成功 → PullMessages が切断で抜ける
        _install_camera(
            monkeypatch, env,
            connect_errors=[RuntimeError("boom")] * 5 + [None],
            pull_error=RemoteDisconnected("peer closed"),
        )
        # 1失敗につき sleep 2回(バックオフ + 解放待ち3秒)×5、成功後の切断で 2回
        _run(monkeypatch, env, stop_after=5 * 2 + 1)

        recovered = [m for m in env["info"] if "復旧しました" in m]
        assert len(recovered) == 1
        assert "失敗5回" in recovered[0]
        # 停止時間は最初の失敗からの経過(20+3+40+3+80+3+160+3+300+3 秒)
        assert "停止約615秒" in recovered[0]
        assert len([p for p in env["pushes"] if "継続しています" in p]) == 1
        assert len([p for p in env["pushes"] if "復旧しました" in p]) == 1

    def test_short_failure_logs_recovery_without_notification(self, monkeypatch, env):
        _install_camera(
            monkeypatch, env,
            connect_errors=[RuntimeError("boom")] * 2 + [None],
            pull_error=RemoteDisconnected("peer closed"),
        )
        _run(monkeypatch, env, stop_after=2 * 2 + 1)

        recovered = [m for m in env["info"] if "復旧しました" in m]
        assert len(recovered) == 1
        assert "失敗2回" in recovered[0]
        assert env["pushes"] == []

    def test_no_recovery_log_without_prior_failure(self, monkeypatch, env):
        _install_camera(monkeypatch, env, pull_error=RemoteDisconnected("peer closed"))
        _run(monkeypatch, env, stop_after=1)
        assert not [m for m in env["info"] if "復旧しました" in m]


class TestErrorHint:
    """#1147"""

    @pytest.mark.parametrize(
        "message, expected",
        [
            ("Unauthorized", "PASSWORD"),
            ("Sender not authorized: ter:NotAuthorized", "PASSWORD"),
            ("Unknown error: Action Failed. The requested SOAP action failed.", "購読が残っている"),
            ("Unknown error: something else", None),
            ("timed out", None),
        ],
    )
    def test_hint_by_message(self, message, expected):
        hint = camera_monitor._error_hint(message)
        if expected is None:
            assert hint is None
        else:
            assert expected in hint

    def test_hint_is_logged_as_warning_not_error(self, monkeypatch, env):
        _install_camera(monkeypatch, env, connect_errors=[RuntimeError("Unknown error: Action Failed.")] * 10)
        _run(monkeypatch, env, stop_after=5 * 2)

        assert any("購読が残っている" in m for m in env["warning"])
        assert not any("Hint" in m for m in env["error"])
        assert not any("PASSWORD" in m for m in env["warning"] + env["error"])

    def test_describe_error_includes_soap_subcodes(self):
        class _Fault(Exception):
            def __init__(self):
                super().__init__("Action Failed")
                self.subcodes = ["ter:Action", "ter:SubscribeFailed"]
                self.detail = None

        text = camera_monitor._describe_error(_Fault())
        assert "Subcodes: ter:Action, ter:SubscribeFailed" in text


class TestDiagnosisPorts:
    """#1151"""

    def test_configured_onvif_port_and_rtsp(self):
        assert camera_monitor._diagnosis_ports({"port": 80}) == [80, 554]
        assert camera_monitor._diagnosis_ports({"port": 2020}) == [2020, 554]
        assert camera_monitor._diagnosis_ports({}) == [80, 554]

    def test_fatal_error_diagnoses_configured_ports(self, monkeypatch, env):
        _install_camera(monkeypatch, env, connect_errors=[RuntimeError("boom")] * 3)
        _run(monkeypatch, env, stop_after=2, cam_conf={**CAM_CONF, "port": 2020})
        assert env["diagnosed"] == [("192.0.2.10", [2020, 554])]

    def test_perform_emergency_diagnosis_checks_only_given_ports(self, monkeypatch):
        checked = []

        class _Sock:
            def __init__(self, *a):
                pass

            def settimeout(self, t):
                pass

            def connect_ex(self, addr):
                checked.append(addr[1])
                return 0

            def close(self):
                pass

        monkeypatch.setattr(camera_monitor.socket, "socket", _Sock)
        monkeypatch.setattr(camera_monitor.logger, "warning", lambda *a, **k: None)
        assert camera_monitor.perform_emergency_diagnosis("192.0.2.10", [80, 554]) == {80: True, 554: True}
        assert checked == [80, 554]


class TestConnectionErrorClassification:
    """#1152"""

    @pytest.mark.parametrize(
        "exc",
        [
            requests.exceptions.ConnectionError("Connection refused"),
            requests.exceptions.ConnectTimeout("connect timeout"),
            RemoteDisconnected("peer closed"),
            ConnectionResetError("reset"),
        ],
    )
    def test_network_errors_take_transient_path(self, monkeypatch, env, exc):
        _install_camera(monkeypatch, env, connect_errors=[exc] * 3)
        _run(monkeypatch, env, stop_after=2)
        # 致命的経路の「Connection/ONVIF Error」と緊急診断は通らない
        assert not any("Connection/ONVIF Error" in m for m in env["warning"])
        assert env["diagnosed"] == []

    @pytest.mark.parametrize("exc", [RuntimeError("Unauthorized"), RuntimeError("Action Failed")])
    def test_application_errors_stay_fatal(self, monkeypatch, env, exc):
        _install_camera(monkeypatch, env, connect_errors=[exc] * 3)
        _run(monkeypatch, env, stop_after=2)
        assert any("Connection/ONVIF Error" in m for m in env["warning"])

    def test_persistent_connection_refused_still_notifies_once(self, monkeypatch, env):
        _install_camera(
            monkeypatch, env, connect_errors=[requests.exceptions.ConnectionError("Connection refused")] * 10
        )
        _run(monkeypatch, env, stop_after=6 * 2)
        notified = [p for p in env["pushes"] if "継続しています" in p]
        assert len(notified) == 1
        assert "連続5回失敗" in notified[0]

    def test_persistent_unreachable_host_notifies_once(self, monkeypatch, env):
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: False)
        _run(monkeypatch, env, stop_after=6)
        notified = [p for p in env["pushes"] if "継続しています" in p]
        assert len(notified) == 1


class TestReleaseBeforeSleep:
    """#1162"""

    @pytest.mark.parametrize("exc", [RuntimeError("process_camera_event bug"), RemoteDisconnected("x")])
    def test_unsubscribe_and_close_happen_before_backoff_sleep(self, monkeypatch, env, exc):
        _install_camera(monkeypatch, env, pull_error=exc)
        _run(monkeypatch, env, stop_after=1)

        kinds = [kind for kind, _ in env["events"]]
        first_sleep = kinds.index("sleep")
        assert "unsubscribe" in kinds[:first_sleep]
        assert "close" in kinds[:first_sleep]

    def test_release_happens_once_per_session(self, monkeypatch, env):
        """解放は例外ハンドラと finally の両方から呼ばれるが、Unsubscribe は1回だけ。"""
        _install_camera(monkeypatch, env, pull_error=RuntimeError("boom"))
        _run(monkeypatch, env, stop_after=2)
        assert [kind for kind, _ in env["events"]].count("unsubscribe") == 1

    def test_periodic_reconnect_releases_once(self, monkeypatch, env):
        """正常な9分ごとの再接続(例外なし)でも解放は1回だけ。"""
        _install_camera(monkeypatch, env, pull_error=None)
        _run(monkeypatch, env, stop_after=1)
        kinds = [kind for kind, _ in env["events"]]
        assert kinds.count("unsubscribe") == 1
        assert kinds.index("unsubscribe") < kinds.index("sleep")

    def test_session_release_is_idempotent(self, env):
        session = camera_monitor._CameraSession()
        session.pullpoint = _FakeRawPullpoint(env["events"])
        session.release("cam")
        session.release("cam")
        assert [kind for kind, _ in env["events"]].count("unsubscribe") == 1
