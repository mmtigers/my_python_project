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

import requests.exceptions

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor


# autouse フィクスチャで差し替える前の本物(#1151 のテストで使う)
_REAL_EMERGENCY_DIAGNOSIS = camera_monitor.perform_emergency_diagnosis


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
def _neutralize_environment(monkeypatch, tmp_path):
    """devices.json を見に行かせず、通知も診断も実際には飛ばさない。"""
    # #1158: 接続成功時刻の記録をリポジトリ内に書かせない
    monkeypatch.setattr(camera_monitor, "CAMERA_STATUS_FILE", str(tmp_path / "camera_status.json"))
    monkeypatch.setattr(camera_monitor, "is_camera_enabled", lambda cam_conf: True)
    monkeypatch.setattr(camera_monitor, "perform_emergency_diagnosis", lambda ip, *a, **k: {})
    monkeypatch.setattr(camera_monitor, "send_push", lambda *a, **k: None)


class _SleepRecorder:
    """time.sleep を置き換えて秒数を記録し、指定回数で番兵例外を送出する。"""

    def __init__(self, stop_after):
        self.calls = []
        self._stop_after = stop_after
        # #1160: 継続障害の通知間隔は time.monotonic で決まるため、sleep した分だけ進む
        # 偽の時計を持つ(実時間では数ミリ秒で回り切ってしまい、間隔を観測できない)。
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.now += seconds
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
    monkeypatch.setattr(camera_monitor.time, "monotonic", recorder.monotonic)
    with pytest.raises(_StopLoop):
        camera_monitor.monitor_single_camera(dict(cam_conf or CAM_CONF))
    return recorder.calls


