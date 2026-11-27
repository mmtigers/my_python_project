# MY_HOME_SYSTEM/services/sensor_service.py
import asyncio
import datetime
import time
from typing import Dict, Optional, Any

import config
from core.database import get_db_cursor
from core.logger import setup_logging
from core.utils import get_now_iso, get_now_jst
from core.database import save_log_async
from services.notification_service import send_push

# ロガー設定
logger = setup_logging("sensor_service")

# === Global State (状態管理) ===
LAST_NOTIFY_TIME: Dict[str, float] = {}
IS_ACTIVE: Dict[str, bool] = {}
MOTION_TASKS: Dict[str, asyncio.Task] = {}

# Webhook重複排除用のインメモリーキャッシュ
EVENT_CACHE: Dict[str, Dict[str, Any]] = {}
DEDUPE_TTL_SECONDS: float = 3.0  # 3秒以内の同一ステータスは重複とみなす

# Issue #1148: 電力の使用開始/終了通知のヒステリシス用の状態(device_id ごと)。
# {"on": 今ONと判定しているか, "since": ONになった時刻(time.time()), "notified": 使用開始を通知済みか}
# notify_settings に power_off_threshold_watts / min_on_sec が無いデバイスは使わない(従来の判定)。
# 再起動で消えたときは、DB の前回値から初期化する(ONの途中なら通知済みとみなし、二重通知しない)。
POWER_STATE: Dict[str, Dict[str, Any]] = {}

# 定数
MOTION_TIMEOUT: int = 900       # 15分 (見守りタイマー)
CONTACT_COOLDOWN: int = 300     # 5分 (通知抑制)

# Issue #621: 上記4つのキャッシュ/タスク管理はすべて SwitchBot Webhook の
# context.deviceMac をキーにしており、このMACアドレスは(config.SWITCHBOT_WEBHOOK_TOKEN
# 未設定時は特に)攻撃者が任意の値を送りつけて増やせる。エントリを削除する仕組みが
# 無かったため、ユニークなMACが増えるほど4つとも無制限に成長し続けていた
# (プロセスは長時間稼働するため実質的なメモリリーク)。
# handlers/line_handler.py の _profile_cache/_SEEN_EVENT_IDS(#410, #376)と同じ
# 「サイズ上限 + 最終アクセス時刻が古いものから削除」方式を、4つとも同一MACキーで
# 連動させて適用する(4つとも同じ攻撃者制御キーで肥大化するため、共通の
# 最終アクセス時刻テーブルで一括管理する)。
_MAC_CACHE_MAX_SIZE = 500
_mac_last_seen: Dict[str, float] = {}  # mac -> 最終アクセス時刻 (4キャッシュ共通のLRU基準)

# Issue #1172: 開閉センサーが最後に open になった時刻(close のログに「開いていた時間」を付ける)。
# MAC キーなので、上の4つと同じく _evict_stale_macs の削除対象にする。
CONTACT_OPENED_AT: Dict[str, float] = {}

# Issue #1153: 長時間の無反応(見守り)。15分の通知は「最後の検知から15分後の1回」だけで、
# その後どれだけ無反応が続いても追加の通知が無く、見守りで本当に知りたい「数時間動きがない」を
# 検知できなかった。拠点(location)ごとに、日中(LONG_INACTIVE_DAY_START_HOUR〜END_HOUR、JST)の
# 間に LONG_INACTIVE_SEC 動きが無ければ1回だけ通知する。拠点単位にするのは、寝室のように
# 日中は動きが無いのが普通のセンサーで毎日通知しないため(同じ拠点のどれかで動けばリセット)。
LONG_INACTIVE_SEC: int = 3 * 3600
LONG_INACTIVE_DAY_START_HOUR: int = 7
LONG_INACTIVE_DAY_END_HOUR: int = 22
LONG_INACTIVE_TASKS: Dict[str, asyncio.Task] = {}   # location -> タスク
LAST_MOTION_AT: Dict[str, datetime.datetime] = {}   # location -> 最後の検知(JST)


