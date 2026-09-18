# MY_HOME_SYSTEM/tests/test_camera_monitor_backoff.py
"""
Issue #662: `monitor_single_camera` の再接続・バックオフ経路を固定する特性テスト。

この関数は255行・二重 `while True` で、`consecutive_errors` /
`transient_error_count` / `last_transient_error_time` / `is_first_connect` /
`was_disabled` の5つの状態がループの外で初期化され `try`/`except` の両方から
変異する。Issue #662 の分割が2度見送られたのは「挙動を変えないこと」を
既存テストで保証できなかったためなので、分割に先立って**現在の挙動**を
ここに固定する。

観測点は、外から見える副作用だけに絞ってある:
  - `time.sleep()` に渡された秒数の列(= バックオフの間隔そのもの)
  - `send_push()` の呼び出し回数(= 管理者への障害通知)
  - `perform_emergency_diagnosis()` の呼び出し有無
  - 解放処理(`Unsubscribe` / `force_close_session`)の対象と順序

実装の内部構造には触れていないため、関数を分割してもこのファイルは
そのまま通るはずである(通らなくなったら、それは挙動が変わったということ)。
"""
import os
import re
import sys
from http.client import RemoteDisconnected

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor


class _StopLoop(Exception):
    """monitor_single_camera の無限ループをテストで打ち切るための番兵例外。"""


CAM_CONF = {
    "id": "cam1",
    "name": "テストカメラ",
    "ip": "192.0.2.10",
    "port": 80,
    "user": "u",
    "pass": "p",
    "enabled": True,
}


@pytest.fixture(autouse=True)
def _neutralize_environment(monkeypatch):
    """devices.json を見に行かせず、通知も診断も実際には飛ばさない。"""
    monkeypatch.setattr(camera_monitor, "is_camera_enabled", lambda cam_conf: True)
    monkeypatch.setattr(camera_monitor, "perform_emergency_diagnosis", lambda ip: {})
    monkeypatch.setattr(camera_monitor, "send_push", lambda *a, **k: None)


class _SleepRecorder:
    """time.sleep を置き換えて秒数を記録し、指定回数で番兵例外を送出する。"""

    def __init__(self, stop_after):
        self.calls = []
        self._stop_after = stop_after

    def __call__(self, seconds):
        self.calls.append(seconds)
        if len(self.calls) >= self._stop_after:
            raise _StopLoop()


def _capture_warnings(monkeypatch):
    """
    `core.logger` の logger は propagate=False なので caplog では拾えない。
    WARNING の発生そのものを固定したいので、メソッドを直接差し替えて記録する。
    """
    messages = []
    monkeypatch.setattr(
        camera_monitor.logger, "warning", lambda message, *a, **k: messages.append(str(message))
    )
    return messages


def _capture_errors(monkeypatch):
    """`_capture_warnings` と同じ理由で logger.error を直接差し替えて記録する。"""
    messages = []
    monkeypatch.setattr(
        camera_monitor.logger, "error", lambda message, *a, **k: messages.append(str(message))
    )
    return messages


def _run(monkeypatch, stop_after, cam_conf=None):
    """monitor_single_camera を stop_after 回目の sleep で打ち切って実行する。"""
    recorder = _SleepRecorder(stop_after)
    monkeypatch.setattr(camera_monitor.time, "sleep", recorder)
    with pytest.raises(_StopLoop):
        camera_monitor.monitor_single_camera(dict(cam_conf or CAM_CONF))
    return recorder.calls