class TestUnreachableHostBackoff:
    """L3到達性チェックで落ちる経路(ONVIF接続まで進まない)。"""

    def test_backoff_doubles_from_20_seconds(self, monkeypatch):
        """10 * 2**consecutive_errors。1回目の失敗で 20 秒から始まり倍々になる。"""
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: False)

        assert _run(monkeypatch, stop_after=4) == [20, 40, 80, 160]

    def test_backoff_is_capped_at_five_minutes(self, monkeypatch):
        """
        #1160: MAX_BACKOFF_SEC(300秒)で頭打ちになり、それ以上は伸びない。

        以前は3600秒で、85分を超える障害ではカメラが復旧してから監視が再開するまで
        最大60分かかっていた(待機中は到達性を見ないため)。
        """
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: False)

        calls = _run(monkeypatch, stop_after=12)

        # 10 * 2**5 = 320 > 300 なので5回目以降は 300 に張り付く
        assert calls[:4] == [20, 40, 80, 160]
        assert calls[4:] == [300] * 8
        assert max(calls) == camera_monitor._ReconnectBackoff.MAX_BACKOFF_SEC == 300

    def test_record_failure_never_exceeds_the_cap(self):
        backoff = camera_monitor._ReconnectBackoff()
        waits = [backoff.record_failure() for _ in range(100)]
        assert max(waits) == backoff.MAX_BACKOFF_SEC

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

    def test_notifies_admin_on_fifth_failure_then_every_twelve_hours(self, monkeypatch):
        """
        連続5回目で初回通知。以降は PERSISTENT_RENOTIFY_SEC(12時間)ごとにだけ通知する。

        #1160: 以前は「12の倍数回目」だった。バックオフの上限を3600→300秒に下げると、
        回数ベースのままでは再通知が約12時間ごとから約1時間ごとに増えるため、時間で決める。
        """
        self._always_fail(monkeypatch)
        notified = []

        def _record(messages, **kwargs):
            # 本文の「連続N回失敗」から何回目の失敗か、偽の時計から何時に通知したかを取り出す
            text = messages[0]["text"]
            notified.append((int(re.search(r"連続(\d+)回失敗", text).group(1)), camera_monitor.time.monotonic()))

        monkeypatch.setattr(camera_monitor, "send_push", _record)

        # 1周につき sleep は2回(バックオフ300 + 解放待ち3)。約26時間ぶん回す。
        _run(monkeypatch, stop_after=2 * 310)

        assert [n for n, _ in notified][0] == 5
        assert len(notified) == 3
        times = [t for _, t in notified]
        renotify = camera_monitor._ReconnectBackoff.PERSISTENT_RENOTIFY_SEC
        assert all(b - a >= renotify for a, b in zip(times, times[1:]))
        assert all(b - a < renotify + 303 for a, b in zip(times, times[1:]))

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

    def test_skip_discord_only_set_on_milestone_failures(self, monkeypatch):
        """
        レビュー指摘の回帰テスト: 以前はskip_discordが5回目以降の全回に無条件で
        付いており、send_pushを呼ばない非マイルストーン回はcatch-all
        経由の通知もskip_discordで握りつぶされてDiscord通知が完全に消えていた。
        skip_discordはsend_pushと同じ回(5回目・12時間ごと)にだけ付き、それ以外の
        ERROR(1時間ごと)は付かない(=catch-all経由の通知に任せる)ことを固定する。

        #1160: その間の回は WARNING(Discordへ送られない)。以前は5回目以降の全回が
        ERROR だったが、上限3600秒のときの「1時間に1回」を時間で保つ。
        """
        self._always_fail(monkeypatch)
        calls = []
        monkeypatch.setattr(
            camera_monitor.logger,
            "error",
            lambda message, *a, **k: calls.append((str(message), k)),
        )
        warnings = _capture_warnings(monkeypatch)

        # 1周につきsleep2回(300 + 3)。5回目(push)から1時間以上回す(約17周)。
        _run(monkeypatch, stop_after=2 * 18)
        persistent = [(m, k) for m, k in calls if "Persistent Error" in m]
        assert len(persistent) == 2

        fifth_message, fifth_kwargs = persistent[0]
        assert "(5 times)" in fifth_message
        assert fifth_kwargs.get("extra") == {"skip_discord": True}

        hourly_message, hourly_kwargs = persistent[1]
        assert "(5 times)" not in hourly_message
        assert "extra" not in hourly_kwargs

        between = [m for m in warnings if "Persistent Error" in m]
        assert "(6 times)" in between[0]

    def test_new_failure_streak_notifies_again_at_the_fifth_failure(self):
        """接続に成功すると通知の記録も消え、次の連続障害は再び5回目で通知する。"""
        backoff = camera_monitor._ReconnectBackoff()
        levels = []
        for i in range(6):
            backoff.record_failure()
            levels.append(backoff.persistent_alert_level(now=100.0 + i))
        assert levels == ["none"] * 4 + ["push", "warning"]
        backoff.reset()
        for i in range(5):
            backoff.record_failure()
        assert backoff.persistent_alert_level(now=200.0) == "push"

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
            camera_monitor, "perform_emergency_diagnosis", lambda ip, *a, **k: diagnosed.append(ip) or {}
        )

        _run(monkeypatch, stop_after=2)

        assert diagnosed == ["192.0.2.10"]

    def test_skips_emergency_diagnosis_when_host_died_mid_flight(self, monkeypatch):
        """接続中にホストが落ちた場合(事前チェックは通過)は診断をスキップする。"""
        self._always_fail(monkeypatch)
        reachable = iter([True, False])
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: next(reachable, False))

        def _fail(ip, *a, **k):
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

    def test_force_reconnect_applies_to_every_camera(self, monkeypatch):
        """購読期限切れ前の自発的な張り直しが、玄関以外のカメラでも働くこと (Issue #766)。

        以前は `if cam_name == "玄関カメラ"` の分岐で、庭・駐車場(VIGI)には
        適用されていなかった。そのため10分で購読期限が切れ、PullMessages の
        3回連続失敗を経てから再購読する = 毎時6回の WARNING とイベント取りこぼし
        が発生していた。間隔を負値にして「常に期限が近い」状態を作り、
        PullMessages を1度も呼ばずに抜けることで先回りを確認する。
        """
        service = _FakePullpointService()
        _connect_successfully(monkeypatch, service)
        monkeypatch.setattr(camera_monitor, "FORCE_RECONNECT_INTERVAL_SEC", -1)
        garden = dict(CAM_CONF, name="庭カメラ")

        calls = _run(monkeypatch, stop_after=3, cam_conf=garden)

        assert calls == [3, 3, 3]
        assert service.pull_calls == 0

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