def _evict_stale_macs() -> None:
    """
    `_mac_last_seen` が `_MAC_CACHE_MAX_SIZE` 件を超えている場合、最終アクセス時刻が
    古いMACから順に EVENT_CACHE/IS_ACTIVE/LAST_NOTIFY_TIME/MOTION_TASKS すべての
    エントリを削除する。

    MOTION_TASKSに未完了のタスクが残っている場合、辞書から削除するだけだと
    参照を失ったタスクが孤立したまま最大MOTION_TIMEOUT秒(15分)残留し続けるため、
    先にキャンセルしてから削除する。
    """
    overflow = len(_mac_last_seen) - _MAC_CACHE_MAX_SIZE
    if overflow <= 0:
        return
    oldest_macs = sorted(_mac_last_seen, key=lambda m: _mac_last_seen[m])[:overflow]
    for mac in oldest_macs:
        task = MOTION_TASKS.pop(mac, None)
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
        IS_ACTIVE.pop(mac, None)
        LAST_NOTIFY_TIME.pop(mac, None)
        EVENT_CACHE.pop(mac, None)
        CONTACT_OPENED_AT.pop(mac, None)
        del _mac_last_seen[mac]


def _touch_mac(mac: str) -> None:
    """指定MACの最終アクセス時刻を記録し、上限超過分を削除する(呼び出し元でロック取得は不要。
    sensor_service はFastAPIの非同期エンドポイントから直接awaitされ、他スレッドから
    並行アクセスされることが無いため、_profile_cacheのようなthreading.Lockは不要)。"""
    _mac_last_seen[mac] = time.time()
    _evict_stale_macs()

def is_duplicate_webhook(mac: str, state: str, event_timestamp: float) -> bool:
    """
    Webhookイベントの重複排除を判定する。
    
    【判定条件】
    インメモリキャッシュ（EVENT_CACHE）を参照し、以下の両方を満たす場合は重複(True)とする。
    1. 同一MACアドレスに対する直近のイベントとステータス(state)が完全に一致していること
    2. 直近のイベント処理時刻から `DEDUPE_TTL_SECONDS` 秒以内の受信であること
    """
    _touch_mac(mac)  # Issue #621: サイズ上限つきキャッシュのLRU基準を更新

    last_event: Optional[Dict[str, Any]] = EVENT_CACHE.get(mac)

    if last_event:
        time_passed: float = event_timestamp - last_event["timestamp"]
        # ステータスが同じ、かつTTL内の連続受信であれば重複として弾く
        if last_event["state"] == state and time_passed <= DEDUPE_TTL_SECONDS:
            return True
            
    # 新規イベント、状態変化、または十分な時間が経過している場合はキャッシュを更新
    EVENT_CACHE[mac] = {
        "state": state,
        "timestamp": event_timestamp
    }
    return False

# ==========================================
# 1. Webhook Logic (Passive)
# ==========================================

async def send_inactive_notification(mac: str, name: str, location: str, timeout: int) -> None:
    """無反応検知通知 (動きがない場合に通知を送る)"""
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        logger.debug(f"動きなしタイマーキャンセル: {name}")
        return

    # ここに到達した時点で実際に無反応状態になっているため、
    # 通知の送信に失敗してもIS_ACTIVE/MOTION_TASKSは必ずクリーンアップする。
    # そうしないと「動きが再開した」通知が二度と出なくなる。
    msg: str = f"💤【{location}・見守り】\n{name} の動きが止まりました（{int(timeout/60)}分経過）"
    # Issue #534: 送信(to_thread、キャンセル不能)の前に非アクティブへ落とす。以前は finally で
    # のみ False にしていたため、送信中に次の検知が届くと process_sensor_data は
    # 「まだアクティブ」とみなして再開通知を出さず、届くのは「止まりました」だけになり、
    # ユーザーには誤った停止状態が残っていた。先に False にしておけば、送信中に届いた検知は
    # 「非アクティブ→アクティブ」の遷移として「動きがありました」を送る(停止→再開の順)。
    IS_ACTIVE[mac] = False
    try:
        await asyncio.to_thread(
            send_push,
            messages=[{"type": "text", "text": msg}],
            target="discord",
            channel="notify",
        )
        # 状態の大きな変化（タイムアウト）なので INFO を維持
        logger.info(f"通知送信 [Digital Event]: {msg}")
    except Exception as e:
        logger.error(f"無反応通知の送信に失敗しました: {name} ({e})")
    finally:
        # #387: 通知送信(to_thread)中に次の検知が来ると、process_sensor_data が本タスクを
        # cancel して新しいタイマータスクを MOTION_TASKS[mac] に登録する。その後この finally が
        # 走ると、以前は無条件に IS_ACTIVE=False・del MOTION_TASKS[mac] していたため、
        # 「新タスクの参照」を消してしまい、直後の検知で二重通知、さらに参照を失った新タスクが
        # cancel 不能のまま満了して誤った「止まりました」通知を出していた。
        # 自分がまだ登録中のタスクである場合にのみ後片付けを行う。
        registered = MOTION_TASKS.get(mac)
        superseded = (
            isinstance(registered, asyncio.Task)
            and registered is not asyncio.current_task()
            and not registered.done()
        )
        if not superseded:
            IS_ACTIVE[mac] = False
            MOTION_TASKS.pop(mac, None)