class TestUnreachableHostBackoff:
    """L3到達性チェックで落ちる経路(ONVIF接続まで進まない)。"""

    def test_backoff_doubles_from_20_seconds(self, monkeypatch):
        """10 * 2**consecutive_errors。1回目の失敗で 20 秒から始まり倍々になる。"""
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: False)

        assert _run(monkeypatch, stop_after=5) == [20, 40, 80, 160, 320]

    def test_backoff_is_capped_at_one_hour(self, monkeypatch):
        """max_backoff_time(3600秒)で頭打ちになり、それ以上は伸びない。"""
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: False)

        calls = _run(monkeypatch, stop_after=12)

        # 10 * 2**9 = 5120 > 3600 なので9回目以降は 3600 に張り付く
        assert calls[:8] == [20, 40, 80, 160, 320, 640, 1280, 2560]
        assert calls[8:] == [3600, 3600, 3600, 3600]

    def test_does_not_attempt_onvif_connection(self, monkeypatch):
        """到達不可と分かった時点で ONVIF 接続は試みない。"""
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: False)

        def _fail(*args, **kwargs):
            raise AssertionError("到達不可のカメラへ ONVIFCamera を生成した")

        monkeypatch.setattr(camera_monitor, "ONVIFCamera", _fail)

        _run(monkeypatch, stop_after=2)


class TestFatalErrorBackoff:
    """ONVIF 接続中の一般例外(認証失敗・タイムアウト等)の経路。"""

    @staticmethod
    def _always_fail(monkeypatch, exc=None):
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: True)
        monkeypatch.setattr(camera_monitor, "find_wsdl_path", lambda: "/tmp/wsdl")

        def _boom(*args, **kwargs):
            raise exc or RuntimeError("Unauthorized")

        monkeypatch.setattr(camera_monitor, "ONVIFCamera", _boom)

    def test_backoff_then_cleanup_wait_alternate(self, monkeypatch):
        """例外経路では「バックオフ」と finally の「解放待ち3秒」が交互に積まれる。"""
        self._always_fail(monkeypatch)

        calls = _run(monkeypatch, stop_after=6)

        assert calls == [20, 3, 40, 3, 80, 3]

    def test_notifies_admin_on_fifth_failure_then_every_twelfth(self, monkeypatch):
        """連続5回目で初回通知。以降は12の倍数回(12, 24, ...)でのみ通知する。"""
        self._always_fail(monkeypatch)
        notified_at = []

        def _record(messages, **kwargs):
            # 本文の「連続N回失敗」から、何回目の失敗で通知したかを取り出す
            text = messages[0]["text"]
            notified_at.append(int(re.search(r"連続(\d+)回失敗", text).group(1)))

        monkeypatch.setattr(camera_monitor, "send_push", _record)

        # 1周につき sleep は2回(バックオフ + 解放待ち)。24周ぶん回す。
        _run(monkeypatch, stop_after=48)

        assert notified_at == [5, 12, 24]

    def test_persistent_error_log_starts_at_the_fifth_failure(self, monkeypatch):
        """
        4回目までは WARNING のみ。5回目から ERROR("Persistent Error")を出す。
        通知(5回目・12の倍数)とは別の閾値なので、個別に固定しておく。
        """
        self._always_fail(monkeypatch)
        errors = _capture_errors(monkeypatch)

        # 1周につき sleep は2回。4周ぶん回した時点では ERROR は出ていない
        _run(monkeypatch, stop_after=8)
        assert errors == []

        errors.clear()
        _run(monkeypatch, stop_after=10)
        persistent = [m for m in errors if "Persistent Error" in m]
        assert len(persistent) == 1
        assert "(5 times)" in persistent[0]

    def test_survives_notification_failure(self, monkeypatch):
        """通知送信自体が失敗しても監視ループは止まらない。"""
        self._always_fail(monkeypatch)

        def _push_boom(*a, **k):
            raise RuntimeError("discord down")

        monkeypatch.setattr(camera_monitor, "send_push", _push_boom)

        # 通知が出る5回目(sleep 10回目)を越えて回り続けることを確認する
        calls = _run(monkeypatch, stop_after=12)
        assert len(calls) == 12

    def test_runs_emergency_diagnosis_only_while_host_is_reachable(self, monkeypatch):
        """ホストが生きているときだけ緊急診断を実行する。"""
        self._always_fail(monkeypatch)
        diagnosed = []
        monkeypatch.setattr(
            camera_monitor, "perform_emergency_diagnosis", lambda ip: diagnosed.append(ip) or {}
        )

        _run(monkeypatch, stop_after=2)

        assert diagnosed == ["192.0.2.10"]

    def test_skips_emergency_diagnosis_when_host_died_mid_flight(self, monkeypatch):
        """接続中にホストが落ちた場合(事前チェックは通過)は診断をスキップする。"""
        self._always_fail(monkeypatch)
        reachable = iter([True, False])
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: next(reachable, False))

        def _fail(ip):
            raise AssertionError("到達不可のホストへ緊急診断を実行した")

        monkeypatch.setattr(camera_monitor, "perform_emergency_diagnosis", _fail)

        _run(monkeypatch, stop_after=2)


