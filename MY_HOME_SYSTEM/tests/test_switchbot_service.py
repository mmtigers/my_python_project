# MY_HOME_SYSTEM/tests/test_switchbot_service.py
"""
services/switchbot_service.py のテスト。

- create_switchbot_auth_headers: SwitchBot API仕様に沿ったHMAC-SHA256署名が
  正しく生成されること(署名ロジックの回帰防止)
- request_switchbot_api: Timeout/ConnectionError発生時にExponential Backoffで
  リトライし、最終的に失敗してもNoneを返してシステムを止めない(Fail-Soft)こと
  (実ネットワークには一切アクセスしない)
"""
import base64
import hashlib
import hmac
import os
import sys
from unittest.mock import MagicMock

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import switchbot_service


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
