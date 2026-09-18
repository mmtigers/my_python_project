# MY_HOME_SYSTEM/tests/test_switchbot_service.py
"""
services/switchbot_service.py のテスト。

- create_switchbot_auth_headers: SwitchBot API仕様に沿ったHMAC-SHA256署名が
  正しく生成されること(署名ロジックの回帰防止)
- request_switchbot_api: Timeout/ConnectionError発生時にExponential Backoffで
  リトライし、最終的に失敗してもNoneを返してシステムを止めない(Fail-Soft)こと
  (実ネットワークには一切アクセスしない)
- trigger_tv_unlock: 元は services/quest/quest_service.py の
  QuestService._trigger_tv_unlock だったが、routine_service側でも同じ処理が
  必要になったため本モジュールへ切り出された(quest_service側のテストからも
  移動)。
"""
import base64
import hashlib
import hmac
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import notification_service, switchbot_service


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """バックオフの待機時間をテストで待たないようにする"""
    monkeypatch.setattr(switchbot_service.time, "sleep", lambda seconds: None)


class TestCreateSwitchbotAuthHeaders:
    def test_missing_token_returns_empty_dict_without_computing_signature(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", None)
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "secret")
        assert switchbot_service.create_switchbot_auth_headers() == {}

    def test_missing_secret_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "token")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", None)
        assert switchbot_service.create_switchbot_auth_headers() == {}

    def test_signature_matches_switchbot_hmac_spec(self, monkeypatch):
        """
        SwitchBot API仕様: sign = Base64(HMAC-SHA256(secret, token + t + nonce))
        実装と全く同じ計算式を独立に再実装し、生成された署名が一致することを確認する。
        """
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "test-token-123")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "test-secret-456")

        fixed_time_ms = 1700000000000
        fixed_nonce = "fixed-nonce-uuid"

        monkeypatch.setattr(switchbot_service.time, "time", lambda: fixed_time_ms / 1000.0)
        monkeypatch.setattr(switchbot_service.uuid, "uuid4", lambda: MagicMock(hex=fixed_nonce))

        headers = switchbot_service.create_switchbot_auth_headers()

        string_to_sign = f"test-token-123{fixed_time_ms}{fixed_nonce}"
        expected_sign = base64.b64encode(
            hmac.new(b"test-secret-456", string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")

        assert headers["Authorization"] == "test-token-123"
        assert headers["sign"] == expected_sign
        assert headers["t"] == str(fixed_time_ms)
        assert headers["nonce"] == fixed_nonce

    def test_different_nonce_produces_different_signature(self, monkeypatch):
        """毎回異なるnonceを使うことでリプレイ攻撃耐性を持たせている点の回帰確認"""
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "token")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "secret")

        headers1 = switchbot_service.create_switchbot_auth_headers()
        headers2 = switchbot_service.create_switchbot_auth_headers()

        assert headers1["nonce"] != headers2["nonce"]
        assert headers1["sign"] != headers2["sign"]