class TestOnvifOperationTimeout:
    """
    不具合修正: 実機で駐車場カメラがnetwork_logger.pyの"Ping:Unreachable"を記録した
    直後、動体検知ログが4時間以上完全に途絶える障害が発生した。原因は、
    ONVIF(zeep/requests)のSOAP呼び出しにライブラリ既定でタイムアウトが無く、
    TCP接続は張れるが応答が返らない種類のネットワーク不調でGetDeviceInformation()等が
    無期限にハングし、監視スレッドが例外を送出しないまま完全に停止してしまうこと
    だった(バックオフ・エラーログ・Discord通知の経路自体は正しいが、一度も
    呼ばれなかった)。`_connect_and_subscribe`がdevicemgmt/events_service/pullpointの
    3つのzeepクライアントすべてに`operation_timeout`を明示することを固定する。
    """

    def test_operation_timeout_is_set_on_all_three_zeep_clients(self, monkeypatch):
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: True)
        monkeypatch.setattr(camera_monitor, "find_wsdl_path", lambda: "/tmp/wsdl")
        monkeypatch.setattr(camera_monitor, "check_camera_time", lambda dm, name: True)

        devicemgmt_svc = _FakeZeepHolder()
        devicemgmt_svc.GetDeviceInformation = lambda: type("Info", (), {"Model": "FAKE-1"})()

        events_svc = _FakeZeepHolder()
        events_svc.CreatePullPointSubscription = lambda: _FakeRawPullpoint()

        pullpoint_svc = _FakeZeepHolder()

        class _FakeCamera:
            def __init__(self, *args, **kwargs):
                pass

            def create_devicemgmt_service(self):
                return devicemgmt_svc

            def create_events_service(self):
                return events_svc

        monkeypatch.setattr(camera_monitor, "ONVIFCamera", _FakeCamera)
        monkeypatch.setattr(camera_monitor, "ONVIFService", lambda **kwargs: pullpoint_svc)

        session = camera_monitor._CameraSession()
        camera_monitor._connect_and_subscribe(
            {"name": "テストカメラ", "ip": "192.0.2.1", "user": "u", "pass": "p"}, session, True
        )

        assert devicemgmt_svc.zeep_client.transport.operation_timeout == camera_monitor.ONVIF_OPERATION_TIMEOUT_SEC
        assert events_svc.zeep_client.transport.operation_timeout == camera_monitor.ONVIF_OPERATION_TIMEOUT_SEC
        assert pullpoint_svc.zeep_client.transport.operation_timeout == camera_monitor.ONVIF_OPERATION_TIMEOUT_SEC


class _FakeZeepHolder:
    """`.zeep_client.transport`を持つだけの最小フェイク(ONVIFカメラ/イベント/
    PullPointの各サービスオブジェクトの代役)。"""

    def __init__(self):
        self.zeep_client = _FakeZeepClient()


