# MY_HOME_SYSTEM/monitors/switchbot_hub_monitor.py
"""SwitchBotハブ(Hub Mini 等)の死活監視。

ハブが Wi-Fi から切断された・電源が落ちた場合に、家族(LINE親グループ)へ知らせる。
従来はハブを一切見ておらず(`switchbot_power_monitor.TARGET_DEVICE_TYPES` の
"Hub 2" は "Hub Mini" に部分一致しないため取得対象外)、ハブが止まっても
配下のセンサー(開閉・人感・温湿度)が無言で止まるだけで誰にも気づかれなかった。

方式: ローカルから ICMP ping(`network_logger.ping_host` を流用)。
  - 対象は `devices.json` の monitor_devices のうち type に "Hub" を含み、
    かつ `ip` が設定されているもの。`ip` が無いハブは対象外。
    ラズパイと別LAN(例: 高砂)のハブは、ここからは ping が届かないため対象にできない。
  - SwitchBot クラウド側の断(Wi-Fiは生きているがクラウドに繋がらない)は検知しない。
    ハブ停止時のAPI応答を実機で確認できていないため、未実装(推測で作らない)。

判定: 5分間隔(scheduler_boot.TASKS)で実行し、1回の実行内で最大
PING_ATTEMPTS 回 ping して全滅なら「失敗1回」と数える。FAILURE_THRESHOLD 回
連続で失敗したら異常とみなして通知する。異常が続く間は REMINDER_INTERVAL_SEC
ごとに再通知し、復旧したら復旧通知を送る(異常を通知していない一時的な失敗では
復旧通知は送らない)。状態は core/state_file.py の JSON に保存する
(本スクリプトは実行のたびに新しいプロセスとして起動されるため)。

通知は LINE の親グループ(`config.LINE_PARENTS_GROUP_ID`)のみ。`logger.error` は
core.logger により Discord へ自動送信されるため、通常の検知ログは warning に留める。
"""
import asyncio
import ipaddress
import os
import sys
import time
from typing import Any

# プロジェクトルートへのパス解決
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core import state_file
from core.logger import setup_logging
from services.notification_service import send_push

from monitors.network_logger import ping_host

logger = setup_logging("hub_monitor")

HUB_TYPE_KEYWORD: str = "Hub"       # "Hub Mini" / "Hub 2" / "Hub 3" を一括で対象にする
PING_ATTEMPTS: int = 3              # 1回の実行内の ping 試行回数(全滅で「失敗1回」)
PING_RETRY_INTERVAL_SEC: float = 2.0
FAILURE_THRESHOLD: int = 3          # 連続失敗がこの回数に達したら異常(5分間隔なら約15分)
REMINDER_INTERVAL_SEC: int = 6 * 3600   # server_watchdog のリマインダーと同じ間隔
_STATE_FILE: str = os.path.join(config.BASE_DIR, "switchbot_hub_monitor_state.json")

# 状態ファイル内で「ip 未設定の対象外ハブのうち、既にログへ出したもの」のIDを持つ予約キー
# (Issue #1037)。ハブ別の状態は hub id をキーにするため、衝突しないよう先頭を `_` にする。
_UNMONITORED_KEY: str = "_unmonitored"

EVENT_DOWN = "down"
EVENT_REMINDER = "reminder"
EVENT_RECOVERED = "recovered"


def evaluate_state(
    prev: dict[str, Any] | None, ok: bool, now: float
) -> tuple[dict[str, Any], str | None]:
    """前回状態と今回の ping 結果から、新しい状態と通知イベントを返す(副作用なし)。

    状態: {"failures": 連続失敗回数, "alerting": 異常通知済みか, "last_notified": 最終通知UNIX時刻}
    イベント: EVENT_DOWN / EVENT_REMINDER / EVENT_RECOVERED / None(通知なし)
    """
    state = {
        "failures": int((prev or {}).get("failures", 0)),
        "alerting": bool((prev or {}).get("alerting", False)),
        "last_notified": float((prev or {}).get("last_notified", 0.0)),
    }

    if ok:
        event = EVENT_RECOVERED if state["alerting"] else None
        return {"failures": 0, "alerting": False, "last_notified": 0.0}, event

    state["failures"] += 1
    if not state["alerting"]:
        if state["failures"] >= FAILURE_THRESHOLD:
            state["alerting"] = True
            state["last_notified"] = now
            return state, EVENT_DOWN
        return state, None

    if now - state["last_notified"] >= REMINDER_INTERVAL_SEC:
        state["last_notified"] = now
        return state, EVENT_REMINDER
    return state, None


def build_message(event: str, name: str, location: str) -> str:
    """家族向けの文面(server_watchdog の文体に合わせる)。"""
    label = f"{location}の「{name}」" if location else f"「{name}」"
    if event == EVENT_RECOVERED:
        return f"📡 {label}に、また繋がるようになりました✨\nもう大丈夫ですよ😊"
    if event == EVENT_REMINDER:
        return (
            f"📡 {label}に、まだ繋がらないようです😢\n"
            "お時間ある時に、電源とWi-Fiを確認お願いします💦"
        )
    return (
        f"📡 {label}に繋がらなくなりました💦\n"
        "Wi-Fiが切れているか、電源が入っていないかもしれません。\n"
        "コンセントとWi-Fiを確認してみてくださいね🙇\n"
        "(自動監視システムより)"
    )


