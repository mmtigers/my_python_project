# MY_HOME_SYSTEM/tests/test_switchbot_hub_monitor.py
"""monitors/switchbot_hub_monitor.py(SwitchBotハブの ping 死活監視)のテスト。

実際の ping・LINE送信は行わず、`ping_host` と `send_push` を差し替えて検証する。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
import scheduler_boot
from monitors import switchbot_hub_monitor as hub


def _run_n(prev, results, start=1000.0, step=300.0):
    """evaluate_state を ok の列で順に適用し、(最終状態, 各回のイベント列) を返す。"""
    state, events = prev, []
    now = start
    for ok in results:
        state, event = hub.evaluate_state(state, ok, now)
        events.append(event)
        now += step
    return state, events


class TestEvaluateState:
    def test_healthy_hub_never_notifies(self):
        _, events = _run_n(None, [True] * 5)
        assert events == [None] * 5

    def test_notifies_down_only_after_threshold_consecutive_failures(self):
        _, events = _run_n(None, [False] * hub.FAILURE_THRESHOLD)
        assert events == [None] * (hub.FAILURE_THRESHOLD - 1) + [hub.EVENT_DOWN]

    def test_transient_failures_reset_without_recovery_notice(self):
        # しきい値未満の失敗 → 復旧: 異常を通知していないので復旧通知も出さない
        state, events = _run_n(None, [False, False, True, False, False, True])
        assert events == [None] * 6
        assert state["failures"] == 0

    def test_down_is_notified_only_once_while_failure_continues(self):
        _, events = _run_n(None, [False] * (hub.FAILURE_THRESHOLD + 3))
        assert events.count(hub.EVENT_DOWN) == 1

    def test_reminder_after_interval_then_suppressed_again(self):
        state, events = _run_n(None, [False] * hub.FAILURE_THRESHOLD)
        assert events[-1] == hub.EVENT_DOWN
        notified_at = state["last_notified"]
        # 間隔未満は再通知しない
        state, event = hub.evaluate_state(state, False, notified_at + hub.REMINDER_INTERVAL_SEC - 1)
        assert event is None
        # 間隔以上でリマインド
        state, event = hub.evaluate_state(state, False, notified_at + hub.REMINDER_INTERVAL_SEC)
        assert event == hub.EVENT_REMINDER
        # リマインド直後はまた抑制される
        _, event = hub.evaluate_state(state, False, state["last_notified"] + 1)
        assert event is None

    def test_recovery_notified_once_after_alert(self):
        state, _ = _run_n(None, [False] * hub.FAILURE_THRESHOLD)
        state, event = hub.evaluate_state(state, True, 99999.0)
        assert event == hub.EVENT_RECOVERED
        assert state == {"failures": 0, "alerting": False, "last_notified": 0.0}
        _, event = hub.evaluate_state(state, True, 100000.0)
        assert event is None

    def test_tolerates_missing_or_garbage_previous_state(self):
        state, event = hub.evaluate_state({}, False, 1.0)
        assert state["failures"] == 1 and event is None


class TestSelectHubs:
    def test_selects_only_hub_types_with_ip(self):
        devices = [
            {"id": "A", "type": "Hub Mini", "name": "伊丹ハブ", "ip": "192.168.1.20"},
            {"id": "B", "type": "Hub Mini", "name": "高砂ハブ"},          # ip なし
            {"id": "C", "type": "MeterPlus", "name": "温湿度", "ip": "192.168.1.30"},
            {"id": "D", "type": "Hub 2", "name": "ハブ2", "ip": "192.168.1.21"},
        ]
        assert [d["id"] for d in hub.select_hubs(devices)] == ["A", "D"]

    @pytest.mark.parametrize("bad_ip", ["-c 100", "not-an-ip", "192.168.1", "--help"])
    def test_rejects_invalid_ip_so_it_never_reaches_ping_argv(self, bad_ip):
        devices = [{"id": "A", "type": "Hub Mini", "name": "ハブ", "ip": bad_ip}]
        assert hub.select_hubs(devices) == []


class TestBuildMessage:
    @pytest.mark.parametrize("event", [hub.EVENT_DOWN, hub.EVENT_REMINDER, hub.EVENT_RECOVERED])
    def test_message_names_the_hub_and_location(self, event):
        msg = hub.build_message(event, "ハブミニ E4", "伊丹")
        assert "ハブミニ E4" in msg and "伊丹" in msg


class TestIsReachable:
    async def test_true_when_any_attempt_succeeds(self, monkeypatch):
        monkeypatch.setattr(hub, "PING_RETRY_INTERVAL_SEC", 0)
        ping = AsyncMock(side_effect=[{"status": "NG"}, {"status": "OK"}])
        monkeypatch.setattr(hub, "ping_host", ping)
        assert await hub.is_reachable("192.168.1.20") is True
        assert ping.await_count == 2

    async def test_false_only_when_all_attempts_fail(self, monkeypatch):
        monkeypatch.setattr(hub, "PING_RETRY_INTERVAL_SEC", 0)
        ping = AsyncMock(return_value={"status": "NG"})
        monkeypatch.setattr(hub, "ping_host", ping)
        assert await hub.is_reachable("192.168.1.20") is False
        assert ping.await_count == hub.PING_ATTEMPTS


class TestMain:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hub, "_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setattr(hub, "PING_RETRY_INTERVAL_SEC", 0)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "Cgroup")
        monkeypatch.setattr(config, "MONITOR_DEVICES", [
            {"id": "HUB1", "type": "Hub Mini", "name": "ハブミニ E4", "location": "伊丹", "ip": "192.168.1.20"},
            {"id": "HUB2", "type": "Hub Mini", "name": "高砂のハブミニ", "location": "高砂"},
        ])
        send = MagicMock(return_value=True)
        monkeypatch.setattr(hub, "send_push", send)
        ping = AsyncMock(return_value={"status": "NG"})
        monkeypatch.setattr(hub, "ping_host", ping)
        return send, ping

    async def test_no_notification_before_threshold_then_line_to_parents_group(self, env):
        send, _ = env
        for _ in range(hub.FAILURE_THRESHOLD - 1):
            await hub.main()
        send.assert_not_called()

        await hub.main()
        send.assert_called_once()
        assert send.call_args.kwargs["target"] == "line"
        assert send.call_args.kwargs["user_id"] == "Cgroup"
        assert "ハブミニ E4" in send.call_args.args[0][0]["text"]

    async def test_hub_without_ip_is_never_pinged(self, env):
        _, ping = env
        await hub.main()
        assert {c.args[0] for c in ping.await_args_list} == {"192.168.1.20"}

    async def test_failed_send_is_retried_on_next_run(self, env):
        send, _ = env
        send.return_value = False
        for _ in range(hub.FAILURE_THRESHOLD):
            await hub.main()
        assert send.call_count == 1
        # 送れていないので「通知済み」にならず、次回も通知を試みる
        await hub.main()
        assert send.call_count == 2
        send.return_value = True
        await hub.main()
        assert send.call_count == 3
        await hub.main()
        assert send.call_count == 3  # 送れた後は抑制される

    async def test_recovery_notice_after_alert(self, env):
        send, ping = env
        for _ in range(hub.FAILURE_THRESHOLD):
            await hub.main()
        send.reset_mock()
        ping.return_value = {"status": "OK", "latency": 1.0, "error": ""}
        await hub.main()
        send.assert_called_once()
        assert "また繋がる" in send.call_args.args[0][0]["text"]

    async def test_missing_group_id_does_not_mark_as_notified(self, env, monkeypatch):
        send, _ = env
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "")
        for _ in range(hub.FAILURE_THRESHOLD + 1):
            await hub.main()
        send.assert_not_called()
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "Cgroup")
        await hub.main()
        send.assert_called_once()


class TestWiring:
    def test_registered_in_scheduler_with_five_minute_interval(self):
        task = next(
            (t for t in scheduler_boot.TASKS if t["script"] == "monitors/switchbot_hub_monitor.py"),
            None,
        )
        assert task is not None, "scheduler_boot.TASKS にハブ死活監視が登録されていません"
        assert task["interval"] == 300

    def test_device_config_accepts_optional_ip(self):
        base = {"id": "x", "type": "Hub Mini", "location": "l", "name": "n"}
        assert config.DeviceConfig(**base).ip is None
        assert config.DeviceConfig(**base, ip="192.168.1.20").ip == "192.168.1.20"
