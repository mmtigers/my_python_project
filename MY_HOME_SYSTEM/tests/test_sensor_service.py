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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
import config
from services import sensor_service


@pytest.fixture(autouse=True)
def _reset_sensor_state():
    """各テストの前後でセンサーのグローバル状態をリセットする"""
    sensor_service.IS_ACTIVE.clear()
    sensor_service.LAST_NOTIFY_TIME.clear()
    sensor_service.EVENT_CACHE.clear()
    sensor_service.MOTION_TASKS.clear()
    sensor_service._mac_last_seen.clear()
    sensor_service.POWER_STATE.clear()
    sensor_service.CONTACT_OPENED_AT.clear()
    sensor_service.LAST_MOTION_AT.clear()
    yield
    sensor_service.cancel_all_tasks()
    sensor_service.MOTION_TASKS.clear()
    sensor_service._mac_last_seen.clear()


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
        kwargs = mock_send.call_args.kwargs
        assert "動きがありました" in kwargs["messages"][0]["text"]

    async def test_motion_detected_while_already_active_does_not_resend_notification(self):
        sensor_service.IS_ACTIVE["mac_motion"] = True
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_sensor_data(
                "mac_motion", "リビングセンサー", "リビング", "Motion Sensor", "detected"
            )

        mock_send.assert_not_called()
        # 継続検知でも「無反応監視タイマー」は再セットされる
        assert "mac_motion" in sensor_service.MOTION_TASKS

    async def test_official_woresence_device_type_also_triggers_motion_logic(self):
        """#94回帰防止: SwitchBot公式Webhookの語彙("WoPresence")は
        "Motion" の部分一致に合致しないため、以前はこの分岐に到達せず
        通知・無反応タイマーが一切発火しなかった。"""
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_sensor_data(
                "mac_motion", "人感センサー", "玄関", "WoPresence", "detected"
            )

        assert sensor_service.IS_ACTIVE["mac_motion"] is True
        assert "mac_motion" in sensor_service.MOTION_TASKS
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert "動きがありました" in kwargs["messages"][0]["text"]


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
        with get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_SWITCHBOT_LOGS} WHERE device_id='dev1'"
            ).fetchone()
        assert row is not None
        assert row["temperature"] == 25.5
        assert row["humidity"] == 48.0

    async def test_save_failure_is_logged_as_error_not_debug_success(self, isolated_db):
        """Issue #740 (AUDIT-011): save_log_async は Fail-Soft で False を返すため、
        戻り値を破棄すると「保存されていないのに saved と DEBUG に出る」状態になる。
        ポーリング経路のため処理は止めないが、どのデバイスの分が欠けたかを
        特定できるよう ERROR で記録すること。"""
        fake_logger = MagicMock()
        with patch.object(sensor_service, "save_log_async", new=AsyncMock(return_value=False)), \
             patch.object(sensor_service, "logger", fake_logger):
            await sensor_service.process_meter_data("dev1", "リビング温湿度計", 25.5, 48.0)

        assert fake_logger.error.called
        assert "リビング温湿度計" in fake_logger.error.call_args[0][0]
        fake_logger.debug.assert_not_called()


@pytest.mark.asyncio
class TestProcessPowerData:
    async def test_first_reading_above_threshold_treats_prior_value_as_zero_and_notifies(self, isolated_db):
        """DB に前回値が無い場合は prev_wattage=0.0 とみなされるため、
        初回の値が閾値以上であれば OFF->ON の状態変化として通知される。"""
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 500, {"power_threshold_watts": 100})

        with get_db_cursor() as cur:
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
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev1', 'エアコン', 5, '2026-01-01T00:00:00')"
            )
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 500, {"power_threshold_watts": 100})

        mock_send.assert_called_once()
        msg = mock_send.call_args.kwargs["messages"][0]["text"]
        assert "使用開始" in msg

    async def test_crossing_threshold_downward_sends_off_notification(self, isolated_db):
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev1', 'エアコン', 500, '2026-01-01T00:00:00')"
            )
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 5, {"power_threshold_watts": 100})

        mock_send.assert_called_once()
        msg = mock_send.call_args.kwargs["messages"][0]["text"]
        assert "使用終了" in msg

    async def test_save_failure_still_evaluates_the_threshold_notification(self, isolated_db):
        """Issue #740 (AUDIT-011): 保存の成否は閾値判定(prev_wattage と引数 wattage)に
        影響しない。DB の一時的なロックで「電気の付けっぱなし通知」まで落とすほうが
        実害が大きいため、保存失敗は ERROR に記録しつつ通知判定は続ける。"""
        fake_logger = MagicMock()
        with patch.object(sensor_service, "save_log_async", new=AsyncMock(return_value=False)), \
             patch.object(sensor_service, "logger", fake_logger), \
             patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 500, {"power_threshold_watts": 100})

        mock_send.assert_called_once()
        assert fake_logger.error.called
        assert "エアコン" in fake_logger.error.call_args[0][0]

    async def test_staying_below_threshold_does_not_notify(self, isolated_db):
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev1', 'エアコン', 5, '2026-01-01T00:00:00')"
            )
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("dev1", "エアコン", 10, {"power_threshold_watts": 100})
        mock_send.assert_not_called()