async def process_sensor_data(mac: str, name: str, location: str, dev_type: str, state: str) -> None:
    """
    センサー検知メインロジック (Webhook経由)
    
    Silence Policy:
    - INFO: 状態が「非アクティブ → アクティブ」に変化した場合、またはドア開閉などの明確なイベント。
    - DEBUG: 既に「アクティブ」な状態での継続的な検知（モーションセンサーの連続反応など）。
    """
    _touch_mac(mac)  # Issue #621: サイズ上限つきキャッシュのLRU基準を更新

    msg: Optional[str] = None
    now: float = time.time()

    # Motion Sensor Logic
    # "Motion" 部分一致はデバイス一覧API語彙("Motion Sensor")向け。
    # SwitchBot公式Webhookの語彙("WoPresence")はこの部分一致に合致しないため、
    # 完全一致で追加している(#94: 以前はWoPresence形式のイベントがこの分岐に
    # 到達せず、見守り通知・無反応タイマーが発火しなかった)。
    if dev_type and ("Motion" in dev_type or dev_type == "WoPresence"):
        if state == "detected":
            if mac in MOTION_TASKS: 
                MOTION_TASKS[mac].cancel()
            
            # 非アクティブ状態からの復帰時のみ通知・INFOログを出力
            if not IS_ACTIVE.get(mac, False):
                logger.info(f"🚶 [Digital Event] Motion detected (Active): {name}")
                msg = f"👀【{location}・見守り】\n{name} で動きがありました"
                IS_ACTIVE[mac] = True
            else:
                # 継続的な検知はノイズになるためDEBUGレベルに降格
                logger.debug(f"🚶 [Analog/Continuous] Motion detected (Already Active): {name}")
            
            # 新たな「動きなし」監視タイマーをセット
            MOTION_TASKS[mac] = asyncio.create_task(
                send_inactive_notification(mac, name, location, MOTION_TIMEOUT)
            )
            # Issue #1153: 拠点の長時間無反応の監視も、最後の検知から数え直す。
            # devices.json に登録された機器だけ(未登録の MAC で「場所不明」の監視を張らない)
            if any(d.get("id") == mac for d in config.MONITOR_DEVICES):
                schedule_long_inactive_check(location, get_now_jst())
    
    # Contact Sensor Logic
    elif state in ["open", "timeoutnotclose"]:
        # 開閉は明確なデジタル状態変化のためINFO
        logger.info(f"🚪 [Digital Event] Contact sensor state: {name} ({state})")
        if state == "open":
            CONTACT_OPENED_AT[mac] = now
        if now - LAST_NOTIFY_TIME.get(mac, 0.0) > CONTACT_COOLDOWN:
            msg = f"🚪【{location}・防犯】\n{name} が開きました" if state == "open" else f"⚠️【{location}・注意】\n{name} が開けっ放しです"
            LAST_NOTIFY_TIME[mac] = now
            
    # Issue #1172: close も1行残す(以前は open だけで、「開けて閉めてまた開けた」のか「重複して届いた」
    # のかをログだけでは区別できず、監査のたびに DB を見ていた)。ノイズ削減(#1156)の方針に揃えて DEBUG。
    elif state == "close":
        opened_at = CONTACT_OPENED_AT.pop(mac, None)
        duration = f", 開いていた時間 {int(now - opened_at)}秒" if opened_at is not None else ""
        logger.debug(f"🚪 [Digital Event] Contact sensor state: {name} (close{duration})")
            
    if msg:
        await asyncio.to_thread(
            send_push,
            messages=[{"type": "text", "text": msg}],
            target="discord",
            channel="notify",
        )


