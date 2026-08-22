# MY_HOME_SYSTEM/tests/test_switchbot_webhook_contract.py
"""
H-4: SwitchBot公式Webhookペイロード形式のcontract test。

既存 tests/test_webhook_router.py の _make_body() はトップレベルに
deviceTypeを置く自作形式でテストしており、SwitchBot公式のWebhook形式
(deviceTypeはcontext内、語彙は"WoContact"/"WoPresence"等)との突合に
なっていなかった。本ファイルは公式ドキュメント形式のペイロード
(dict/JSON)をそのままパースして、switchbot_webhookが
"unsupported_device"として黙って捨てずに処理することを検証する。
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from routers import webhook_router
from services import sensor_service
from models.switchbot import SwitchBotWebhookBody


@pytest.fixture(autouse=True)
def _reset_dedupe_cache():
    sensor_service.EVENT_CACHE.clear()
    yield
    sensor_service.EVENT_CACHE.clear()


# SwitchBot公式ドキュメントのWebhookペイロード形式(WoContact: 開閉センサー)。
# deviceTypeはcontext内にあり、トップレベルには無い。
OFFICIAL_CONTACT_SENSOR_PAYLOAD = {
    "eventType": "changeReport",
    "eventVersion": "1",
    "context": {
        "deviceType": "WoContact",
        "deviceMac": "AA:BB:CC:DD:EE:01",
        "detectionState": "open",
        "brightness": "bright",
        "openState": "open",
        "timeOfSample": 1699999999000,
    },
}

# 公式ドキュメントのWebhookペイロード形式(WoPresence: 人体検知センサー)。
OFFICIAL_MOTION_SENSOR_PAYLOAD = {
    "eventType": "changeReport",
    "eventVersion": "1",
    "context": {
        "deviceType": "WoPresence",
        "deviceMac": "AA:BB:CC:DD:EE:02",
        "detectionState": "DETECTED",
        "timeOfSample": 1699999999000,
    },
}


class TestOfficialPayloadContract:
    @pytest.mark.asyncio
    async def test_official_contact_sensor_payload_is_not_ignored_as_unsupported(self):
        body = SwitchBotWebhookBody(**OFFICIAL_CONTACT_SENSOR_PAYLOAD)
        assert body.context.deviceType == "WoContact"

        with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)), \
             patch.object(webhook_router.sensor_service, "process_sensor_data", new=AsyncMock(return_value=None)), \
             patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="玄関ドア"):
            result = await webhook_router.switchbot_webhook(body, token=None)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_official_motion_sensor_payload_is_not_ignored_as_unsupported(self):
        body = SwitchBotWebhookBody(**OFFICIAL_MOTION_SENSOR_PAYLOAD)
        assert body.context.deviceType == "WoPresence"

        with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)), \
             patch.object(webhook_router.sensor_service, "process_sensor_data", new=AsyncMock(return_value=None)), \
             patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="人感センサー"):
            result = await webhook_router.switchbot_webhook(body, token=None)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_device_type_resolved_from_context_not_top_level(self):
        """H-4回帰防止: device_typeの解決がcontext.deviceTypeを見ること
        (以前はcontextにdeviceTypeフィールドが無く、常にトップレベルの
        Noneにフォールバックしていた)。"""
        body = SwitchBotWebhookBody(**OFFICIAL_CONTACT_SENSOR_PAYLOAD)
        device_type = getattr(body.context, "deviceType", getattr(body, "deviceType", "Unknown"))
        assert device_type == "WoContact"