@pytest.mark.asyncio
class TestInactiveStateIsClearedBeforeSending:
    """Issue #534: 「止まりました」送信中に再検知が来ても再開通知が出るよう、
    IS_ACTIVE は送信前に False へ落とす。"""

    async def test_is_active_is_false_when_send_push_runs(self):
        sensor_service.IS_ACTIVE["mac_motion"] = True
        sensor_service.MOTION_TASKS["mac_motion"] = MagicMock()
        observed = {}

        def fake_send(*a, **k):
            observed["active_during_send"] = sensor_service.IS_ACTIVE.get("mac_motion")
            return True

        with patch.object(sensor_service, "send_push", fake_send):
            await sensor_service.send_inactive_notification("mac_motion", "テスト", "リビング", 0)

        assert observed["active_during_send"] is False
        assert sensor_service.IS_ACTIVE["mac_motion"] is False

    async def test_detection_during_send_is_reported_as_resumed(self):
        """送信中に届いた検知は「非アクティブ→アクティブ」として再開通知を送ること。"""
        sensor_service.IS_ACTIVE["mac_motion"] = True
        sensor_service.MOTION_TASKS["mac_motion"] = MagicMock()
        sent = []

        def fake_send(*a, **k):
            sent.append(k["messages"][0]["text"])
            return True

        with patch.object(sensor_service, "send_push", fake_send):
            stop_task = asyncio.create_task(
                sensor_service.send_inactive_notification("mac_motion", "テスト", "リビング", 0)
            )
            # タスク開始 → 内部の sleep(0) 通過 → IS_ACTIVE=False まで進める(2回譲る)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await sensor_service.process_sensor_data("mac_motion", "テスト", "リビング", "Motion Sensor", "detected")
            await stop_task
            for t in list(sensor_service.MOTION_TASKS.values()):
                if isinstance(t, asyncio.Task):
                    t.cancel()

        assert any("止まりました" in m for m in sent)
        assert any("動きがありました" in m for m in sent)
        assert sensor_service.IS_ACTIVE["mac_motion"] is True