def long_inactive_deadline(last_motion: datetime.datetime) -> datetime.datetime:
    """最後の検知から、日中の LONG_INACTIVE_SEC が経つ時刻(Issue #1153)。

    夜間(END_HOUR〜翌START_HOUR)は数えない。例: 20:00 が最後なら、22:00 までに3時間は経たないので、
    翌朝 7:00 から数えて 10:00。6:30 が最後なら 7:00 から数えて 10:00。
    """
    def _day_start(d: datetime.datetime) -> datetime.datetime:
        return d.replace(hour=LONG_INACTIVE_DAY_START_HOUR, minute=0, second=0, microsecond=0)

    start = last_motion
    if start.hour < LONG_INACTIVE_DAY_START_HOUR:
        start = _day_start(start)
    elif start.hour >= LONG_INACTIVE_DAY_END_HOUR:
        start = _day_start(start + datetime.timedelta(days=1))
    deadline = start + datetime.timedelta(seconds=LONG_INACTIVE_SEC)
    day_end = start.replace(hour=LONG_INACTIVE_DAY_END_HOUR, minute=0, second=0, microsecond=0)
    if deadline > day_end:
        deadline = _day_start(start + datetime.timedelta(days=1)) + datetime.timedelta(seconds=LONG_INACTIVE_SEC)
    return deadline


async def send_long_inactive_notification(location: str, last_motion: datetime.datetime) -> None:
    """拠点で日中に LONG_INACTIVE_SEC 動きが無ければ1回通知する(Issue #1153)。次の検知でキャンセルされる。"""
    deadline = long_inactive_deadline(last_motion)
    try:
        await asyncio.sleep(max(0.0, (deadline - get_now_jst()).total_seconds()))
    except asyncio.CancelledError:
        return
    hours = LONG_INACTIVE_SEC // 3600
    msg = (
        f"⚠️【{location}・見守り】\n日中に{hours}時間以上、動きがありません"
        f"(最後の検知 {last_motion.strftime('%m/%d %H:%M')})"
    )
    try:
        await asyncio.to_thread(
            send_push,
            messages=[{"type": "text", "text": msg}],
            target="discord",
            channel="notify",
        )
        logger.info(f"通知送信 [Digital Event]: {msg}")
    except Exception as e:
        logger.error(f"長時間無反応の通知の送信に失敗しました: {location} ({e})")
    finally:
        if LONG_INACTIVE_TASKS.get(location) is asyncio.current_task():
            LONG_INACTIVE_TASKS.pop(location, None)


def schedule_long_inactive_check(location: str, last_motion: datetime.datetime) -> None:
    """拠点の長時間無反応の監視を、last_motion から数え直す(既存の監視はキャンセルする)。"""
    LAST_MOTION_AT[location] = last_motion
    task = LONG_INACTIVE_TASKS.pop(location, None)
    if task is not None:
        task.cancel()
    LONG_INACTIVE_TASKS[location] = asyncio.create_task(send_long_inactive_notification(location, last_motion))


def _is_motion_device(dev_type: Optional[str]) -> bool:
    return bool(dev_type) and ("Motion" in dev_type or dev_type == "WoPresence")


def _load_last_motion_times() -> Dict[str, datetime.datetime]:
    """人感センサーごとの最後の検知時刻(JST)を device_records から読む。"""
    macs = [d["id"] for d in config.MONITOR_DEVICES if _is_motion_device(d.get("type"))]
    result: Dict[str, datetime.datetime] = {}
    if not macs:
        return result
    with get_db_cursor() as cur:
        if cur is None:
            return result
        for mac in macs:
            row = cur.execute(
                "SELECT MAX(timestamp) FROM device_records WHERE device_id = ? AND contact_state = 'detected'",
                (mac,),
            ).fetchone()
            value = row[0] if row else None
            if not value:
                continue
            try:
                ts = datetime.datetime.fromisoformat(str(value))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=get_now_jst().tzinfo)
            result[mac] = ts
    return result


async def restore_motion_timers() -> None:
    """起動時に、DB 上の最後の検知時刻から見守りのタイマーを復元する(Issue #1153)。

    以前はタイマーがメモリ上にしかなく、再起動のたびに消えたため、再起動の直後に動きが
    止まっても15分の通知が出ず、長時間の無反応も数えられなかった。
    - 最後の検知から MOTION_TIMEOUT 以内のセンサーは「アクティブ」に戻し、残り時間で15分のタイマーを張る
      (それより古ければ、15分の通知は停止中に過ぎたものとして張らない)
    - 拠点ごとに、最後の検知(拠点内の最新)から長時間無反応の監視を張る
    失敗しても起動は止めない。
    """
    try:
        last_times = await asyncio.to_thread(_load_last_motion_times)
    except Exception as e:
        logger.warning(f"見守りタイマーの復元に失敗しました: {e}")
        return
    now = get_now_jst()
    by_location: Dict[str, datetime.datetime] = {}
    restored = 0
    for device in config.MONITOR_DEVICES:
        mac = device.get("id")
        last = last_times.get(mac)
        if last is None:
            continue
        location = device.get("location", "未登録")
        name = device.get("name") or mac
        if location not in by_location or last > by_location[location]:
            by_location[location] = last
        remaining = MOTION_TIMEOUT - (now - last).total_seconds()
        if remaining > 0 and mac not in MOTION_TASKS:
            IS_ACTIVE[mac] = True
            MOTION_TASKS[mac] = asyncio.create_task(send_inactive_notification(mac, name, location, int(remaining)))
            restored += 1
    for location, last in by_location.items():
        if location not in LONG_INACTIVE_TASKS:
            schedule_long_inactive_check(location, last)
    logger.info(f"見守りタイマーを復元しました (15分タイマー {restored}件 / 長時間無反応の監視 {len(by_location)}拠点)")


