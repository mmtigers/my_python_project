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
"""
import logging
import os
import sys
import time
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import DiscordErrorHandler


def _make_error_record(msg="something broke") -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )


class TestEmitDoesNotBlockOnSlowDiscord:
    def test_emit_returns_quickly_even_if_discord_post_is_slow(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()

        def slow_post(*args, **kwargs):
            time.sleep(2)
            return None

        with patch("core.logger.requests.post", side_effect=slow_post) as mock_post:
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
            assert mock_post.call_count == 1


class TestEmitHandlesNonStringMsg:
    def test_emit_does_not_raise_when_msg_is_not_a_string(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()
        record = _make_error_record(msg=ValueError("non-string msg"))

        with patch("core.logger.requests.post") as mock_post:
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


class TestEmitSkipsDiscordOriginatedMessages:
    def test_emit_skips_when_msg_already_mentions_discord(self, monkeypatch):
        """Discord通知自体の失敗ログを再度Discordへ送って無限ループ/スパムするのを防ぐ既存挙動。"""
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = DiscordErrorHandler()
        record = _make_error_record(msg="Discord webhook failed")

        with patch("core.logger.requests.post") as mock_post:
            handler.emit(record)
            time.sleep(0.3)
            assert mock_post.call_count == 0