@pytest.mark.asyncio
class TestCacheEviction:
    """Issue #621の回帰テスト: 攻撃者が制御可能な context.deviceMac をキーにした
    4つのキャッシュ/タスク管理(EVENT_CACHE/IS_ACTIVE/LAST_NOTIFY_TIME/MOTION_TASKS)
    が上限を超えて無制限に成長しないこと。"""

    async def test_event_cache_eviction_bounds_size(self, monkeypatch):
        monkeypatch.setattr(sensor_service, "_MAC_CACHE_MAX_SIZE", 5)
        for i in range(8):
            sensor_service.is_duplicate_webhook(f"mac{i}", "open", float(i))

        assert len(sensor_service.EVENT_CACHE) == 5
        assert len(sensor_service._mac_last_seen) == 5
        # 古い順(mac0〜mac2)は削除され、直近5件だけが残る
        assert set(sensor_service.EVENT_CACHE.keys()) == {"mac3", "mac4", "mac5", "mac6", "mac7"}

    async def test_motion_state_eviction_cancels_pending_task_and_clears_all_caches(self, monkeypatch):
        """MOTION_TASKS/IS_ACTIVE/LAST_NOTIFY_TIMEも同じMAC単位で連動して削除され、
        削除されるタスクが未完了ならキャンセルされること。"""
        monkeypatch.setattr(sensor_service, "_MAC_CACHE_MAX_SIZE", 2)
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)):
            await sensor_service.process_sensor_data("mac_old", "古いセンサー", "リビング", "Motion Sensor", "detected")
            old_task = sensor_service.MOTION_TASKS["mac_old"]

            await sensor_service.process_sensor_data("mac_mid", "中間センサー", "リビング", "Motion Sensor", "detected")
            # 上限(2)を超える3件目の追加により mac_old が最も古いエントリとして追い出される
            await sensor_service.process_sensor_data("mac_new", "新しいセンサー", "リビング", "Motion Sensor", "detected")

        assert "mac_old" not in sensor_service.MOTION_TASKS
        assert "mac_old" not in sensor_service.IS_ACTIVE
        assert "mac_old" not in sensor_service._mac_last_seen

        # send_inactive_notification は CancelledError を内部で捕捉して静かに
        # returnする(既存のTestSendInactiveNotification.test_cancellation_leaves_active_state_untouched
        # と同じ挙動)ため、キャンセル要求が実際に伝播したことは正常終了で確認する。
        await old_task
        assert old_task.done()

        for t in list(sensor_service.MOTION_TASKS.values()):
            if isinstance(t, asyncio.Task):
                t.cancel()


class TestPowerHysteresis:
    """Issue #1148: power_off_threshold_watts / min_on_sec による使用開始/終了通知の抑制。"""

    T0 = 1_000_000.0

    def _run(self, readings, **settings):
        """(経過秒, W) の列を順に判定し、通知文の列を返す。前回値は直前の読み取り。"""
        msgs = []
        prev = 0.0
        for t, w in readings:
            msg = sensor_service._power_transition_with_hysteresis(
                "toilet", "1Fのトイレ", w, prev,
                settings.get("on", 30.0), settings.get("off", settings.get("on", 30.0)),
                settings.get("min_on_sec", 0), now=self.T0 + t,
            )
            if msg:
                msgs.append(msg)
            prev = w
        return msgs

    def test_short_thermostat_cycles_are_ignored_with_min_on_sec(self):
        # 47W → 0 → 47W … を5分刻みで(暖房便座のサーモスタット)
        readings = [(i * 300, 47.0 if i % 2 == 0 else 0.0) for i in range(12)]
        assert self._run(readings, min_on_sec=600) == []

    def test_long_use_notifies_on_and_off_once(self):
        readings = [(0, 0.0), (300, 47.0), (600, 47.0), (900, 47.0), (1200, 0.0), (1500, 0.0)]
        msgs = self._run(readings, min_on_sec=600)
        assert len(msgs) == 2
        assert "使用開始" in msgs[0] and "使用終了" in msgs[1]

    def test_hysteresis_keeps_on_between_thresholds(self):
        readings = [(0, 0.0), (300, 100.0), (600, 20.0), (900, 35.0), (1200, 5.0)]
        msgs = self._run(readings, on=30.0, off=10.0)
        assert len(msgs) == 2  # 20W・35W では切り替わらない
        assert "使用開始" in msgs[0] and "使用終了" in msgs[1]

    def test_restart_mid_use_does_not_double_notify(self):
        # 状態が無い(再起動直後)で、前回値が既に ON なら通知済みとみなす
        msg = sensor_service._power_transition_with_hysteresis(
            "dev", "炊飯器", 300.0, 300.0, 100.0, 100.0, 600, now=self.T0)
        assert msg is None

    async def test_unset_settings_keep_the_legacy_behaviour(self, isolated_db):
        """devices.json の model_dump は未設定キーを None で持つ。その場合は従来どおり即時に通知する。"""
        settings = {"power_threshold_watts": 30.0, "power_off_threshold_watts": None, "min_on_sec": None}
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("toilet", "1Fのトイレ", 47.0, settings)
        mock_send.assert_called_once()
        assert sensor_service.POWER_STATE == {}

    async def test_min_on_sec_suppresses_first_reading_via_process_power_data(self, isolated_db):
        settings = {"power_threshold_watts": 30.0, "min_on_sec": 600}
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.process_power_data("toilet", "1Fのトイレ", 47.0, settings)
        mock_send.assert_not_called()

    def test_notify_settings_model_keeps_the_new_keys(self):
        """config.NotifySettings が新しいキーを落とさない(落とすと設定が効かない)。"""
        dumped = config.NotifySettings(power_threshold_watts=30, power_off_threshold_watts=10, min_on_sec=600).model_dump()
        assert dumped["power_off_threshold_watts"] == 10 and dumped["min_on_sec"] == 600


