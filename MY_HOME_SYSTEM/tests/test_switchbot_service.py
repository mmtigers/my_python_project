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
