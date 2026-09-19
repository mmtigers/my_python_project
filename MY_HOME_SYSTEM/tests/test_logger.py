# MY_HOME_SYSTEM/tests/test_logger.py
"""
core/logger.py の DiscordErrorHandler.emit() のテスト。

M-5-5: emit() はエラーログのたびに同期 requests.post(timeout=5) を呼んでおり、
Discord側が遅い/落ちている場合、ログを出したリクエスト処理スレッドを最大5秒
ブロックしていた。バックグラウンドスレッドで送信することで emit() 自体は
即座に返るように修正した。

あわせて、Low項目として報告されていた `"Discord" not in record.msg` が
record.msg が非文字列(例外オブジェクト等)の場合にTypeErrorになりうる問題も
同時に修正する(str化してから比較する)。

Issue #742 (AUDIT-013) / #759 (AUDIT-030): 抑制の仕組みを2つとも入れ替えた。
- 「メッセージに Discord という語が含まれるか」の推測 → `extra={"skip_discord": True}`
  という明示フラグ(推測では実障害まで無音になっていた)
- レート制限が実質無かった状態 → 同一内容のクールダウン(重複排除)
"""
import itertools
import logging
import os
import sys
import threading
import time
from unittest.mock import patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core import logger as core_logger
from core.logger import DiscordErrorHandler

_msg_counter = itertools.count()


def _make_error_record(msg=None, **extra) -> logging.LogRecord:
    """テスト用の ERROR レコード。

    msg を省略した場合は毎回異なる文字列にする。Issue #759 の重複排除は
    「同じログ行から出たエラー」を束ねるため、同一 msg を使い回すテストが
    互いに干渉するのを避ける。
    """
    if msg is None:
        msg = f"something broke #{next(_msg_counter)}"
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.fixture(autouse=True)
def _reset_dedup_state():
    """重複排除はモジュールグローバルの状態を持つため、テストごとに消す。"""
    core_logger.reset_discord_dedup_state()
    yield
    core_logger.reset_discord_dedup_state()