class TestConnectedInfoLog:
    """
    Issue #1145: 「📡 Connected」の INFO は、購読まで成功した初回だけ出す。

    以前は `GetDeviceInformation()` の直後に INFO を出して `is_first_connect=False` を
    ローカル変数にだけ設定しており、その後の `CreatePullPointSubscription()` が例外を
    出すと値が呼び出し元へ返らなかった。そのため購読に失敗し続けている間、リトライの
    たびに「初回接続」扱いの INFO が出ていた(2026-11-24 の玄関カメラで5回連続)。
    """

    @staticmethod
    def _connect_with_subscription_failures(monkeypatch, failures):
        """デバイス情報の取得までは成功し、購読だけ `failures` 回失敗するカメラ。"""
        monkeypatch.setattr(camera_monitor, "is_host_reachable", lambda ip: True)
        monkeypatch.setattr(camera_monitor, "find_wsdl_path", lambda: "/tmp/wsdl")
        monkeypatch.setattr(camera_monitor, "check_camera_time", lambda dm, name: True)
        monkeypatch.setattr(camera_monitor, "force_close_session", lambda obj: None)
        monkeypatch.setattr(camera_monitor, "ONVIFService", lambda **kwargs: _FakePullpointService())
        monkeypatch.setattr(camera_monitor, "SESSION_LIFETIME", -1)
        remaining = {"failures": failures}

        class _FakeService:
            def __init__(self):
                self.zeep_client = _FakeZeepClient()

            def GetDeviceInformation(self):
                return type("Info", (), {"Model": "NC-9820"})()

            def CreatePullPointSubscription(self):
                if remaining["failures"] > 0:
                    remaining["failures"] -= 1
                    raise RuntimeError("Unknown error: Action Failed. The requested SOAP action failed.")
                return _FakeRawPullpoint()

        class _FakeCamera:
            def __init__(self, *args, **kwargs):
                pass

            def create_devicemgmt_service(self):
                return _FakeService()

            def create_events_service(self):
                return _FakeService()

        monkeypatch.setattr(camera_monitor, "ONVIFCamera", _FakeCamera)

    @staticmethod
    def _capture_connected_info(monkeypatch):
        messages = []
        monkeypatch.setattr(
            camera_monitor.logger, "info", lambda message, *a, **k: messages.append(str(message))
        )
        return lambda: [m for m in messages if "Connected" in m]

    def test_no_connected_info_while_subscription_keeps_failing(self, monkeypatch):
        self._connect_with_subscription_failures(monkeypatch, failures=99)
        connected = self._capture_connected_info(monkeypatch)

        # 1周につき sleep 2回(バックオフ + 解放待ち)。5回失敗させる
        _run(monkeypatch, stop_after=10)

        assert connected() == []

    def test_connected_info_once_after_subscription_finally_succeeds(self, monkeypatch):
        self._connect_with_subscription_failures(monkeypatch, failures=3)
        connected = self._capture_connected_info(monkeypatch)

        # 3回失敗(各2回 sleep) → 成功後はセッション寿命切れで再接続を繰り返す(各1回 sleep)
        _run(monkeypatch, stop_after=6 + 4)

        assert len(connected()) == 1
        assert "NC-9820" in connected()[0]

    def test_connected_info_only_once_across_normal_reconnects(self, monkeypatch):
        self._connect_with_subscription_failures(monkeypatch, failures=0)
        connected = self._capture_connected_info(monkeypatch)

        _run(monkeypatch, stop_after=5)

        assert len(connected()) == 1


class TestEmergencyDiagnosisPorts:
    """#1151: 緊急診断は設定した ONVIF のポートと RTSP の554番だけを調べる。"""

    def test_uses_configured_port_and_rtsp(self):
        assert camera_monitor.diagnosis_ports({"port": 2020}) == [2020, 554]
        assert camera_monitor.diagnosis_ports({}) == [80, 554]
        assert camera_monitor.diagnosis_ports({"port": 554}) == [554]

    def test_probes_only_the_given_ports(self, monkeypatch):
        probed = []

        class _Sock:
            def settimeout(self, t):
                pass

            def connect_ex(self, addr):
                probed.append(addr[1])
                return 111

            def close(self):
                pass

        monkeypatch.setattr(camera_monitor.socket, "socket", lambda *a, **k: _Sock())
        monkeypatch.setattr(camera_monitor.logger, "warning", lambda *a, **k: None)
        result = _REAL_EMERGENCY_DIAGNOSIS("192.0.2.10", ports=[80, 554])
        assert probed == [80, 554]
        assert result == {80: False, 554: False}

    def test_fatal_error_passes_configured_ports(self, monkeypatch):
        TestFatalErrorBackoff._always_fail(monkeypatch)
        seen = []
        monkeypatch.setattr(
            camera_monitor, "perform_emergency_diagnosis",
            lambda ip, ports=None: seen.append(ports) or {},
        )
        _run(monkeypatch, stop_after=2, cam_conf={**CAM_CONF, "port": 2020})
        assert seen == [[2020, 554]]


