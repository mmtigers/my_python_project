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

# #251回帰防止用: detectionStateとopenStateが食い違う、実機に忠実なペイロード。
# SwitchBot公式Webhookドキュメント(OpenWonderLabs/SwitchBotAPI README-v1.0.md)の
# WoContact例と同様に、detectionStateは内蔵PIRのモーション検知結果
# ("NOT_DETECTED")であり、実際の開閉状態はopenState("open")側に入っている。
REALISTIC_CONTACT_SENSOR_OPEN_PAYLOAD = {
    "eventType": "changeReport",
    "eventVersion": "1",
    "context": {
        "deviceType": "WoContact",
        "deviceMac": "AA:BB:CC:DD:EE:03",
        "detectionState": "NOT_DETECTED",
        "doorMode": "OUT_DOOR",
        "brightness": "dim",
        "openState": "open",
        "timeOfSample": 1699999999000,
    },
}

# openStateが送られてこない旧来/未知形式のペイロード(後方互換確認用)。
LEGACY_CONTACT_SENSOR_PAYLOAD_WITHOUT_OPEN_STATE = {
    "eventType": "changeReport",
    "eventVersion": "1",
    "context": {
        "deviceType": "WoContact",
        "deviceMac": "AA:BB:CC:DD:EE:04",
        "detectionState": "open",
        "brightness": "bright",
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

    @pytest.mark.asyncio
    async def test_official_motion_payload_calls_process_sensor_data_with_resolved_device_type(self):
        """#94回帰防止: switchbot_webhookがprocess_sensor_dataへ渡すdevice_typeが、
        61行目で解決済みの値("WoPresence")であり、常にNoneになる未解決の
        body.deviceType(トップレベル)ではないことを検証する。
        以前は未解決のbody.deviceTypeが渡っていたため、公式形式のモーション
        イベントがsensor_service側のMotion判定に一切到達しなかった。"""
        body = SwitchBotWebhookBody(**OFFICIAL_MOTION_SENSOR_PAYLOAD)
        assert body.deviceType is None  # トップレベルは常に未設定(公式形式)

        with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)), \
             patch.object(webhook_router.sensor_service, "process_sensor_data", new=AsyncMock(return_value=None)) as mock_process, \
             patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="人感センサー"):
            await webhook_router.switchbot_webhook(body, token=None)

        mock_process.assert_awaited_once()
        called_dev_type = mock_process.await_args.args[3]
        assert called_dev_type == "WoPresence"


class TestOfficialMotionPayloadTriggersNotification:
    """#94回帰防止: 公式Webhook形式("WoPresence")のモーション検知が、
    process_sensor_data内のMotion判定分岐(通知送信+無反応タイマー起動)に
    実際に到達することをエンドツーエンドで検証する(process_sensor_dataをモックしない)。"""

    @pytest.mark.asyncio
    async def test_official_motion_event_triggers_notification_and_timer(self):
        from services import sensor_service as svc

        svc.IS_ACTIVE.pop("AA:BB:CC:DD:EE:02", None)
        if "AA:BB:CC:DD:EE:02" in svc.MOTION_TASKS:
            svc.MOTION_TASKS["AA:BB:CC:DD:EE:02"].cancel()
            del svc.MOTION_TASKS["AA:BB:CC:DD:EE:02"]

        body = SwitchBotWebhookBody(**OFFICIAL_MOTION_SENSOR_PAYLOAD)

        with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)), \
             patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="人感センサー"), \
             patch("services.sensor_service.send_push") as mock_send_push:
            result = await webhook_router.switchbot_webhook(body, token=None)

        try:
            assert result["status"] == "success"
            assert svc.IS_ACTIVE.get("AA:BB:CC:DD:EE:02") is True
            assert "AA:BB:CC:DD:EE:02" in svc.MOTION_TASKS
            mock_send_push.assert_called_once()
            sent_msg = mock_send_push.call_args.args[1][0]["text"]
            assert "動きがありました" in sent_msg
        finally:
            if "AA:BB:CC:DD:EE:02" in svc.MOTION_TASKS:
                svc.MOTION_TASKS["AA:BB:CC:DD:EE:02"].cancel()
                del svc.MOTION_TASKS["AA:BB:CC:DD:EE:02"]
            svc.IS_ACTIVE.pop("AA:BB:CC:DD:EE:02", None)