class TestContactCloseLog:
    """Issue #1172: close を経過秒付きで DEBUG に1行出す。通知の回数は変えない。"""

    async def test_close_logs_open_duration_without_notification(self):
        debug = []
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send, \
             patch.object(sensor_service.logger, "debug", side_effect=lambda m, *a, **k: debug.append(m)):
            await sensor_service.process_sensor_data("mac-c", "冷蔵庫", "伊丹", "WoContact", "open")
            assert "mac-c" in sensor_service.CONTACT_OPENED_AT
            sensor_service.CONTACT_OPENED_AT["mac-c"] -= 12  # 12秒前に開いたことにする
            await sensor_service.process_sensor_data("mac-c", "冷蔵庫", "伊丹", "WoContact", "close")
        assert mock_send.call_count == 1  # open の1回だけ
        assert any("close, 開いていた時間 12秒" in m for m in debug)

    async def test_close_without_prior_open(self):
        debug = []
        with patch.object(sensor_service.logger, "debug", side_effect=lambda m, *a, **k: debug.append(m)):
            await sensor_service.process_sensor_data("mac-c", "冷蔵庫", "伊丹", "WoContact", "close")
        assert any(m.endswith("(close)") for m in debug)


class TestLongInactivity:
    """Issue #1153: 日中に3時間動きが無ければ拠点ごとに1回通知、起動時はDBからタイマーを復元。"""

    @staticmethod
    def _jst(*args):
        import datetime as _dt
        return _dt.datetime(*args, tzinfo=sensor_service.get_now_jst().tzinfo)

    @pytest.mark.parametrize(
        "last, expected",
        [
            ((2026, 11, 27, 9, 0), (2026, 11, 27, 12, 0)),
            ((2026, 11, 27, 6, 30), (2026, 11, 27, 10, 0)),   # 夜明け前は 7:00 から数える
            ((2026, 11, 27, 20, 0), (2026, 11, 28, 10, 0)),   # 22時までに3時間経たない → 翌朝
            ((2026, 11, 27, 23, 0), (2026, 11, 28, 10, 0)),
            ((2026, 11, 27, 19, 0), (2026, 11, 27, 22, 0)),   # ちょうど22時は日中のうち
        ],
    )
    def test_deadline_counts_only_daytime(self, last, expected):
        assert sensor_service.long_inactive_deadline(self._jst(*last)) == self._jst(*expected)

    async def test_notifies_once_when_deadline_passes(self, monkeypatch):
        now = self._jst(2026, 11, 27, 12, 30)
        monkeypatch.setattr(sensor_service, "get_now_jst", lambda: now)
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            sensor_service.schedule_long_inactive_check("高砂", self._jst(2026, 11, 27, 9, 0))
            await asyncio.sleep(0.05)
        mock_send.assert_called_once()
        text = mock_send.call_args.kwargs["messages"][0]["text"]
        assert "高砂" in text and "3時間以上" in text and "11/27 09:00" in text
        assert "高砂" not in sensor_service.LONG_INACTIVE_TASKS

    async def test_new_motion_cancels_the_pending_check(self, monkeypatch):
        now = self._jst(2026, 11, 27, 10, 0)
        monkeypatch.setattr(sensor_service, "get_now_jst", lambda: now)
        monkeypatch.setattr(config, "MONITOR_DEVICES", [{"id": "mac-m", "type": "Motion Sensor", "location": "高砂"}])
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)):
            sensor_service.schedule_long_inactive_check("高砂", self._jst(2026, 11, 27, 9, 0))
            first = sensor_service.LONG_INACTIVE_TASKS["高砂"]
            await sensor_service.process_sensor_data("mac-m", "人感", "高砂", "Motion Sensor", "detected")
            await asyncio.sleep(0)
        assert first.cancelled()
        assert sensor_service.LONG_INACTIVE_TASKS["高砂"] is not first

    async def test_unregistered_device_does_not_start_a_check(self, monkeypatch):
        monkeypatch.setattr(config, "MONITOR_DEVICES", [])
        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)):
            await sensor_service.process_sensor_data("mac-x", "?", "場所不明", "Motion Sensor", "detected")
        assert sensor_service.LONG_INACTIVE_TASKS == {}

    async def test_restore_from_db(self, isolated_db, monkeypatch):
        now = self._jst(2026, 11, 27, 10, 0)
        monkeypatch.setattr(sensor_service, "get_now_jst", lambda: now)
        monkeypatch.setattr(config, "MONITOR_DEVICES", [
            {"id": "m-living", "type": "Motion Sensor", "location": "伊丹", "name": "リビング"},
            {"id": "m-bed", "type": "Motion Sensor", "location": "伊丹", "name": "寝室"},
            {"id": "m-taka", "type": "Motion Sensor", "location": "高砂", "name": "居間"},
            {"id": "plug", "type": "Plug Mini (JP)", "location": "伊丹", "name": "プラグ"},
        ])
        with get_db_cursor(commit=True) as cur:
            for mac, ts, state in [
                ("m-living", "2026-11-27T09:55:00+09:00", "detected"),   # 5分前 → 15分タイマーを復元
                ("m-bed", "2026-11-27T08:00:00+09:00", "detected"),      # 2時間前 → 15分タイマーは張らない
                ("m-taka", "2026-11-27T07:30:00+09:00", "detected"),
                ("m-taka", "2026-11-27T09:59:00+09:00", "not_detected"),  # detected 以外は数えない
            ]:
                cur.execute(
                    "INSERT INTO device_records (timestamp, device_name, device_id, device_type, contact_state) "
                    "VALUES (?, 'x', ?, 'Webhook', ?)", (ts, mac, state))

        await sensor_service.restore_motion_timers()

        assert set(sensor_service.MOTION_TASKS) == {"m-living"}
        assert sensor_service.IS_ACTIVE.get("m-living") is True
        assert set(sensor_service.LONG_INACTIVE_TASKS) == {"伊丹", "高砂"}
        assert sensor_service.LAST_MOTION_AT["伊丹"] == self._jst(2026, 11, 27, 9, 55)
        assert sensor_service.LAST_MOTION_AT["高砂"] == self._jst(2026, 11, 27, 7, 30)

    async def test_restore_skips_locations_whose_deadline_already_passed(self, isolated_db, monkeypatch):
        """PR #1185 レビュー指摘: 期限が過ぎた拠点の監視を張ると、再起動のたびに即時に誤通知する。"""
        now = self._jst(2026, 11, 28, 2, 0)  # 深夜の再起動
        monkeypatch.setattr(sensor_service, "get_now_jst", lambda: now)
        monkeypatch.setattr(config, "MONITOR_DEVICES", [
            {"id": "m-old", "type": "Motion Sensor", "location": "高砂", "name": "居間"},   # 3日前が最後(電池切れ)
            {"id": "m-eve", "type": "Motion Sensor", "location": "伊丹", "name": "リビング"},  # 前日20時 → 期限は当日10時
        ])
        with get_db_cursor(commit=True) as cur:
            for mac, ts in [("m-old", "2026-11-25T09:00:00+09:00"), ("m-eve", "2026-11-27T20:00:00+09:00")]:
                cur.execute(
                    "INSERT INTO device_records (timestamp, device_name, device_id, device_type, contact_state) "
                    "VALUES (?, 'x', ?, 'Webhook', 'detected')", (ts, mac))

        with patch.object(sensor_service, "send_push", MagicMock(return_value=True)) as mock_send:
            await sensor_service.restore_motion_timers()
            await asyncio.sleep(0.05)

        mock_send.assert_not_called()
        assert set(sensor_service.LONG_INACTIVE_TASKS) == {"伊丹"}

    async def test_restore_failure_does_not_raise(self, monkeypatch):
        def _boom():
            raise RuntimeError("db")
        monkeypatch.setattr(sensor_service, "_load_last_motion_times", _boom)
        await sensor_service.restore_motion_timers()
        assert sensor_service.MOTION_TASKS == {}