class TestRequestSwitchbotApiRetry:
    def test_retries_on_timeout_then_succeeds(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.json.return_value = {"statusCode": 100, "message": "success", "body": {}}
        mock_response.raise_for_status.return_value = None

        call_count = {"n": 0}

        def _flaky_get(url, headers, timeout):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise requests.exceptions.Timeout("simulated timeout")
            return mock_response

        monkeypatch.setattr(switchbot_service.requests, "get", _flaky_get)

        result = switchbot_service.request_switchbot_api("http://fake", {}, max_retries=4)

        assert result == {"statusCode": 100, "message": "success", "body": {}}
        assert call_count["n"] == 3

    def test_gives_up_after_max_retries_and_returns_none_fail_soft(self, monkeypatch):
        def _always_times_out(url, headers, timeout):
            raise requests.exceptions.ConnectionError("simulated connection error")

        monkeypatch.setattr(switchbot_service.requests, "get", _always_times_out)

        result = switchbot_service.request_switchbot_api("http://fake", {}, max_retries=3)

        assert result is None

    def test_fatal_http_error_stops_retrying_immediately(self, monkeypatch):
        """401等の恒久的エラーはリトライを続けても無駄なので即座に諦めること"""
        call_count = {"n": 0}

        def _unauthorized(url, headers, timeout):
            call_count["n"] += 1
            response = MagicMock()
            response.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
            return response

        monkeypatch.setattr(switchbot_service.requests, "get", _unauthorized)

        result = switchbot_service.request_switchbot_api("http://fake", {}, max_retries=4)

        assert result is None
        assert call_count["n"] == 1

    # ここから下は #661 のリトライ共通化(core.utils.retry_with_backoff への寄せ)で
    # 壊さないための特性テスト。既存3件では試行回数・待機秒数・ログ・
    # ValidationError の扱いが固定されていなかった。

    def test_exhaustion_makes_exactly_max_retries_attempts(self, monkeypatch):
        """max_retries は「初回を含む総試行回数」。共通化で1回ずれやすい境界。"""
        call_count = {"n": 0}

        def _always_times_out(url, headers, timeout):
            call_count["n"] += 1
            raise requests.exceptions.Timeout("simulated timeout")

        monkeypatch.setattr(switchbot_service.requests, "get", _always_times_out)
        monkeypatch.setattr(switchbot_service.time, "sleep", lambda _s: None)

        assert switchbot_service.request_switchbot_api("http://fake", {}, max_retries=4) is None
        assert call_count["n"] == 4

    def test_backoff_delays_are_1_2_4_with_no_sleep_after_last_attempt(self, monkeypatch):
        """待機は 1s, 2s, 4s。最後の試行のあとは待たずに諦める(無駄な4秒を足さない)。"""
        def _always_times_out(url, headers, timeout):
            raise requests.exceptions.Timeout("simulated timeout")

        slept = []
        monkeypatch.setattr(switchbot_service.requests, "get", _always_times_out)
        monkeypatch.setattr(switchbot_service.time, "sleep", lambda s: slept.append(s))

        switchbot_service.request_switchbot_api("http://fake", {}, max_retries=4)

        assert slept == [1, 2, 4]

    def test_fatal_error_does_not_sleep_at_all(self, monkeypatch):
        """401等はリトライしないので、待機も1回も入らないこと。"""
        def _unauthorized(url, headers, timeout):
            response = MagicMock()
            response.raise_for_status.side_effect = requests.exceptions.HTTPError("401")
            return response

        slept = []
        monkeypatch.setattr(switchbot_service.requests, "get", _unauthorized)
        monkeypatch.setattr(switchbot_service.time, "sleep", lambda s: slept.append(s))

        switchbot_service.request_switchbot_api("http://fake", {}, max_retries=4)

        assert slept == []

    def test_unexpected_payload_shape_propagates_to_caller(self, monkeypatch):
        """APIが想定外の形を返した場合は Fail-Soft の None に混ぜず、そのまま送出する。

        None にしてしまうと「通信できなかった」と区別がつかなくなる。
        """
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"unexpected": "shape"}

        monkeypatch.setattr(switchbot_service.requests, "get", lambda url, headers, timeout: response)
        monkeypatch.setattr(switchbot_service.time, "sleep", lambda _s: None)

        with pytest.raises(Exception) as excinfo:
            switchbot_service.request_switchbot_api("http://fake", {}, max_retries=4)

        assert not isinstance(excinfo.value, requests.exceptions.RequestException)

    def test_logs_one_warning_per_failed_attempt_plus_a_final_fail_soft_line(self, monkeypatch):
        """ログの本数は運用で「何回粘ったか」を読む手掛かりなので固定する。

        core.logger の logger は propagate=False のため caplog では拾えない。
        """
        def _always_times_out(url, headers, timeout):
            raise requests.exceptions.ConnectionError("simulated connection error")

        warnings = []
        monkeypatch.setattr(switchbot_service.requests, "get", _always_times_out)
        monkeypatch.setattr(switchbot_service.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            switchbot_service.logger, "warning", lambda message, *a, **k: warnings.append(str(message))
        )

        switchbot_service.request_switchbot_api("http://fake", {}, max_retries=4)

        assert len(warnings) == 5
        assert "(Attempt 1/4)" in warnings[0]
        assert "(Attempt 4/4)" in warnings[3]
        assert "Fail-Soft" in warnings[4]


