# MY_HOME_SYSTEM/tests/test_sensor_service.py
"""
services/sensor_service.py のテスト。

旧 tests/test_unified_server.py は unified_server.callback_switchbot 等の
既に存在しないシンボルを参照しており(リファクタリングでsensor_service.pyへ移動済み)、
CIの `unittest discover` にも収集されずに放置されていた。
本ファイルは現在の実装(services/sensor_service.py)を対象に書き直したもの。
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import sensor_service


@pytest.fixture(autouse=True)
def _reset_sensor_state():
    """各テストの前後でセンサーのグローバル状態をリセットする"""
    sensor_service.IS_ACTIVE.clear()
    sensor_service.LAST_NOTIFY_TIME.clear()
    sensor_service.EVENT_CACHE.clear()
    sensor_service.MOTION_TASKS.clear()
    yield
    sensor_service.cancel_all_tasks()
    sensor_service.MOTION_TASKS.clear()


class TestIsDuplicateWebhook:
    def test_same_state_within_ttl_is_duplicate(self):
        assert sensor_service.is_duplicate_webhook("mac1", "open", 100.0) is False
        assert sensor_service.is_duplicate_webhook(
            "mac1", "open", 100.0 + sensor_service.DEDUPE_TTL_SECONDS - 0.01
        ) is True

    def test_same_state_after_ttl_is_not_duplicate(self):
        assert sensor_service.is_duplicate_webhook("mac1", "open", 100.0) is False
        assert sensor_service.is_duplicate_webhook(
            "mac1", "open", 100.0 + sensor_service.DEDUPE_TTL_SECONDS + 0.01
        ) is False

    def test_different_state_is_not_duplicate(self):
        assert sensor_service.is_duplicate_webhook("mac1", "open", 100.0) is False
        assert sensor_service.is_duplicate_webhook("mac1", "close", 100.1) is False


@pytest.mark.asyncio
class TestProcessSensorDataMotion:
    async def test_motion_detected_while_inactive_sends_notification_and_schedules_timer(self):
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_sensor_data(
                "mac_motion", "リビングセンサー", "リビング", "Motion Sensor", "detected"
            )

        assert sensor_service.IS_ACTIVE["mac_motion"] is True
        assert "mac_motion" in sensor_service.MOTION_TASKS
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert "動きがありました" in args[1][0]["text"]

    async def test_motion_detected_while_already_active_does_not_resend_notification(self):
        sensor_service.IS_ACTIVE["mac_motion"] = True
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_sensor_data(
                "mac_motion", "リビングセンサー", "リビング", "Motion Sensor", "detected"
            )

        mock_send.assert_not_called()
        # 継続検知でも「無反応監視タイマー」は再セットされる
        assert "mac_motion" in sensor_service.MOTION_TASKS


@pytest.mark.asyncio
class TestProcessSensorDataContact:
    async def test_contact_open_notifies_once_then_cooldown_suppresses(self):
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_sensor_data(
                "mac_door", "玄関ドア", "玄関", "Contact Sensor", "open"
            )
            assert mock_send.call_count == 1

            # クールダウン期間内の再検知は通知されない
            await sensor_service.process_sensor_data(
                "mac_door", "玄関ドア", "玄関", "Contact Sensor", "open"
            )
            assert mock_send.call_count == 1