def select_hubs(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """監視対象のハブ(type に "Hub" を含み、有効な ip を持つもの)を返す。"""
    hubs: list[dict[str, Any]] = []
    for device in devices:
        dtype = str(device.get("type") or "")
        if HUB_TYPE_KEYWORD not in dtype:
            continue
        ip = device.get("ip")
        if not ip:
            # ip 未設定のハブは対象外。ログは main の _log_unmonitored_once が出す
            # (5分ごとに別プロセスで起動するため、ここで毎回出すと同じ行が延々と並ぶ。#1037)
            continue
        try:
            # ping の引数にそのまま渡すため、IPアドレス以外(オプション文字列等)は弾く
            ipaddress.ip_address(str(ip))
        except ValueError:
            logger.error(f"ハブ「{device.get('name')}」の ip が不正です: {ip!r}(監視をスキップします)")
            continue
        hubs.append(device)
    return hubs


def find_unmonitored_hubs(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """type に "Hub" を含むが ip が未設定で、死活監視の対象外になっているハブを返す。"""
    return [
        d for d in devices
        if HUB_TYPE_KEYWORD in str(d.get("type") or "") and not d.get("ip")
    ]


def _log_unmonitored_once(
    unmonitored: list[dict[str, Any]], monitored_count: int, states: dict[str, Any]
) -> bool:
    """ip 未設定の対象外ハブを、新しく対象外になったものだけログに出す(Issue #1037)。

    本スクリプトは5分ごとに新しいプロセスとして起動されるため、プロセス内の変数では
    「出力済み」を覚えられない。以前は毎回INFOを出して home_system.log の約27%を占めていた。
    出力済みのIDは状態ファイル(`states[_UNMONITORED_KEY]`)に残し、新規分だけ出す。
    監視中のハブが1台も無いまま新たに対象外が見つかったときは、止まっても検知されない
    ことに気づけるよう WARNING を1回だけ出す。

    Returns:
        状態(`states[_UNMONITORED_KEY]`)を更新した場合 True(呼び出し側が保存する)。
    """
    prev = states.get(_UNMONITORED_KEY)
    prev_ids = [str(i) for i in prev] if isinstance(prev, list) else []
    current = {str(d.get("id")): d for d in unmonitored}

    new_ids = sorted(set(current) - set(prev_ids))
    for hub_id in new_ids:
        logger.info(
            f"ハブ「{current[hub_id].get('name')}」は ip 未設定のため死活監視の対象外です"
            "(devices.json に ip を追加すると監視されます)"
        )
    if new_ids and monitored_count == 0:
        logger.warning(
            "死活監視中のハブが1台もありません(type=Hub のすべてが ip 未設定)。"
            "ハブが止まっても検知されません"
        )

    current_ids = sorted(current)
    if current_ids == sorted(prev_ids):
        return False
    if current_ids:
        states[_UNMONITORED_KEY] = current_ids
    else:
        states.pop(_UNMONITORED_KEY, None)
    return True


async def is_reachable(ip: str) -> bool:
    """最大 PING_ATTEMPTS 回 ping し、1回でも応答があれば True。"""
    for attempt in range(PING_ATTEMPTS):
        result = await ping_host(ip)
        if result["status"] == "OK":
            return True
        if attempt < PING_ATTEMPTS - 1:
            await asyncio.sleep(PING_RETRY_INTERVAL_SEC)
    return False


def _notify(event: str, name: str, location: str) -> bool:
    """親グループへ LINE 通知する。送れたら True。"""
    group_id = getattr(config, "LINE_PARENTS_GROUP_ID", "")
    if not group_id:
        logger.warning("LINE_PARENTS_GROUP_ID が未設定のため、ハブの異常を通知できません")
        return False
    return send_push(
        [{"type": "text", "text": build_message(event, name, location)}],
        target="line",
        user_id=group_id,
    )


async def main() -> None:
    devices = getattr(config, "MONITOR_DEVICES", [])
    hubs = select_hubs(devices)

    states = state_file.read_json(_STATE_FILE, default={})
    if not isinstance(states, dict):
        states = {}

    unmonitored_changed = _log_unmonitored_once(
        find_unmonitored_hubs(devices), len(hubs), states
    )
    if not hubs:
        logger.debug("死活監視の対象ハブがありません(type=Hub かつ ip 設定済みのもの)")
        if unmonitored_changed:
            # 出力済みのIDを覚えておかないと、次回また同じログを出してしまう
            state_file.write_json_atomic(_STATE_FILE, states)
        return

    # ハブごとの ping は互いに独立なので並列に行う(全滅待ちの間に他のハブを待たせない)
    results = await asyncio.gather(*(is_reachable(str(h["ip"])) for h in hubs))

    now = time.time()
    for hub, ok in zip(hubs, results):
        did = str(hub["id"])
        name = str(hub.get("name", did))
        location = str(hub.get("location", ""))
        prev = states.get(did)
        new_state, event = evaluate_state(prev if isinstance(prev, dict) else None, ok, now)

        if event:
            logger.warning(f"ハブの状態が変化しました: {name} ({hub['ip']}) event={event}")
            if not _notify(event, name, location):
                # 通知できなかった場合は「通知済み」にしない(次回の実行で再試行する)
                old = prev if isinstance(prev, dict) else {}
                new_state["alerting"] = bool(old.get("alerting", False))
                new_state["last_notified"] = float(old.get("last_notified", 0.0))
        elif not ok:
            logger.debug(f"ハブに繋がりません: {name} ({hub['ip']}) 連続失敗={new_state['failures']}")

        states[did] = new_state

    # 監視対象から外れたハブの状態は残さない
    active_ids = {str(h["id"]) for h in hubs} | {_UNMONITORED_KEY}
    states = {k: v for k, v in states.items() if k in active_ids}
    state_file.write_json_atomic(_STATE_FILE, states)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Hub monitor interrupted by user.")
    except Exception as e:
        logger.critical(f"Critical Error: {e}", exc_info=True)
        sys.exit(1)
