# MY_HOME_SYSTEM/tests/test_core_utils.py
"""
core/utils.py (指数バックオフ・ストレージ復帰待機) のテスト。

「一時的な障害からどう復帰するか」という例外処理そのものが対象。
実時間のsleepは全てmonkeypatchで無効化し、リトライ回数・最終結果のみを検証する。

以前は core/network.py (HTTPリトライセッション・API呼び出しリトライデコレータ) の
テストも同居していたが、core/network.py はどこからも呼び出されていない
デッドコードだったため、#411 品質でモジュールごと削除しファイル名も
test_core_utils.py に改名した。
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

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


class TestRetryWithBackoff:
    """Issue #292の回帰テスト: config.py::verify_and_initialize_storage と
    monitors/nas_monitor.py::check_write_permission が個別に実装していた
    Exponential Backoffループを core.utils.retry_with_backoff に集約した。
    共通ユーティリティ自体の挙動(成功、リトライ後の成功、リトライ枯渇、
    対象外の例外の即時伝播)を検証する。"""

    def test_returns_result_on_first_success_without_sleeping(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(utils.time, "sleep", lambda s: sleeps.append(s))

        result = utils.retry_with_backoff(
            lambda: "ok", max_retries=3, retryable_exceptions=(OSError,)
        )

        assert result == "ok"
        assert sleeps == []

    def test_retries_on_retryable_exception_then_succeeds(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(utils.time, "sleep", lambda s: sleeps.append(s))
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise OSError("transient")
            return "recovered"

        result = utils.retry_with_backoff(
            flaky, max_retries=5, retryable_exceptions=(OSError,), base_delay=1.0
        )

        assert result == "recovered"
        assert call_count["n"] == 3
        # 1回目: 1*2^0=1, 2回目: 1*2^1=2
        assert sleeps == [1, 2]

    def test_reraises_last_exception_after_exhausting_retries(self, monkeypatch):
        monkeypatch.setattr(utils.time, "sleep", lambda s: None)
        call_count = {"n": 0}

        def always_fails():
            call_count["n"] += 1
            raise OSError(f"fail-{call_count['n']}")

        with pytest.raises(OSError, match="fail-3"):
            utils.retry_with_backoff(
                always_fails, max_retries=2, retryable_exceptions=(OSError,)
            )

        # max_retries=2 -> 初回 + 追加2回 = 合計3回試行
        assert call_count["n"] == 3

    def test_non_retryable_exception_propagates_immediately_without_retry(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(utils.time, "sleep", lambda s: sleeps.append(s))
        call_count = {"n": 0}

        def raises_value_error():
            call_count["n"] += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            utils.retry_with_backoff(
                raises_value_error, max_retries=5, retryable_exceptions=(OSError,)
            )

        assert call_count["n"] == 1, "retryable_exceptionsに含まれない例外はリトライされないこと"
        assert sleeps == []

    def test_delay_is_capped_at_max_delay(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(utils.time, "sleep", lambda s: sleeps.append(s))
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise OSError("fail")
            return "ok"

        utils.retry_with_backoff(
            flaky, max_retries=5, retryable_exceptions=(OSError,), base_delay=1.0, max_delay=1.5
        )
        # 1回目: 1*2^0=1, 2回目: 1*2^1=2 -> 1.5にキャップされる
        assert sleeps == [1, 1.5]

    def test_on_retry_callback_receives_attempt_delay_and_exception(self, monkeypatch):
        monkeypatch.setattr(utils.time, "sleep", lambda s: None)
        calls = []
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise OSError("boom")
            return "ok"

        utils.retry_with_backoff(
            flaky,
            max_retries=3,
            retryable_exceptions=(OSError,),
            base_delay=1.0,
            on_retry=lambda attempt, delay, e: calls.append((attempt, delay, str(e))),
        )

        assert calls == [(0, 1.0, "boom")]

    def test_max_delay_defaults_to_unbounded(self, monkeypatch):
        """nas_monitor.check_write_permissionはmax_delayを指定せず呼び出すため、
        既存挙動(2^attempt秒が上限なく伸びる)を維持していることを確認する。"""
        sleeps = []
        monkeypatch.setattr(utils.time, "sleep", lambda s: sleeps.append(s))
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 5:
                raise OSError("fail")
            return "ok"

        utils.retry_with_backoff(
            flaky, max_retries=5, retryable_exceptions=(OSError,), base_delay=1.0
        )
        assert sleeps == [1, 2, 4, 8]


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