class TestTransientErrorBackoff:
    """RemoteDisconnected 等の一時的障害の経路(finally の3秒待機を通らない)。"""

    @staticmethod
    def _always_disconnect(monkeypatch):
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: True)
        monkeypatch.setattr(camera_monitor, "find_wsdl_path", lambda: "/tmp/wsdl")

        def _boom(*args, **kwargs):
            raise RemoteDisconnected("peer closed")

        monkeypatch.setattr(camera_monitor, "ONVIFCamera", _boom)

    def test_backoff_doubles_and_still_waits_for_cleanup(self, monkeypatch):
        """
        一般例外と同じく「バックオフ」→ finally の「解放待ち3秒」の順に積まれる。
        `continue` で抜けても finally 末尾の `time.sleep(3)` は無条件に通る。
        """
        self._always_disconnect(monkeypatch)

        assert _run(monkeypatch, stop_after=6) == [20, 3, 40, 3, 80, 3]

    def test_escalates_to_warning_after_three_errors_within_15_seconds(self, monkeypatch):
        """15秒以内に3回続くと WARNING へ格上げされる(2回目まではdebug)。"""
        self._always_disconnect(monkeypatch)
        # time.time は進めない = 常に「15秒以内」
        monkeypatch.setattr(camera_monitor.time, "time", lambda: 1000.0)
        warnings = _capture_warnings(monkeypatch)

        # 1周につき sleep は2回(バックオフ + 解放待ち)。4周ぶん回す。
        _run(monkeypatch, stop_after=8)

        # 3回目・4回目の2件だけが WARNING
        assert len(warnings) == 2
        assert all("Transient Network Error" in message for message in warnings)

    def test_resets_transient_counter_after_15_seconds(self, monkeypatch):
        """15秒以上空けば連続扱いがリセットされ、WARNING に格上げされない。"""
        self._always_disconnect(monkeypatch)
        clock = iter([1000.0, 2000.0, 3000.0, 4000.0, 5000.0])
        monkeypatch.setattr(camera_monitor.time, "time", lambda: next(clock, 9000.0))
        warnings = _capture_warnings(monkeypatch)

        _run(monkeypatch, stop_after=8)

        assert warnings == []


class _FakeSession:
    def __init__(self):
        self.auth = None


class _FakeTransport:
    def __init__(self):
        self.session = _FakeSession()


class _FakeZeepClient:
    def __init__(self):
        self.transport = _FakeTransport()


class _FakeSubscriptionRef:
    class Address:
        _value_1 = "http://192.0.2.10/onvif/subscription"


class _FakeRawPullpoint:
    """CreatePullPointSubscription() の生の戻り値。"""

    def __init__(self):
        self.SubscriptionReference = _FakeSubscriptionRef()
        self.unsubscribed = 0

    def Unsubscribe(self):
        self.unsubscribed += 1