class TestPersistentErrorHint:
    """#1147: ヒントは例外メッセージ別に出し分け、WARNING で出す。"""

    @pytest.mark.parametrize(
        "message, expected",
        [
            ("Unauthorized", "PASSWORD"),
            ("Sender not Authorized: NotAuthorized", "PASSWORD"),
            ("Unknown error: Action Failed. The requested SOAP action failed.", "購読が残っている"),
            ("Unknown error: something else", None),
            ("Read timed out", None),
        ],
    )
    def test_hint_by_message(self, message, expected):
        hint = camera_monitor._hint_for_error(message)
        if expected is None:
            assert hint is None
        else:
            assert expected in hint

    def test_hint_is_warning_never_error(self, monkeypatch):
        TestFatalErrorBackoff._always_fail(
            monkeypatch, exc=RuntimeError("Unknown error: Action Failed. The requested SOAP action failed.")
        )
        errors = _capture_errors(monkeypatch)
        warnings = _capture_warnings(monkeypatch)
        _run(monkeypatch, stop_after=10)  # 5回目(Persistent)まで
        assert not any("Hint" in m for m in errors)
        hints = [m for m in warnings if "Hint" in m]
        assert len(hints) == 1
        assert "PASSWORD" not in hints[0]


class TestFaultDetailInLog:
    """#1175: zeep の Fault は code・subcodes・detail をログに付ける。"""

    def test_fault_includes_subcodes_and_detail(self):
        from lxml import etree
        from zeep.exceptions import Fault

        detail = etree.fromstring("<Detail><Text>max pull points reached</Text></Detail>")
        fault = Fault(
            "Action Failed", code="env:Receiver", detail=detail,
            subcodes=["ter:Action", "ter:MaxPullPoints"],
        )
        text = camera_monitor._describe_error(fault)
        assert "Code: env:Receiver" in text
        assert "Subcodes: ter:Action, ter:MaxPullPoints" in text
        assert "max pull points reached" in text

    def test_fault_detail_is_truncated(self):
        from lxml import etree
        from zeep.exceptions import Fault

        detail = etree.fromstring("<D>" + "x" * 5000 + "</D>")
        text = camera_monitor._describe_error(Fault("f", detail=detail))
        assert len(text) < camera_monitor.FAULT_DETAIL_MAX_CHARS + 50

    def test_non_fault_output_is_unchanged(self):
        assert camera_monitor._describe_error(RuntimeError("x")) == ""

        class _WithDetail(Exception):
            detail = "d"
            content = "c" * 300

        assert camera_monitor._describe_error(_WithDetail()) == " | Detail: d | Content: " + "c" * 200

    def test_fault_subcode_reaches_the_persistent_log(self, monkeypatch):
        from zeep.exceptions import Fault

        TestFatalErrorBackoff._always_fail(
            monkeypatch, exc=Fault("Action Failed", subcodes=["ter:MaxPullPoints"])
        )
        errors = _capture_errors(monkeypatch)
        _run(monkeypatch, stop_after=10)
        assert any("ter:MaxPullPoints" in m for m in errors if "Persistent Error" in m)


class TestReleaseBeforeSleep:
    """#1162: 障害時は待機(sleep)の前に購読とセッションを解放し、解放は1回だけ。"""

    def test_unsubscribe_and_close_happen_before_backoff_sleep(self, monkeypatch):
        order = []

        class _Raw(_FakeRawPullpoint):
            def Unsubscribe(self):
                order.append("unsubscribe")

        TestCleanupOfIntermediatePullpoint._connect_until_onvif_service(monkeypatch, _Raw())
        monkeypatch.setattr(
            camera_monitor, "ONVIFService", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("x"))
        )
        monkeypatch.setattr(camera_monitor, "force_close_session", lambda obj: order.append("close"))

        def _sleep(seconds):
            order.append(f"sleep{seconds}")
            if len([o for o in order if o.startswith("sleep")]) >= 2:
                raise _StopLoop()

        monkeypatch.setattr(camera_monitor.time, "sleep", _sleep)
        with pytest.raises(_StopLoop):
            camera_monitor.monitor_single_camera(dict(CAM_CONF))

        assert order == ["unsubscribe", "close", "close", "close", "sleep20", "sleep3"]

    def test_release_happens_once_on_normal_reconnect(self, monkeypatch):
        service = _FakePullpointService()
        _connect_successfully(monkeypatch, service)
        monkeypatch.setattr(camera_monitor, "SESSION_LIFETIME", -1)

        _run(monkeypatch, stop_after=1)

        assert service.unsubscribed == 1

    def test_release_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(camera_monitor, "force_close_session", lambda obj: None)
        session = camera_monitor._CameraSession()
        raw = _FakeRawPullpoint()
        session.pullpoint = raw
        session.release("cam")
        session.release("cam")
        assert raw.unsubscribed == 1