def cancel_all_tasks() -> None:
    """シャットダウン時のタスククリーンアップ"""
    for t in MOTION_TASKS.values():
        t.cancel()
    for t in LONG_INACTIVE_TASKS.values():
        t.cancel()
    LONG_INACTIVE_TASKS.clear()
    logger.info("All motion sensor tasks cancelled.")

# ==========================================
# 2. Polling Logic (Active) - New!
# ==========================================

async def process_meter_data(device_id: str, device_name: str, temp: float, humidity: float) -> None:
    """
    温湿度計データの保存
    
    Silence Policy:
    - DEBUG: 温湿度のアナログ値保存は定常処理のため、ログノイズ防止として DEBUG に限定。
    """
    # Issue #740 (AUDIT-011): save_log_async は Fail-Soft で False を返すため、
    # 戻り値を破棄すると「保存されていないのに DEBUG で saved と出る」状態になる。
    # ここはポーリング経路(scheduler 駆動)で再送の概念が無く、次回ポーリングで
    # 新しい値が取れるため処理は止めない。どのデバイスの分が欠けたかを特定できる
    # よう ERROR で記録する(save_log_generic 側のログにはデバイス名が入らない)。
    save_ok = await save_log_async(
        config.SQLITE_TABLE_SWITCHBOT_LOGS,
        ["device_id", "device_name", "temperature", "humidity", "timestamp"],
        (device_id, device_name, temp, humidity, get_now_iso())
    )
    if not save_ok:
        logger.error(f"🌡️ 温湿度データの保存に失敗しました: {device_name} (id={device_id})")
        return
    logger.debug(f"🌡️ [Analog] Meter data saved: {device_name} (Temp: {temp}℃, Hum: {humidity}%)")

def _power_transition_with_hysteresis(
    device_id: str,
    device_name: str,
    wattage: float,
    prev_wattage: float,
    on_threshold: float,
    off_threshold: float,
    min_on_sec: int,
    now: Optional[float] = None,
) -> Optional[str]:
    """ヒステリシスと最短ON時間を考慮して、使用開始/終了の通知文を返す(Issue #1148)。

    1Fのトイレで、暖房便座のサーモスタットが入り切りする約47Wを5分ごとのポーリングで拾い、
    1日に約105件の通知が出ていた(単一の閾値をまたいだかだけで判定していたため)。
    - ON中は off_threshold を下回るまで ON とみなす(閾値付近の揺れで入り切りしない)
    - ONが min_on_sec 以上続いた時点で「使用開始」を1回だけ通知する(短いONは無視)
    - 「使用終了」は「使用開始」を通知した場合だけ通知する
    """
    now = time.time() if now is None else now
    state = POWER_STATE.get(device_id)
    if state is None:
        was_on = prev_wattage >= on_threshold
        state = {"on": was_on, "since": now, "notified": was_on}
        POWER_STATE[device_id] = state

    is_on = wattage >= (off_threshold if state["on"] else on_threshold)
    msg: Optional[str] = None
    if is_on:
        if not state["on"]:
            state.update(on=True, since=now, notified=False)
        if not state["notified"] and now - state["since"] >= min_on_sec:
            state["notified"] = True
            msg = f"💡【使用開始】\n{device_name} がONになりました ({wattage}W)"
    else:
        if state["on"] and state["notified"]:
            msg = f"🌑【使用終了】\n{device_name} がOFFになりました"
        elif state["on"]:
            logger.debug(f"⚡ {device_name}: ONが{int(now - state['since'])}秒で終わったため通知しません (min_on_sec={min_on_sec})")
        state.update(on=False, notified=False)
    return msg