class TestCleanupOfIntermediatePullpoint:
    """
    `current_pullpoint` が「生のサブスクリプション → ONVIFService」と2段階で
    代入されている理由の固定(Issue #662 のコメントで分割の障害として挙げられた点)。
    """

    @staticmethod
    def _connect_until_onvif_service(monkeypatch, raw):
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: True)
        monkeypatch.setattr(camera_monitor, "find_wsdl_path", lambda: "/tmp/wsdl")
        monkeypatch.setattr(camera_monitor, "check_camera_time", lambda dm, name: True)

        class _FakeService:
            def __init__(self):
                self.zeep_client = _FakeZeepClient()

            def GetDeviceInformation(self):
                return type("Info", (), {"Model": "FAKE-1"})()

            def CreatePullPointSubscription(self):
                return raw

        class _FakeCamera:
            def __init__(self, *args, **kwargs):
                pass

            def create_devicemgmt_service(self):
                return _FakeService()

            def create_events_service(self):
                return _FakeService()

        monkeypatch.setattr(camera_monitor, "ONVIFCamera", _FakeCamera)

    def test_unsubscribes_raw_subscription_when_onvif_service_construction_fails(
        self, monkeypatch
    ):
        """
        `ONVIFService` の構築に失敗しても、直前に作った生のサブスクリプションは
        Unsubscribe される。ここを取りこぼすとカメラ側に購読が残り続ける。
        """
        raw = _FakeRawPullpoint()
        self._connect_until_onvif_service(monkeypatch, raw)

        def _boom(*args, **kwargs):
            raise RuntimeError("ONVIFService construction failed")

        monkeypatch.setattr(camera_monitor, "ONVIFService", _boom)

        _run(monkeypatch, stop_after=2)

        assert raw.unsubscribed == 1

    def test_cleanup_continues_when_unsubscribe_raises(self, monkeypatch):
        """Unsubscribe が例外を投げても後続の解放処理と3秒待機は行われる。"""

        class _AngryPullpoint(_FakeRawPullpoint):
            def Unsubscribe(self):
                raise RuntimeError("already gone")

        raw = _AngryPullpoint()
        self._connect_until_onvif_service(monkeypatch, raw)
        monkeypatch.setattr(
            camera_monitor, "ONVIFService", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("x"))
        )
        closed = []
        monkeypatch.setattr(camera_monitor, "force_close_session", lambda obj: closed.append(obj))

        calls = _run(monkeypatch, stop_after=2)

        assert calls == [20, 3]
        # 生の pullpoint・events_service・devicemgmt(mycam)の3つが閉じられる
        assert len(closed) == 3


class _FakePullpointService:
    """ONVIFService(PullPoint バインディング)の代役。"""

    def __init__(self, pull_error=None):
        self.zeep_client = _FakeZeepClient()
        self._pull_error = pull_error
        self.pull_calls = 0
        self.unsubscribed = 0

    def PullMessages(self, params):
        self.pull_calls += 1
        if self._pull_error:
            raise self._pull_error
        return None

    def Unsubscribe(self):
        self.unsubscribed += 1


def _connect_successfully(monkeypatch, pullpoint_service, connect_errors=0):
    """
    ONVIF 接続を成功させ、内側の監視ループまで到達させる。
    connect_errors を指定すると、その回数だけ接続を失敗させてから成功させる。
    """
    monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: True)
    monkeypatch.setattr(camera_monitor, "find_wsdl_path", lambda: "/tmp/wsdl")
    monkeypatch.setattr(camera_monitor, "check_camera_time", lambda dm, name: True)
    monkeypatch.setattr(camera_monitor, "force_close_session", lambda obj: None)
    monkeypatch.setattr(camera_monitor, "ONVIFService", lambda **kwargs: pullpoint_service)

    remaining = {"errors": connect_errors}

    class _FakeService:
        def __init__(self):
            self.zeep_client = _FakeZeepClient()

        def GetDeviceInformation(self):
            return type("Info", (), {"Model": "FAKE-1"})()

        def CreatePullPointSubscription(self):
            return _FakeRawPullpoint()

    class _FakeCamera:
        def __init__(self, *args, **kwargs):
            if remaining["errors"] > 0:
                remaining["errors"] -= 1
                raise RuntimeError("Unauthorized")

        def create_devicemgmt_service(self):
            return _FakeService()

        def create_events_service(self):
            return _FakeService()

    monkeypatch.setattr(camera_monitor, "ONVIFCamera", _FakeCamera)