class TestContactSensorUsesOpenStateNotDetectionState:
    """#251回帰防止: WoContact(開閉センサー)の開閉判定は context.openState
    ("open"/"close"/"timeOutNotClose")を見るべきであり、同デバイス内蔵PIRの
    モーション検知結果である context.detectionState("DETECTED"/"NOT_DETECTED")を
    開閉状態として誤用してはならない(SwitchBot公式Webhookドキュメント参照)。"""

    @pytest.mark.asyncio
    async def test_realistic_payload_resolves_state_from_open_state_not_detection_state(self):
        body = SwitchBotWebhookBody(**REALISTIC_CONTACT_SENSOR_OPEN_PAYLOAD)
        assert body.context.detectionState == "NOT_DETECTED"
        assert body.context.openState == "open"

        with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)) as mock_save, \
             patch.object(webhook_router.sensor_service, "process_sensor_data", new=AsyncMock(return_value=None)) as mock_process, \
             patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="玄関ドア"):
            result = await webhook_router.switchbot_webhook(body, token=None)

        assert result["status"] == "success"
        mock_process.assert_awaited_once()
        # process_sensor_data(mac, name, location, dev_type, state) の第5引数(state)
        assert mock_process.await_args.args[4] == "open"

        # device_recordsへの保存にも、detectionState("not_detected")ではなく
        # openState由来の"open"が使われていること
        first_call_args = mock_save.await_args_list[0].args
        # save_log_async(table, columns, values); values = (timestamp, name, mac, "Webhook", state, brightness)
        assert first_call_args[2][4] == "open"

    @pytest.mark.asyncio
    async def test_legacy_payload_without_open_state_falls_back_to_detection_state(self):
        """openStateが送られてこない旧来/未知形式のペイロードでは、
        後方互換のためdetectionStateへフォールバックする。"""
        body = SwitchBotWebhookBody(**LEGACY_CONTACT_SENSOR_PAYLOAD_WITHOUT_OPEN_STATE)
        assert body.context.openState is None

        with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)), \
             patch.object(webhook_router.sensor_service, "process_sensor_data", new=AsyncMock(return_value=None)) as mock_process, \
             patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="玄関ドア"):
            result = await webhook_router.switchbot_webhook(body, token=None)

        assert result["status"] == "success"
        assert mock_process.await_args.args[4] == "open"


class TestContactSensorEndToEndNotification:
    """#251回帰防止: 実機に忠実なペイロード(detectionStateとopenStateが食い違う)でも、
    process_sensor_data内の開閉判定分岐(通知送信)に実際に到達することをエンドツーエンド
    で検証する(process_sensor_dataをモックしない)。修正前はdetectionState
    ("not_detected")がそのまま開閉状態として使われ、"open"/"timeoutnotclose"の
    いずれにも一致しないため、この通知は発火しなかった。"""

    @pytest.mark.asyncio
    async def test_realistic_open_payload_triggers_security_notification(self):
        from services import sensor_service as svc

        svc.LAST_NOTIFY_TIME.pop("AA:BB:CC:DD:EE:03", None)

        body = SwitchBotWebhookBody(**REALISTIC_CONTACT_SENSOR_OPEN_PAYLOAD)

        with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)), \
             patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="玄関ドア"), \
             patch("services.sensor_service.send_push") as mock_send_push:
            result = await webhook_router.switchbot_webhook(body, token=None)

        assert result["status"] == "success"
        mock_send_push.assert_called_once()
        sent_msg = mock_send_push.call_args.args[1][0]["text"]
        assert "開きました" in sent_msg
