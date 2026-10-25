# MY_HOME_SYSTEM/tests/test_heartbeat.py
"""core/heartbeat.log_daily_heartbeat のテスト(Issue #1042)。"""
import logging
import os
import sys

import pytest
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core import heartbeat


@pytest.fixture(autouse=True)
def _base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if "[heartbeat]" in r.getMessage()]


def test_first_call_of_the_day_logs_once(caplog):
    logger = logging.getLogger("hb_test")
    with freeze_time("2026-10-25 09:00:00+09:00"), caplog.at_level(logging.INFO, logger="hb_test"):
        assert heartbeat.log_daily_heartbeat("nas_monitor", logger) is True
        assert heartbeat.log_daily_heartbeat("nas_monitor", logger) is False
    assert len(_messages(caplog)) == 1
    assert "nas_monitor" in _messages(caplog)[0]


def test_logs_again_on_the_next_day(caplog):
    logger = logging.getLogger("hb_test")
    with caplog.at_level(logging.INFO, logger="hb_test"):
        with freeze_time("2026-10-25 23:55:00+09:00"):
            assert heartbeat.log_daily_heartbeat("server_watchdog", logger) is True
        with freeze_time("2026-10-26 00:05:00+09:00"):
            assert heartbeat.log_daily_heartbeat("server_watchdog", logger) is True
    assert len(_messages(caplog)) == 2


def test_each_monitor_has_its_own_state(caplog):
    logger = logging.getLogger("hb_test")
    with freeze_time("2026-10-25 09:00:00+09:00"), caplog.at_level(logging.INFO, logger="hb_test"):
        assert heartbeat.log_daily_heartbeat("nas_monitor", logger) is True
        assert heartbeat.log_daily_heartbeat("nature_remo_monitor", logger) is True
    assert len(_messages(caplog)) == 2


def test_detail_is_appended(caplog):
    logger = logging.getLogger("hb_test")
    with freeze_time("2026-10-25 09:00:00+09:00"), caplog.at_level(logging.INFO, logger="hb_test"):
        heartbeat.log_daily_heartbeat("nas_monitor", logger, detail="usage=40%")
    assert "usage=40%" in _messages(caplog)[0]


def test_unwritable_state_does_not_raise(monkeypatch, caplog):
    logger = logging.getLogger("hb_test")
    monkeypatch.setattr(heartbeat.state_file, "write_text_atomic", lambda *a, **k: False)
    with freeze_time("2026-10-25 09:00:00+09:00"), caplog.at_level(logging.INFO, logger="hb_test"):
        assert heartbeat.log_daily_heartbeat("nas_monitor", logger) is True
        # 書けないときは毎回出るが、例外は出さない
        assert heartbeat.log_daily_heartbeat("nas_monitor", logger) is True


def test_name_is_sanitized_for_path(tmp_path):
    path = heartbeat._state_path("../evil/name")
    assert os.path.dirname(path) == str(tmp_path / "data" / "heartbeat")
    assert os.path.basename(path) == ".._evil_name.txt"
