# MY_HOME_SYSTEM/services/sensor_service.py
import asyncio
import time
from typing import Dict, Optional, List, Any

import config
import common
from core.logger import setup_logging
from core.utils import get_now_iso
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

# 定数
MOTION_TIMEOUT: int = 900       # 15分 (見守りタイマー)
CONTACT_COOLDOWN: int = 300     # 5分 (通知抑制)

def is_duplicate_webhook(mac: str, state: str, event_timestamp: float) -> bool:
    """
    Webhookイベントの重複排除を判定する。
    
    【判定条件】
    インメモリキャッシュ（EVENT_CACHE）を参照し、以下の両方を満たす場合は重複(True)とする。
    1. 同一MACアドレスに対する直近のイベントとステータス(state)が完全に一致していること
    2. 直近のイベント処理時刻から `DEDUPE_TTL_SECONDS` 秒以内の受信であること
    """
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
        msg: str = f"💤【{location}・見守り】\n{name} の動きが止まりました（{int(timeout/60)}分経過）"
        
        await asyncio.to_thread(
            send_push,
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            None, "discord", "notify"
        )
        # 状態の大きな変化（タイムアウト）なので INFO を維持
        logger.info(f"通知送信 [Digital Event]: {msg}")
        IS_ACTIVE[mac] = False
        if mac in MOTION_TASKS:
            del MOTION_TASKS[mac]
            
    except asyncio.CancelledError:
        logger.debug(f"動きなしタイマーキャンセル: {name}")

async def process_sensor_data(mac: str, name: str, location: str, dev_type: str, state: str) -> None:
    """
    センサー検知メインロジック (Webhook経由)
    
    Silence Policy:
    - INFO: 状態が「非アクティブ → アクティブ」に変化した場合、またはドア開閉などの明確なイベント。
    - DEBUG: 既に「アクティブ」な状態での継続的な検知（モーションセンサーの連続反応など）。
    """
    msg: Optional[str] = None
    now: float = time.time()
    
    # Motion Sensor Logic
    if dev_type and "Motion" in dev_type:
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
    
    # Contact Sensor Logic
    elif state in ["open", "timeoutnotclose"]:
        # 開閉は明確なデジタル状態変化のためINFO
        logger.info(f"🚪 [Digital Event] Contact sensor state: {name} ({state})")
        if now - LAST_NOTIFY_TIME.get(mac, 0.0) > CONTACT_COOLDOWN:
            msg = f"🚪【{location}・防犯】\n{name} が開きました" if state == "open" else f"⚠️【{location}・注意】\n{name} が開けっ放しです"
            LAST_NOTIFY_TIME[mac] = now
            
    if msg:
        await asyncio.to_thread(
            send_push, 
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            None, "discord", "notify"
        )

def cancel_all_tasks() -> None:
    """シャットダウン時のタスククリーンアップ"""
    for t in MOTION_TASKS.values():
        t.cancel()
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
    await save_log_async(
        config.SQLITE_TABLE_SWITCHBOT_LOGS,
        ["device_id", "device_name", "temperature", "humidity", "timestamp"],
        (device_id, device_name, temp, humidity, get_now_iso())
    )
    logger.debug(f"🌡️ [Analog] Meter data saved: {device_name} (Temp: {temp}℃, Hum: {humidity}%)")

async def process_power_data(device_id: str, device_name: str, wattage: float, notify_settings: Dict[str, Any]) -> None:
    """
    電力データの保存と通知判定
    - 前回のDB値を参照して、閾値をまたいだ場合のみ通知する (Stateful Check)
    
    Silence Policy:
    - INFO: 閾値を跨ぐ（ON/OFF）状態の切り替わりが発生した場合。
    - DEBUG: 平常時の電力値（アナログ値）の保存処理。
    """
    # 1. 保存前の最新値を取得（前回値）
    prev_wattage: float = 0.0
    try:
        def _fetch_prev_wattage() -> float:
            with common.get_db_cursor() as cur:
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
    await save_log_async(
        config.SQLITE_TABLE_POWER_USAGE,
        ["device_id", "device_name", "wattage", "timestamp"],
        (device_id, device_name, wattage, get_now_iso())
    )
    logger.debug(f"⚡ [Analog] Power data saved: {device_name} ({wattage}W)")
    
    # 3. 通知判定 (閾値クロス検知)
    threshold: Optional[float] = notify_settings.get("threshold")
    if threshold is None:
        return

    msg: Optional[str] = None
    target_platform: str = notify_settings.get("target", "discord")
    
    # OFF -> ON
    if prev_wattage < threshold and wattage >= threshold:
        msg = f"💡【使用開始】\n{device_name} がONになりました ({wattage}W)"
        
    # ON -> OFF
    elif prev_wattage >= threshold and wattage < threshold:
        msg = f"🌑【使用終了】\n{device_name} がOFFになりました"

    if msg:
        # 閾値超えは明確な状態変化のため INFO
        logger.info(f"🔔 [Digital Event] Power state threshold crossed: {msg}")
        await asyncio.to_thread(
            send_push,
            config.LINE_USER_ID,
            [{"type": "text", "text": msg}],
            None, target_platform, "notify"
        )