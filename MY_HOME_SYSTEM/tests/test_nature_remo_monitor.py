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