class TestSendDeviceCommand:
    def test_missing_credentials_returns_none_without_http_call(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", None)
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", None)
        calls = []
        monkeypatch.setattr(switchbot_service.requests, "post", lambda *a, **kw: calls.append(1))

        result = switchbot_service.send_device_command("dev1", "turnOn")

        assert result is None
        assert calls == []

    def test_success_returns_response_json(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "token")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "secret")
        fake_response = MagicMock()
        fake_response.json.return_value = {"statusCode": 100, "message": "success"}
        fake_response.raise_for_status.return_value = None
        monkeypatch.setattr(switchbot_service.requests, "post", lambda *a, **kw: fake_response)

        result = switchbot_service.send_device_command("dev1", "turnOn")

        assert result == {"statusCode": 100, "message": "success"}

    def test_http_error_is_caught_and_returns_none(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "token")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "secret")

        def _raise(*a, **kw):
            raise requests.exceptions.RequestException("device offline")

        monkeypatch.setattr(switchbot_service.requests, "post", _raise)

        assert switchbot_service.send_device_command("dev1", "turnOn") is None


class TestGetDeviceStatus:
    def test_returns_parsed_status(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "token")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "secret")
        fake_response = MagicMock()
        fake_response.json.return_value = {"statusCode": 100, "message": "success", "body": {"power": "on"}}
        fake_response.raise_for_status.return_value = None
        monkeypatch.setattr(switchbot_service.requests, "get", lambda *a, **kw: fake_response)

        result = switchbot_service.get_device_status("dev1")

        assert result["body"]["power"] == "on"

    def test_exception_is_caught_and_returns_none(self, monkeypatch):
        def _raise(*a, **kw):
            raise requests.exceptions.Timeout("no response")
        monkeypatch.setattr(switchbot_service.requests, "get", _raise)

        assert switchbot_service.get_device_status("dev1") is None


class TestFetchDeviceNameCache:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        switchbot_service.DEVICE_NAME_CACHE.clear()
        yield
        switchbot_service.DEVICE_NAME_CACHE.clear()

    def test_missing_credentials_returns_false(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", None)
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", None)
        assert switchbot_service.fetch_device_name_cache() is False

    def test_populates_cache_from_devices_and_infrared_remotes(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "token")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "secret")
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "statusCode": 100,
            "message": "success",
            "body": {
                "deviceList": [{"deviceId": "d1", "deviceName": "玄関ドア"}],
                "infraredRemoteList": [{"deviceId": "ir1", "deviceName": "テレビ"}],
            },
        }
        fake_response.raise_for_status.return_value = None
        monkeypatch.setattr(switchbot_service.requests, "get", lambda *a, **kw: fake_response)

        result = switchbot_service.fetch_device_name_cache()

        assert result is True
        assert switchbot_service.get_device_name_by_id("d1") == "玄関ドア"
        assert switchbot_service.get_device_name_by_id("ir1") == "テレビ"

    def test_unknown_device_id_returns_none(self):
        assert switchbot_service.get_device_name_by_id("nonexistent") is None

    def test_non_success_status_code_returns_false(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "token")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "secret")
        fake_response = MagicMock()
        fake_response.json.return_value = {"statusCode": 190, "message": "invalid auth", "body": {}}
        fake_response.raise_for_status.return_value = None
        monkeypatch.setattr(switchbot_service.requests, "get", lambda *a, **kw: fake_response)

        assert switchbot_service.fetch_device_name_cache() is False

    def test_no_response_from_api_returns_false(self, monkeypatch):
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", "token")
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", "secret")

        def _always_fails(*a, **kw):
            raise requests.exceptions.ConnectionError("offline")
        monkeypatch.setattr(switchbot_service.requests, "get", _always_fails)

        assert switchbot_service.fetch_device_name_cache() is False


class TestDeviceNameCacheRetry:
    """Issue #533: 取得失敗後、再起動まで再試行しなかった問題の回帰テスト。"""

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        switchbot_service.DEVICE_NAME_CACHE.clear()
        monkeypatch.setattr(switchbot_service, "_last_fetch_attempt_at", None)
        yield
        switchbot_service.DEVICE_NAME_CACHE.clear()

    def test_failed_fetch_is_retried_after_interval(self, monkeypatch):
        calls = []
        monkeypatch.setattr(switchbot_service, "fetch_device_name_cache", lambda: calls.append(1) or False)
        clock = {"t": 1000.0}
        monkeypatch.setattr(switchbot_service.time, "monotonic", lambda: clock["t"])

        assert switchbot_service.get_device_name_by_id("d1") is None
        assert switchbot_service.get_device_name_by_id("d1") is None
        assert len(calls) == 1, "再試行間隔内では再取得しない"

        clock["t"] += switchbot_service.DEVICE_NAME_FETCH_RETRY_SEC
        assert switchbot_service.get_device_name_by_id("d1") is None
        assert len(calls) == 2, "間隔経過後は再取得する"

    def test_successful_fetch_is_not_repeated(self, monkeypatch):
        calls = []

        def fake_fetch():
            calls.append(1)
            switchbot_service.DEVICE_NAME_CACHE["d1"] = "玄関ドア"
            return True

        monkeypatch.setattr(switchbot_service, "fetch_device_name_cache", fake_fetch)
        clock = {"t": 0.0}
        monkeypatch.setattr(switchbot_service.time, "monotonic", lambda: clock["t"])
        assert switchbot_service.get_device_name_by_id("d1") == "玄関ドア"
        clock["t"] += switchbot_service.DEVICE_NAME_FETCH_RETRY_SEC * 2
        assert switchbot_service.get_device_name_by_id("d1") == "玄関ドア"
        assert len(calls) == 1