@pytest.fixture(autouse=True)
def _clear_pullpoint_registry():
    """モジュールグローバルの active_pullpoints をテスト間で持ち越さない。"""
    yield
    camera_monitor.active_pullpoints.clear()


class TestInnerMonitorLoop:
    """接続成功後の監視ループ(内側の while True)から抜ける条件。"""

    def test_reconnects_after_threshold_consecutive_pull_failures(self, monkeypatch):
        """
        通常カメラは PullMessages の失敗が閾値(3回)連続したら再接続へ抜ける。
        以前は SESSION_LIFETIME(3600秒)まで events=None で回り続けていた。
        """
        service = _FakePullpointService(pull_error=RuntimeError("subscription gone"))
        _connect_successfully(monkeypatch, service)

        calls = _run(monkeypatch, stop_after=6)

        # 1・2回目の失敗は 0.5 秒待って継続、3回目で break → finally の3秒待機
        assert calls == [0.5, 0.5, 3, 0.5, 0.5, 3]
        assert service.pull_calls == 6

    def test_entrance_camera_reconnects_on_first_failure(self, monkeypatch):
        """玄関カメラだけは1回の失敗で即座に再接続へ抜ける(Renew非対応のため)。"""
        service = _FakePullpointService(pull_error=RuntimeError("timeout"))
        _connect_successfully(monkeypatch, service)
        entrance = dict(CAM_CONF, name="玄関カメラ")

        calls = _run(monkeypatch, stop_after=3, cam_conf=entrance)

        assert calls == [3, 3, 3]
        assert service.pull_calls == 3

    def test_session_lifetime_triggers_graceful_refresh(self, monkeypatch):
        """セッション寿命に達したら PullMessages せずに抜け直す。"""
        service = _FakePullpointService()
        _connect_successfully(monkeypatch, service)
        monkeypatch.setattr(camera_monitor, "SESSION_LIFETIME", -1)

        calls = _run(monkeypatch, stop_after=3)

        assert calls == [3, 3, 3]
        assert service.pull_calls == 0

    def test_disabling_mid_session_unsubscribes(self, monkeypatch):
        """監視中に enabled=false になったら購読を解除して待機側へ移る。"""
        service = _FakePullpointService()
        _connect_successfully(monkeypatch, service)
        # 1回目の外側ループは有効、内側ループ入り口で無効化される
        flags = iter([True, False])
        monkeypatch.setattr(camera_monitor, "is_camera_enabled", lambda conf: next(flags, False))

        calls = _run(monkeypatch, stop_after=2)

        # 内側ループを抜けて finally の3秒待機 → 外側ループは無効化待ちへ
        assert calls == [3, camera_monitor.DISABLED_POLL_INTERVAL_SEC]
        assert service.unsubscribed == 1

    def test_successful_connection_resets_the_backoff(self, monkeypatch):
        """
        接続に成功すると consecutive_errors が 0 に戻り、次の失敗は
        最大待機からではなく再び 20 秒から始まる。
        """
        service = _FakePullpointService()
        _connect_successfully(monkeypatch, service, connect_errors=3)
        monkeypatch.setattr(camera_monitor, "SESSION_LIFETIME", -1)

        calls = _run(monkeypatch, stop_after=8)

        # 3回失敗(20/40/80 + 各3秒) → 成功(寿命切れで3秒のみ) → 以降も成功が続く
        assert calls == [20, 3, 40, 3, 80, 3, 3, 3]

    def test_pullpoint_is_deregistered_after_each_session(self, monkeypatch):
        """セッションごとに active_pullpoints へ積みっぱなしにならない。"""
        service = _FakePullpointService()
        _connect_successfully(monkeypatch, service)
        monkeypatch.setattr(camera_monitor, "SESSION_LIFETIME", -1)

        _run(monkeypatch, stop_after=5)

        assert camera_monitor.active_pullpoints == []