class TestEmitDoesNotBlockOnSlowDiscord:
    def test_emit_returns_quickly_even_if_discord_post_is_slow(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()

        # C-L2 (Issue #414): 「遅いDiscord」は固定の time.sleep(2) ではなく、テスト終了時に
        # 解放する Event で再現する(バックグラウンドスレッドを2秒間残さない)
        release_post = threading.Event()

        def slow_post(*args, **kwargs):
            release_post.wait(2)
            return None

        with patch("core.discord.requests.post", side_effect=slow_post) as mock_post:
            start = time.monotonic()
            handler.emit(_make_error_record())
            elapsed = time.monotonic() - start

            assert elapsed < 1.0, (
                f"emit() blocked the calling thread for {elapsed:.2f}s waiting on "
                "the Discord webhook POST; it should return immediately and send "
                "in the background"
            )

            # バックグラウンドで確実に送信されることも確認する(遅延を待って検証)。
            deadline = time.monotonic() + 3
            while mock_post.call_count == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            release_post.set()
            assert mock_post.call_count == 1


class TestEmitHandlesNonStringMsg:
    def test_emit_does_not_raise_when_msg_is_not_a_string(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()
        record = _make_error_record(msg=ValueError("non-string msg"))

        with patch("core.discord.requests.post") as mock_post:
            # 例外を投げずに完了すること(修正前は "Discord" not in record.msg で
            # TypeErrorになりうる)。
            handler.emit(record)
            deadline = time.monotonic() + 3
            while mock_post.call_count == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert mock_post.call_count == 1


class TestEmitReportsFailuresInsteadOfSwallowingThem:
    """Issue #288の回帰テスト: emit()内で例外が起きた場合、以前は
    `except Exception: pass` で完全に握りつぶされ、ハンドラの不調を検知する
    手段がなかった。標準のhandleError()経由でsys.stderrに可視化されるようにする。
    handleError()はlogging機構を再度通らないため、無限ループの心配はない。
    """

    def test_emit_calls_handle_error_when_formatting_fails(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()

        with patch.object(handler, "format", side_effect=RuntimeError("boom")), \
             patch.object(handler, "handleError") as mock_handle_error:
            # 例外はemit()の外へ伝播しないこと。
            handler.emit(_make_error_record())

        mock_handle_error.assert_called_once()

    def test_emit_calls_handle_error_when_thread_start_fails(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()

        with patch("core.logger.threading.Thread") as mock_thread_cls, \
             patch.object(handler, "handleError") as mock_handle_error:
            mock_thread_cls.return_value.start.side_effect = RuntimeError("can't start new thread")
            handler.emit(_make_error_record())

        mock_handle_error.assert_called_once()


class TestEmitSkipsOnlyExplicitlyOptedOutRecords:
    """Issue #742 (AUDIT-013): 抑制の判定を内容の推測から明示フラグへ変えた。

    要件は「通知システム自身の失敗を通知しようとしない」であって
    「Discord という語を含むログを通知しない」ではない。以前の実装は後者で、
    smart_timelapse_generator の「Discord送信失敗: {file_name}」(タイムラプス
    配信の実障害)まで無音にしていた。
    """

    def test_emit_skips_records_flagged_with_skip_discord(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()
        record = _make_error_record(msg="Discord送信失敗: boom", skip_discord=True)

        with patch("core.discord.requests.post") as mock_post:
            handler.emit(record)
            time.sleep(0.3)
            assert mock_post.call_count == 0

    def test_emit_sends_records_that_mention_discord_without_the_flag(self, monkeypatch):
        """フラグの無い「Discord」を含むログは通知されること(本 Issue の本体)。"""
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()
        record = _make_error_record(msg="Discord送信失敗: 20260920_timelapse.mp4")

        with patch("core.discord.requests.post") as mock_post:
            handler.emit(record)
            core_logger.flush_pending_discord_notifications(timeout=2.0)
            assert mock_post.call_count == 1

    def test_notification_service_failure_logs_opt_out(self):
        """通知システム自身の失敗ログにフラグが付いていること(無限ループ防止)。"""
        import inspect

        from services import notification_service

        source = inspect.getsource(notification_service._send_discord_webhook)
        assert source.count('"skip_discord": True') == 2


class TestEmitDeduplicatesRepeatedErrors:
    """Issue #759 (AUDIT-030): 同一内容の繰り返しをウィンドウ内で束ねる。

    DISCORD_MAX_INFLIGHT_SENDERS は「同時実行数」の制限であってレート制限では
    なく、scheduler のタスク失敗(5分毎 → 1日288通)や camera_monitor の接続失敗
    (30秒毎 → 1日2,880通)で通知が洪水になり、同時期の別の重要なエラーが
    見落とされていた。
    """

    def test_same_message_is_sent_once_within_the_window(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()

        with patch("core.discord.requests.post") as mock_post:
            for _ in range(5):
                handler.emit(_make_error_record(msg="タスクが失敗しました"))
            core_logger.flush_pending_discord_notifications(timeout=2.0)

        assert mock_post.call_count == 1

    def test_different_messages_are_not_deduplicated(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()

        with patch("core.discord.requests.post") as mock_post:
            handler.emit(_make_error_record(msg="エラーA"))
            handler.emit(_make_error_record(msg="エラーB"))
            core_logger.flush_pending_discord_notifications(timeout=2.0)

        assert mock_post.call_count == 2

    def test_message_is_sent_again_after_the_window_elapses(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        # ウィンドウを 0 にすれば実時間を待たずに経過後の挙動を確認できる
        monkeypatch.setattr(core_logger, "DISCORD_DEDUP_WINDOW_SEC", 0.0)
        handler = DiscordErrorHandler()

        with patch("core.discord.requests.post") as mock_post:
            handler.emit(_make_error_record(msg="繰り返すエラー"))
            handler.emit(_make_error_record(msg="繰り返すエラー"))
            core_logger.flush_pending_discord_notifications(timeout=2.0)

        assert mock_post.call_count == 2

    def test_suppressed_count_is_reported_in_the_next_notification(self, monkeypatch):
        """「静かになった」のか「抑制されている」のかを運用側から区別できること。"""
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()

        sent_payloads = []

        def _capture(url, json=None, **kwargs):
            sent_payloads.append(json)

        with patch("core.logger.core_discord.post_with_retry", side_effect=_capture):
            handler.emit(_make_error_record(msg="連発するエラー"))
            for _ in range(3):
                handler.emit(_make_error_record(msg="連発するエラー"))
            # ウィンドウを閉じて次の1件を通す
            monkeypatch.setattr(core_logger, "DISCORD_DEDUP_WINDOW_SEC", 0.0)
            handler.emit(_make_error_record(msg="連発するエラー"))
            core_logger.flush_pending_discord_notifications(timeout=2.0)

        assert len(sent_payloads) == 2
        assert "3 件抑制されました" in sent_payloads[1]["content"]

    def test_webhook_unset_does_not_consume_a_dedup_slot(self, monkeypatch):
        """Webhook 未設定で送らなかった分を「送信済み」として記録しないこと。"""
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "")
        handler = DiscordErrorHandler()
        handler.emit(_make_error_record(msg="設定前のエラー"))

        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        with patch("core.discord.requests.post") as mock_post:
            handler.emit(_make_error_record(msg="設定前のエラー"))
            core_logger.flush_pending_discord_notifications(timeout=2.0)

        assert mock_post.call_count == 1


class TestWebhookFailureLogDoesNotLeakToken:
    def test_redact_webhook_url_masks_token_segment(self):
        from core import logger as core_logger
        url = "https://discord.com/api/webhooks/123456789012345678/AbC-dEf_123"
        assert core_logger._redact_webhook_url(url) == "https://discord.com/api/webhooks/123456789012345678/<redacted>"

    def test_send_webhook_failure_log_hides_token(self, monkeypatch, caplog):
        import logging
        from core import logger as core_logger

        def boom(*a, **k):
            raise RuntimeError("Max retries exceeded with url: /api/webhooks/1/AbC-dEf_123")

        # #661: 送信の実体は core/discord.py へ移った。
        from core import discord as core_discord
        monkeypatch.setattr(core_discord.requests, "post", boom)
        core_logger._webhook_failure_logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger=core_logger._webhook_failure_logger.name):
                core_logger.DiscordErrorHandler._send_webhook(
                    "https://discord.com/api/webhooks/1/AbC-dEf_123", {"content": "x"}
                )
        finally:
            core_logger._webhook_failure_logger.propagate = False
        assert "AbC-dEf_123" not in caplog.text
        assert "<redacted>" in caplog.text
