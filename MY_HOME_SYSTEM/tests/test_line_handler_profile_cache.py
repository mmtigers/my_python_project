# MY_HOME_SYSTEM/tests/test_line_handler_profile_cache.py
"""
handlers/line_handler.py の _get_display_name TTLキャッシュのテスト
(CODE_REVIEW_REPORT.md 8.1の再発防止)。

修正前はメッセージ受信のたびに line_bot_api.get_profile() を呼んでおり、
利用者・メッセージ頻度が増えるほどLINE APIのレート制限を消費するボトルネックに
なりうる、と指摘されていた。現在はTTL付きインメモリキャッシュで抑制されている。
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from handlers import line_handler


@pytest.fixture(autouse=True)
def _clean_cache():
    line_handler._profile_cache.clear()
    yield
    line_handler._profile_cache.clear()


@pytest.fixture
def fake_line_api(monkeypatch):
    fake_api = MagicMock()
    fake_api.get_profile.return_value = MagicMock(display_name="太郎")
    monkeypatch.setattr(line_handler, "line_bot_api", fake_api)
    return fake_api


def test_first_call_fetches_profile_from_api(fake_line_api):
    name = line_handler._get_display_name("U123")
    assert name == "太郎"
    fake_line_api.get_profile.assert_called_once_with("U123")


def test_repeated_calls_within_ttl_use_cache(fake_line_api, monkeypatch):
    base_time = 1_700_000_000.0
    monkeypatch.setattr(line_handler.time, "time", lambda: base_time)

    line_handler._get_display_name("U123")
    monkeypatch.setattr(line_handler.time, "time", lambda: base_time + 100)  # TTL(3600s)内
    line_handler._get_display_name("U123")
    line_handler._get_display_name("U123")

    assert fake_line_api.get_profile.call_count == 1


def test_call_after_ttl_expires_refetches_profile(fake_line_api, monkeypatch):
    base_time = 1_700_000_000.0
    monkeypatch.setattr(line_handler.time, "time", lambda: base_time)
    line_handler._get_display_name("U123")

    monkeypatch.setattr(
        line_handler.time, "time", lambda: base_time + line_handler._PROFILE_CACHE_TTL_SEC + 1
    )
    line_handler._get_display_name("U123")

    assert fake_line_api.get_profile.call_count == 2


def test_different_users_are_cached_independently(fake_line_api):
    line_handler._get_display_name("U_A")
    line_handler._get_display_name("U_B")
    assert fake_line_api.get_profile.call_count == 2


def test_returns_unknown_without_error_when_line_api_not_configured(monkeypatch):
    monkeypatch.setattr(line_handler, "line_bot_api", None)
    name = line_handler._get_display_name("U123")
    assert name == "Unknown"


def test_api_exception_falls_back_to_unknown_without_raising(monkeypatch):
    fake_api = MagicMock()
    fake_api.get_profile.side_effect = Exception("LINE API error")
    monkeypatch.setattr(line_handler, "line_bot_api", fake_api)

    name = line_handler._get_display_name("U123")
    assert name == "Unknown"
