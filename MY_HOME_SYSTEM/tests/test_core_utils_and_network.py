# MY_HOME_SYSTEM/tests/test_core_utils_and_network.py
"""
core/utils.py (指数バックオフ・ストレージ復帰待機) と
core/network.py (HTTPリトライセッション・API呼び出しリトライデコレータ) のテスト。

いずれも「一時的な障害からどう復帰するか」という例外処理そのものが対象。
実時間のsleepは全てmonkeypatchで無効化し、リトライ回数・最終結果のみを検証する。
"""
import os
import sys
import time

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import core.network as network
import core.utils as utils


class TestWithExponentialBackoff:
    def test_returns_result_after_transient_failures(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(utils.time, "sleep", lambda s: sleeps.append(s))

        call_count = {"n": 0}

        @utils.with_exponential_backoff(base_delay=1, max_delay=300, alert_threshold=5)
        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionError("simulated transient failure")
            return "success"

        result = flaky()

        assert result == "success"
        assert call_count["n"] == 3
        # 1回目: 1 * 2^0 = 1, 2回目: 1 * 2^1 = 2
        assert sleeps == [1, 2]

    def test_delay_is_capped_at_max_delay(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(utils.time, "sleep", lambda s: sleeps.append(s))

        call_count = {"n": 0}

        @utils.with_exponential_backoff(base_delay=100, max_delay=150, alert_threshold=10)
        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("fail")
            return "ok"

        flaky()
        # 1回目: 100*2^0=100, 2回目: 100*2^1=200 -> 150にキャップされる
        assert sleeps == [100, 150]

    def test_alert_threshold_switches_log_level_to_error(self, monkeypatch, caplog):
        monkeypatch.setattr(utils.time, "sleep", lambda s: None)
        call_count = {"n": 0}

        @utils.with_exponential_backoff(base_delay=1, max_delay=10, alert_threshold=2)
        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("fail")
            return "ok"

        with caplog.at_level("WARNING", logger="core"):
            flaky()

        levels = [r.levelname for r in caplog.records]
        # 1回目(attempt=1 < threshold=2)はWARNING、2回目(attempt=2 >= threshold=2)はERROR
        assert "WARNING" in levels
        assert "ERROR" in levels


class TestWaitForStorageWarmup:
    def test_returns_true_immediately_when_path_accessible(self, tmp_path, monkeypatch):
        sleep_calls = []
        monkeypatch.setattr(utils.time, "sleep", lambda s: sleep_calls.append(s))

        result = utils.wait_for_storage_warmup(tmp_path, max_retries=3)

        assert result is True
        assert sleep_calls == []

    def test_returns_false_after_exhausting_retries_when_path_never_accessible(self, tmp_path, monkeypatch):
        monkeypatch.setattr(utils.time, "sleep", lambda s: None)
        missing_path = tmp_path / "does_not_exist" / "file.db"

        result = utils.wait_for_storage_warmup(missing_path, max_retries=2, base_delay=0.1, max_delay=1.0)

        assert result is False

    def test_recovers_after_transient_inaccessibility(self, tmp_path, monkeypatch):
        monkeypatch.setattr(utils.time, "sleep", lambda s: None)
        real_access = os.access
        call_count = {"n": 0}

        def _flaky_access(path, mode):
            call_count["n"] += 1
            if call_count["n"] < 2:
                return False
            return real_access(path, mode)

        monkeypatch.setattr(utils.os, "access", _flaky_access)

        result = utils.wait_for_storage_warmup(tmp_path, max_retries=5)

        assert result is True
        assert call_count["n"] == 2


class TestGetRetrySession:
    def test_returns_session_with_retry_adapter_mounted(self):
        session = network.get_retry_session(retries=4, backoff_factor=0.5)
        assert isinstance(session, requests.Session)

        https_adapter = session.get_adapter("https://example.com")
        assert https_adapter.max_retries.total == 4
        assert https_adapter.max_retries.backoff_factor == 0.5
        assert 500 in https_adapter.max_retries.status_forcelist

    def test_create_resilient_session_has_custom_status_forcelist(self):
        session = network.create_resilient_session(retries=2, status_forcelist=(503,))
        adapter = session.get_adapter("http://example.com")
        assert adapter.max_retries.total == 2
        assert adapter.max_retries.status_forcelist == (503,)


class TestRetryApiCallDecorator:
    def test_retries_on_request_exception_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        call_count = {"n": 0}

        @network.retry_api_call
        def flaky_api_call():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise requests.exceptions.ConnectionError("simulated network blip")
            return "ok"

        result = flaky_api_call()
        assert result == "ok"
        assert call_count["n"] == 2

    def test_gives_up_after_max_attempts_and_reraises(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        call_count = {"n": 0}

        @network.retry_api_call
        def always_fails():
            call_count["n"] += 1
            raise requests.exceptions.Timeout("always times out")

        with pytest.raises(requests.exceptions.Timeout):
            always_fails()

        assert call_count["n"] == 3  # stop_after_attempt(3)

    def test_does_not_retry_non_request_exceptions(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        call_count = {"n": 0}

        @network.retry_api_call
        def raises_value_error():
            call_count["n"] += 1
            raise ValueError("not a network error, should not be retried")

        with pytest.raises(ValueError):
            raises_value_error()

        assert call_count["n"] == 1