async def process_power_data(
    device_id: str,
    device_name: str,
    wattage: float,
    notify_settings: Dict[str, Any],
    device_category: str = "plug",
) -> None:
    """
    電力データの保存と通知判定
    - 前回のDB値を参照して、閾値をまたいだ場合のみ通知する (Stateful Check)

    `device_category`: "smart_meter"(住宅全体のスマートメーター) か "plug"(個別家電の
    プラグ計測)かを呼び出し元が明示する。以前は device_name に "Remo" という文字列が
    含まれるかどうかで下流(analysis_service)が推測していたが、Nature Remoアプリで
    設定するニックネーム次第で判定が外れ、電気代集計が恒常的に0件になっていた
    (device_nameは表示用の自由文字列であり、種別の判定に使うべきではない)。
    呼び出し元(nature_remo_monitor.py はスマートメーターであることを既に知っている)
    で明示させることで、この一致問題を書き込み時点で解消する。既定値 "plug" は
    switchbot_power_monitor.py(SwitchBotプラグ)が明示せずに呼ぶための後方互換。

    Silence Policy:
    - INFO: 閾値を跨ぐ（ON/OFF）状態の切り替わりが発生した場合。
    - DEBUG: 平常時の電力値（アナログ値）の保存処理。
    """
    # 1. 保存前の最新値を取得（前回値）
    prev_wattage: float = 0.0
    try:
        def _fetch_prev_wattage() -> float:
            with get_db_cursor() as cur:
                row = cur.execute(
                    f"SELECT wattage FROM {config.SQLITE_TABLE_POWER_USAGE} WHERE device_id = ? ORDER BY timestamp DESC LIMIT 1",
                    (device_id,)
                ).fetchone()
                if row:
                    try:
                        return float(row['wattage'])
                    except (TypeError, IndexError, KeyError):
                        return float(row[0])
                return 0.0

        prev_wattage = await asyncio.to_thread(_fetch_prev_wattage)
        
    except Exception as e:
        logger.debug(f"Prev power fetch skipped for {device_name}: {e}")

    # 2. データを保存
    # Issue #740 (AUDIT-011): process_meter_data と同じ理由で戻り値を確認する。
    # ただしこちらは保存後に「閾値をまたいだか」の通知判定が続く。判定に使うのは
    # 上で取得済みの prev_wattage と引数の wattage であり保存結果に依存しないため、
    # 保存が失敗しても通知判定までは続ける(電気の付けっぱなし通知が、DB の一時的な
    # ロックで落ちるほうが実害が大きい)。
    save_ok = await save_log_async(
        config.SQLITE_TABLE_POWER_USAGE,
        ["device_id", "device_name", "wattage", "timestamp", "device_category"],
        (device_id, device_name, wattage, get_now_iso(), device_category)
    )
    if not save_ok:
        logger.error(f"⚡ 電力データの保存に失敗しました: {device_name} (id={device_id}, {wattage}W)")
    else:
        logger.debug(f"⚡ [Analog] Power data saved: {device_name} ({wattage}W)")
    
    # 3. 通知判定 (閾値クロス検知)
    threshold: Optional[float] = notify_settings.get("power_threshold_watts")
    if threshold is None:
        return

    msg: Optional[str] = None
    target_platform: str = notify_settings.get("target", "discord")
    off_threshold: Optional[float] = notify_settings.get("power_off_threshold_watts")
    min_on_sec: Optional[int] = notify_settings.get("min_on_sec")

    if off_threshold is not None or min_on_sec:
        # Issue #1148: ヒステリシス・最短ON時間つきの判定
        msg = _power_transition_with_hysteresis(
            device_id, device_name, wattage, prev_wattage, threshold,
            off_threshold if off_threshold is not None else threshold, min_on_sec or 0,
        )

    # OFF -> ON
    elif prev_wattage < threshold and wattage >= threshold:
        msg = f"💡【使用開始】\n{device_name} がONになりました ({wattage}W)"
        
    # ON -> OFF
    elif prev_wattage >= threshold and wattage < threshold:
        msg = f"🌑【使用終了】\n{device_name} がOFFになりました"

    if msg:
        # 閾値超えは明確な状態変化のため INFO
        logger.info(f"🔔 [Digital Event] Power state threshold crossed: {msg}")
        await asyncio.to_thread(
            send_push,
            messages=[{"type": "text", "text": msg}],
            target=target_platform,
            channel="notify",
        )