class TestTriggerTvUnlock:
    """
    trigger_tv_unlock() のテスト。
    実装は threading.Thread(daemon=True) でバックグラウンド実行するため、
    そのままでは実スレッドが絡みテストが非決定的(flaky)になる。
    threading.Thread.start を threading.Thread.run に差し替え、
    start()呼び出し時にターゲット関数を「同じスレッドで同期的に」実行させることで、
    実スレッド生成を避けつつ決定的にテストする。
    send_device_command/notification_serviceは全てモックし、実際のAPI呼び出しは行わない。
    """

    @pytest.fixture(autouse=True)
    def _run_background_thread_synchronously(self, monkeypatch):
        monkeypatch.setattr(threading.Thread, "start", threading.Thread.run)

    def test_success_status_code_does_not_notify_parents(self, monkeypatch):
        monkeypatch.setattr(
            switchbot_service, "send_device_command", MagicMock(return_value={"statusCode": 100})
        )
        mock_send_push = MagicMock()
        monkeypatch.setattr(notification_service, "send_push", mock_send_push)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "group123")

        switchbot_service.trigger_tv_unlock(context="quest_id=101")

        mock_send_push.assert_not_called()

    def test_non_success_status_code_notifies_parents_group(self, monkeypatch):
        monkeypatch.setattr(
            switchbot_service,
            "send_device_command",
            MagicMock(return_value={"statusCode": 190, "message": "Invalid auth"}),
        )
        mock_send_push = MagicMock()
        monkeypatch.setattr(notification_service, "send_push", mock_send_push)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "group123")

        switchbot_service.trigger_tv_unlock(context="quest_id=101")

        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args.kwargs
        assert call_kwargs["user_id"] == "group123"
        assert "失敗" in call_kwargs["messages"][0]["text"]

    def test_no_response_from_switchbot_is_treated_as_failure(self, monkeypatch):
        """switchbot側がFail-Soft設計上Noneを返すケース(未設定/通信失敗)でも
        例外として扱われ、親グループへの通知分岐に入ること。"""
        monkeypatch.setattr(switchbot_service, "send_device_command", MagicMock(return_value=None))
        mock_send_push = MagicMock()
        monkeypatch.setattr(notification_service, "send_push", mock_send_push)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "group123")

        switchbot_service.trigger_tv_unlock(context="quest_id=101")

        mock_send_push.assert_called_once()

    def test_failure_without_parents_group_configured_skips_notification(self, monkeypatch):
        """LINE_PARENTS_GROUP_ID が未設定の場合は、失敗しても通知を試みない
        (通知失敗で二重に例外を出さないためのFail-Soft分岐)。"""
        monkeypatch.setattr(
            switchbot_service, "send_device_command", MagicMock(return_value={"statusCode": 190})
        )
        mock_send_push = MagicMock()
        monkeypatch.setattr(notification_service, "send_push", mock_send_push)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "")

        switchbot_service.trigger_tv_unlock(context="quest_id=101")

        mock_send_push.assert_not_called()

    def test_does_not_spawn_a_real_background_thread(self, monkeypatch):
        """daemon=Trueのスレッドとして起動されることの回帰確認(実装の意図を固定する)。"""
        monkeypatch.setattr(
            switchbot_service, "send_device_command", MagicMock(return_value={"statusCode": 100})
        )
        captured_threads = []
        real_thread_cls = threading.Thread

        class _CapturingThread(real_thread_cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured_threads.append(self)

        monkeypatch.setattr(threading, "Thread", _CapturingThread)

        switchbot_service.trigger_tv_unlock(context="quest_id=101")

        assert len(captured_threads) == 1
        assert captured_threads[0].daemon is True