class TestRecoveryNotice:
    """#1146: 失敗が続いた後に接続できたら、復旧を INFO で出す(通知は障害通知を送ったときだけ)。"""

    @staticmethod
    def _run_with_capture(monkeypatch, connect_errors):
        service = _FakePullpointService()
        _connect_successfully(monkeypatch, service, connect_errors=connect_errors)
        monkeypatch.setattr(camera_monitor, "SESSION_LIFETIME", -1)
        infos, pushes = [], []
        monkeypatch.setattr(camera_monitor.logger, "info", lambda m, *a, **k: infos.append(str(m)))
        monkeypatch.setattr(camera_monitor, "send_push", lambda msgs, **k: pushes.append(msgs[0]["text"]))
        # 失敗1回につき sleep 2回(バックオフ + 解放待ち)、成功後は解放待ち1回
        _run(monkeypatch, stop_after=2 * connect_errors + 1)
        return infos, pushes

    def test_five_failures_then_success_logs_and_notifies_once(self, monkeypatch):
        infos, pushes = self._run_with_capture(monkeypatch, connect_errors=5)
        recovered = [m for m in infos if "復旧しました" in m]
        assert len(recovered) == 1
        assert "失敗5回" in recovered[0]
        assert "停止約" in recovered[0]
        assert len([p for p in pushes if "復旧" in p]) == 1
        assert len([p for p in pushes if "継続しています" in p]) == 1

    def test_two_failures_then_success_logs_only(self, monkeypatch):
        infos, pushes = self._run_with_capture(monkeypatch, connect_errors=2)
        assert len([m for m in infos if "復旧しました" in m]) == 1
        assert pushes == []

    def test_no_recovery_log_without_failures(self, monkeypatch):
        infos, pushes = self._run_with_capture(monkeypatch, connect_errors=0)
        assert not any("復旧しました" in m for m in infos)


class TestConnectionErrorsAreTransient:
    """#1152: 接続拒否・接続タイムアウトは一時的障害の経路。SOAP Fault 等は致命的のまま。"""

    @pytest.mark.parametrize(
        "exc, transient",
        [
            (RemoteDisconnected("x"), True),
            (ConnectionResetError("x"), True),
            (requests.exceptions.ConnectionError("refused"), True),
            (requests.exceptions.ConnectTimeout("timeout"), True),
            (RuntimeError("Unauthorized"), False),
            (ConnectionRefusedError("Time verification failed"), False),
            (requests.exceptions.ReadTimeout("read"), False),
        ],
    )
    def test_routing_by_exception_type(self, monkeypatch, exc, transient):
        TestFatalErrorBackoff._always_fail(monkeypatch, exc=exc)
        diagnosed = []
        monkeypatch.setattr(
            camera_monitor, "perform_emergency_diagnosis", lambda ip, *a, **k: diagnosed.append(ip) or {}
        )
        _run(monkeypatch, stop_after=2)
        # 緊急診断は致命的障害の経路にしか無い
        assert (diagnosed == []) is transient

    def test_persistent_connection_refused_still_notifies(self, monkeypatch):
        """一時的障害の経路でも、連続5回目で継続障害の通知が1回出る(#890 のような長時間の拒否)。"""
        TestFatalErrorBackoff._always_fail(
            monkeypatch, exc=requests.exceptions.ConnectionError("Connection refused")
        )
        pushes = []
        monkeypatch.setattr(camera_monitor, "send_push", lambda msgs, **k: pushes.append(msgs[0]["text"]))
        _run(monkeypatch, stop_after=2 * 8)
        assert len(pushes) == 1
        assert "連続5回失敗" in pushes[0]
