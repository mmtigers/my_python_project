"""camera_monitor: カメラが書式未展開の日付を送るときの zeep エラー抑止(2026-09-19)。

実機で ONVIF 応答の日時項目に '2026-%m-07' のような値が届き、zeep が
トレースバック付きの ERROR を出していた(項目は None になり処理は継続)。
health_watch が journal の標準エラーも見るようになったため、発生源で落とす。
"""
import logging
import os
import sys

import isodate
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor  # noqa: F401  (フィルタの登録を import 時に行う)

ZEEP_LOGGER = "zeep.xsd.types.simple"


class _Collector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def zeep_records():
    handler = _Collector()
    zlog = logging.getLogger(ZEEP_LOGGER)
    zlog.addHandler(handler)
    try:
        yield zlog, handler.records
    finally:
        zlog.removeHandler(handler)


def _emit_translation_error(zlog, datestring):
    try:
        isodate.parse_datetime(datestring)
    except ValueError:
        zlog.exception("Error during xml -> python translation")


@pytest.mark.parametrize("datestring", ["2026-%m-07T10:00:00Z", "%Y-09-18T10:00:00Z"])
def test_unexpanded_strftime_date_is_suppressed(zeep_records, datestring):
    zlog, records = zeep_records
    _emit_translation_error(zlog, datestring)
    assert records == []


def test_other_date_errors_are_still_logged(zeep_records):
    zlog, records = zeep_records
    _emit_translation_error(zlog, "not-a-date")
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
