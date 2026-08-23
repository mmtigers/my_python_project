# MY_HOME_SYSTEM/tests/test_sensor_service.py
"""
services/sensor_service.py のテスト。

旧 tests/test_unified_server.py は unified_server.callback_switchbot 等の
既に存在しないシンボルを参照しており(リファクタリングでsensor_service.pyへ移動済み)、
CIの `unittest discover` にも収集されずに放置されていた。
本ファイルは現在の実装(services/sensor_service.py)を対象に書き直したもの。
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
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

    def test_boundary_exactly_at_ttl_is_still_duplicate(self):
        """判定は `time_passed <= DEDUPE_TTL_SECONDS` のため、ちょうどTTL経過時点は重複扱い(境界値)"""
        assert sensor_service.is_duplicate_webhook("mac1", "open", 100.0) is False
        assert sensor_service.is_duplicate_webhook(
            "mac1", "open", 100.0 + sensor_service.DEDUPE_TTL_SECONDS
        ) is True

    def test_boundary_just_after_ttl_is_not_duplicate(self):
        assert sensor_service.is_duplicate_webhook("mac1", "open", 100.0) is False
        assert sensor_service.is_duplicate_webhook(
            "mac1", "open", 100.0 + sensor_service.DEDUPE_TTL_SECONDS + 1e-6
        ) is False


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
class TestSendInactiveNotification:
    async def test_notification_success_resets_active_state_and_task(self):
        sensor_service.IS_ACTIVE["mac_motion"] = True
        sensor_service.MOTION_TASKS["mac_motion"] = MagicMock()
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)):
            await sensor_service.send_inactive_notification("mac_motion", "テスト", "リビング", 0)

        assert sensor_service.IS_ACTIVE["mac_motion"] is False
        assert "mac_motion" not in sensor_service.MOTION_TASKS

    async def test_notification_failure_still_resets_active_state_and_task(self):
        """通知送信(send_push)が例外を送出しても、実際の無反応状態は変わらないため
        IS_ACTIVE/MOTION_TASKSのクリーンアップは行われるべき(M-5-2の回帰テスト)。
        従来はCancelledErrorしか捕捉していなかったため、この後片付けに到達せず、
        IS_ACTIVEがTrueのまま残り「動きが再開した」通知が二度と出なくなっていた。"""
        sensor_service.IS_ACTIVE["mac_motion"] = True
        sensor_service.MOTION_TASKS["mac_motion"] = MagicMock()
        with patch.object(sensor_service, "send_push", MagicMock(side_effect=RuntimeError("network down"))):
            await sensor_service.send_inactive_notification("mac_motion", "テスト", "リビング", 0)

        assert sensor_service.IS_ACTIVE["mac_motion"] is False
        assert "mac_motion" not in sensor_service.MOTION_TASKS

    async def test_cancellation_leaves_active_state_untouched(self):
        """タイムアウト前に動きが検知されてキャンセルされた場合は、
        実際にアクティブなままなのでIS_ACTIVEを書き換えてはいけない。"""
        sensor_service.IS_ACTIVE["mac_motion"] = True

        async def _run_and_cancel():
            task = asyncio.create_task(
                sensor_service.send_inactive_notification("mac_motion", "テスト", "リビング", 10)
            )
            await asyncio.sleep(0)
            task.cancel()
            await task

        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await _run_and_cancel()

        mock_send.assert_not_called()
        assert sensor_service.IS_ACTIVE["mac_motion"] is True


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


@pytest.mark.asyncio
class TestProcessMeterData:
    async def test_saves_temperature_and_humidity(self, isolated_db):
        await sensor_service.process_meter_data("dev1", "リビング温湿度計", 25.5, 48.0)
        with common.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_SWITCHBOT_LOGS} WHERE device_id='dev1'"
            ).fetchone()
        assert row is not None
        assert row["temperature"] == 25.5
        assert row["humidity"] == 48.0


@pytest.mark.asyncio
class TestProcessPowerData:
    async def test_first_reading_above_threshold_treats_prior_value_as_zero_and_notifies(self, isolated_db):
        """DB に前回値が無い場合は prev_wattage=0.0 とみなされるため、
        初回の値が閾値以上であれば OFF->ON の状態変化として通知される。"""
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 500, {"power_threshold_watts": 100})

        with common.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_POWER_USAGE} WHERE device_id='dev1'"
            ).fetchone()
        assert row is not None
        assert row["wattage"] == 500
        mock_send.assert_called_once()

    async def test_no_notify_settings_threshold_skips_notification_entirely(self, isolated_db):
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 500, {})
        mock_send.assert_not_called()

    async def test_crossing_threshold_upward_sends_on_notification(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev1', 'エアコン', 5, '2026-01-01T00:00:00')"
            )
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 500, {"power_threshold_watts": 100})

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1][0]["text"]
        assert "使用開始" in msg

    async def test_crossing_threshold_downward_sends_off_notification(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev1', 'エアコン', 500, '2026-01-01T00:00:00')"
            )
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 5, {"power_threshold_watts": 100})

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1][0]["text"]
        assert "使用終了" in msg

    async def test_staying_below_threshold_does_not_notify(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev1', 'エアコン', 5, '2026-01-01T00:00:00')"
            )
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 10, {"power_threshold_watts": 100})
        mock_send.assert_not_called()
