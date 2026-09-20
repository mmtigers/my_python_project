# MY_HOME_SYSTEM/tests/test_nature_remo_monitor.py
"""
monitors/nature_remo_monitor.py の process_location() (Nature Remo電力データ処理)のテスト。

Issue #235の回帰テスト: 瞬時電力計測値(EPC=231)のパースに str.isdigit() を使用していたため、
太陽光発電等による逆潮流(売電)時の負の文字列値("-120"等)が str.isdigit()==False となり、
power_val が None のまま無警告でそのポーリング周期のデータが丸ごと破棄されていた不具合。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import nature_remo_monitor as nrm


def _make_appliance(val_str):
    return {
        "type": "EL_SMART_METER",
        "id": "app1",
        "nickname": "SmartMeter",
        "smart_meter": {
            "echonetlite_properties": [
                {"epc": 231, "val": val_str},
            ]
        },
    }


class TestProcessLocationNegativePowerValue:
    def _patch_fetch(self, monkeypatch, val_str):
        monkeypatch.setattr(
            nrm, "fetch_data_sync",
            lambda location, token: {"appliances": [_make_appliance(val_str)], "devices": []}
        )
        mock_process_power = AsyncMock()
        monkeypatch.setattr(nrm.sensor_service, "process_power_data", mock_process_power)
        return mock_process_power

    @pytest.mark.asyncio
    async def test_negative_wattage_string_is_still_recorded(self, monkeypatch):
        """逆潮流(売電)時の負の瞬時電力値("-120")が破棄されず記録されること"""
        mock_process_power = self._patch_fetch(monkeypatch, "-120")

        await nrm.process_location("伊丹", "dummy_token")

        mock_process_power.assert_called_once()
        args = mock_process_power.call_args[0]
        assert args[2] == -120.0

    @pytest.mark.asyncio
    async def test_positive_wattage_string_still_works(self, monkeypatch):
        mock_process_power = self._patch_fetch(monkeypatch, "850")

        await nrm.process_location("伊丹", "dummy_token")

        mock_process_power.assert_called_once()
        args = mock_process_power.call_args[0]
        assert args[2] == 850.0

    @pytest.mark.asyncio
    async def test_unparsable_value_logs_warning_and_is_not_recorded(self, monkeypatch):
        mock_process_power = self._patch_fetch(monkeypatch, "N/A")
        mock_logger = MagicMock()
        monkeypatch.setattr(nrm, "logger", mock_logger)

        await nrm.process_location("伊丹", "dummy_token")

        mock_process_power.assert_not_called()
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_value_logs_warning_and_is_not_recorded(self, monkeypatch):
        mock_process_power = self._patch_fetch(monkeypatch, None)
        mock_logger = MagicMock()
        monkeypatch.setattr(nrm, "logger", mock_logger)

        await nrm.process_location("伊丹", "dummy_token")

        mock_process_power.assert_not_called()
        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #758 (AUDIT-029): API レスポンスの異常形状への耐性と、センサー処理・
# エントリポイントの分岐。
#
# 5分間隔で回る収集スクリプトで、API が想定外の形を返したときに例外で落ちると
# その周期のデータが丸ごと欠ける。requests は一切飛ばさずにセッションを差し替える。
# ---------------------------------------------------------------------------
import config
import requests


class TestCreateSession:
    def test_https_adapter_has_retry_configured(self):
        session = nrm.create_session()
        adapter = session.get_adapter("https://api.nature.global/1/devices")
        assert adapter.max_retries.total == 3
        assert 503 in adapter.max_retries.status_forcelist


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._ok = status_ok

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("401 Unauthorized")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        res = self._responses.pop(0)
        if isinstance(res, Exception):
            raise res
        return res


class TestFetchDataSync:
    def test_empty_token_short_circuits(self, monkeypatch):
        called = []
        monkeypatch.setattr(nrm, "create_session", lambda: called.append(1))
        assert nrm.fetch_data_sync("伊丹", "") == {}
        assert called == []

    def test_returns_both_payloads_on_success(self, monkeypatch):
        session = _FakeSession([_FakeResponse([{"id": "a"}]), _FakeResponse([{"id": "d"}])])
        monkeypatch.setattr(nrm, "create_session", lambda: session)

        result = nrm.fetch_data_sync("伊丹", "tok")

        assert result == {"appliances": [{"id": "a"}], "devices": [{"id": "d"}]}
        assert session.calls == [
            "https://api.nature.global/1/appliances",
            "https://api.nature.global/1/devices",
        ]

    def test_http_error_is_logged_and_partial_result_is_returned(self, monkeypatch):
        """appliances は取れたが devices で失敗した場合、例外を投げずに
        取得済みの分だけを返す(呼び出し元の process_location は .get で読む)。"""
        session = _FakeSession([_FakeResponse([{"id": "a"}]), _FakeResponse(None, status_ok=False)])
        monkeypatch.setattr(nrm, "create_session", lambda: session)
        mock_logger = MagicMock()
        monkeypatch.setattr(nrm, "logger", mock_logger)

        result = nrm.fetch_data_sync("伊丹", "tok")

        assert result == {"appliances": [{"id": "a"}], "devices": []}
        mock_logger.error.assert_called_once()

    def test_connection_error_returns_empty_lists(self, monkeypatch):
        session = _FakeSession([requests.ConnectionError("dns failure")])
        monkeypatch.setattr(nrm, "create_session", lambda: session)
        monkeypatch.setattr(nrm, "logger", MagicMock())

        assert nrm.fetch_data_sync("伊丹", "tok") == {"appliances": [], "devices": []}


class TestProcessLocationResilience:
    @pytest.mark.asyncio
    async def test_empty_token_does_not_call_the_api(self, monkeypatch):
        called = []
        monkeypatch.setattr(nrm, "fetch_data_sync", lambda loc, tok: called.append(1) or {})
        await nrm.process_location("伊丹", "")
        assert called == []

    @pytest.mark.asyncio
    async def test_non_smart_meter_appliances_are_ignored(self, monkeypatch):
        mock_power = AsyncMock()
        monkeypatch.setattr(nrm.sensor_service, "process_power_data", mock_power)
        monkeypatch.setattr(
            nrm, "fetch_data_sync",
            lambda loc, tok: {"appliances": [{"type": "AC", "id": "ac1"}], "devices": []},
        )

        await nrm.process_location("伊丹", "tok")

        mock_power.assert_not_called()

    @pytest.mark.asyncio
    async def test_smart_meter_without_properties_is_tolerated(self, monkeypatch):
        """smart_meter / echonetlite_properties が欠けていても例外にしない。"""
        mock_power = AsyncMock()
        monkeypatch.setattr(nrm.sensor_service, "process_power_data", mock_power)
        monkeypatch.setattr(
            nrm, "fetch_data_sync",
            lambda loc, tok: {
                "appliances": [{"type": "EL_SMART_METER", "id": "m1"}], "devices": []
            },
        )

        await nrm.process_location("伊丹", "tok")

        mock_power.assert_not_called()

    @pytest.mark.asyncio
    async def test_properties_without_the_instant_power_epc_are_ignored(self, monkeypatch):
        mock_power = AsyncMock()
        monkeypatch.setattr(nrm.sensor_service, "process_power_data", mock_power)
        monkeypatch.setattr(
            nrm, "fetch_data_sync",
            lambda loc, tok: {
                "appliances": [{
                    "type": "EL_SMART_METER", "id": "m1",
                    "smart_meter": {"echonetlite_properties": [{"epc": 224, "val": "1"}]},
                }],
                "devices": [],
            },
        )

        await nrm.process_location("伊丹", "tok")

        mock_power.assert_not_called()

    @pytest.mark.asyncio
    async def test_temperature_and_humidity_are_forwarded(self, monkeypatch):
        mock_meter = AsyncMock()
        monkeypatch.setattr(nrm.sensor_service, "process_meter_data", mock_meter)
        monkeypatch.setattr(
            nrm, "fetch_data_sync",
            lambda loc, tok: {
                "appliances": [],
                "devices": [{
                    "id": "d1", "name": "リビング",
                    "newest_events": {"te": {"val": "24.5"}, "hu": {"val": "58"}},
                }],
            },
        )

        await nrm.process_location("伊丹", "tok")

        mock_meter.assert_called_once_with("d1", "伊丹_リビング", 24.5, 58.0)

    @pytest.mark.asyncio
    async def test_humidity_missing_defaults_to_zero(self, monkeypatch):
        mock_meter = AsyncMock()
        monkeypatch.setattr(nrm.sensor_service, "process_meter_data", mock_meter)
        monkeypatch.setattr(
            nrm, "fetch_data_sync",
            lambda loc, tok: {
                "appliances": [],
                "devices": [{"id": "d1", "newest_events": {"te": {"val": "20"}}}],
            },
        )

        await nrm.process_location("伊丹", "tok")

        assert mock_meter.call_args[0][3] == 0.0

    @pytest.mark.asyncio
    async def test_device_without_events_is_skipped(self, monkeypatch):
        mock_meter = AsyncMock()
        monkeypatch.setattr(nrm.sensor_service, "process_meter_data", mock_meter)
        monkeypatch.setattr(
            nrm, "fetch_data_sync",
            lambda loc, tok: {"appliances": [], "devices": [{"id": "d1"}]},
        )

        await nrm.process_location("伊丹", "tok")

        mock_meter.assert_not_called()


class TestMain:
    @pytest.mark.asyncio
    async def test_only_locations_with_a_token_are_processed(self, monkeypatch):
        processed = []

        async def _fake_process(location, token):
            processed.append(location)

        monkeypatch.setattr(nrm, "process_location", _fake_process)
        monkeypatch.setattr(config, "NATURE_REMO_ACCESS_TOKEN", "tok", raising=False)
        monkeypatch.setattr(config, "NATURE_REMO_ACCESS_TOKEN_TAKASAGO", "", raising=False)

        await nrm.main()

        assert processed == ["伊丹"]
