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

# 定数
MOTION_TIMEOUT: int = 900       # 15分 (見守りタイマー)
CONTACT_COOLDOWN: int = 300     # 5分 (通知抑制)

# ==========================================
# 1. Webhook Logic (Passive)
# ==========================================

async def send_inactive_notification(mac: str, name: str, location: str, timeout: int) -> None:
    """無反応検知通知 (動きがない場合に通知を送る)"""
    try:
        await asyncio.sleep(timeout)
        msg = f"💤【{location}・見守り】\n{name} の動きが止まりました（{int(timeout/60)}分経過）"
        
        await asyncio.to_thread(
            send_push,
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            None, "discord", "notify"
        )
        logger.info(f"通知送信: {msg}")
        IS_ACTIVE[mac] = False
        if mac in MOTION_TASKS:
            del MOTION_TASKS[mac]
            
    except asyncio.CancelledError:
        logger.debug(f"動きなしタイマーキャンセル: {name}")

async def process_sensor_data(mac: str, name: str, location: str, dev_type: str, state: str) -> None:
    """センサー検知メインロジック (Webhook経由)"""
    msg: Optional[str] = None
    now = time.time()
    
    # Motion Sensor Logic
    if dev_type and "Motion" in dev_type:
        if state == "detected":
            # 既存のタイマーがあればキャンセル（動きがあったため）
            if mac in MOTION_TASKS: 
                MOTION_TASKS[mac].cancel()
            
            # 非アクティブ状態からの復帰時に通知
            if not IS_ACTIVE.get(mac, False):
                msg = f"👀【{location}・見守り】\n{name} で動きがありました"
                IS_ACTIVE[mac] = True
            
            # 新たな「動きなし」監視タイマーをセット
            MOTION_TASKS[mac] = asyncio.create_task(
                send_inactive_notification(mac, name, location, MOTION_TIMEOUT)
            )
    
    # Contact Sensor Logic
    elif state in ["open", "timeoutnotclose"]:
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

def cancel_all_tasks():
    """シャットダウン時のタスククリーンアップ"""
    for t in MOTION_TASKS.values():
        t.cancel()
    logger.info("All motion sensor tasks cancelled.")




# ==========================================
# 2. Polling Logic (Active) - New!
# ==========================================

async def process_meter_data(device_id: str, device_name: str, temp: float, humidity: float) -> None:
    """温湿度計データの保存"""
    await save_log_async(
        config.SQLITE_TABLE_SWITCHBOT_LOGS,
        ["device_id", "device_name", "temperature", "humidity", "timestamp"],
        (device_id, device_name, temp, humidity, get_now_iso())
    )
    # 必要であればここで熱中症アラートなどのロジックを追加可能

async def process_power_data(device_id: str, device_name: str, wattage: float, notify_settings: Dict[str, Any]) -> None:
    """
    電力データの保存と通知判定
    - 前回のDB値を参照して、閾値をまたいだ場合のみ通知する (Stateful Check)
    """
    # 1. 保存前の最新値を取得（前回値）
    prev_wattage = 0.0
    try:
        def _fetch_prev_wattage():
            # common.execute_read_query ではなく get_db_cursor を直接使用する
            with common.get_db_cursor() as cur:
                row = cur.execute(
                    f"SELECT wattage FROM {config.SQLITE_TABLE_POWER_USAGE} WHERE device_id = ? ORDER BY timestamp DESC LIMIT 1",
                    (device_id,)
                ).fetchone()
                # RowFactoryが有効なら辞書ライク、そうでなければタプル(index 0)
                if row:
                    try:
                        return float(row['wattage'])
                    except (TypeError, IndexError, KeyError):
                        return float(row[0])
                return 0.0

        prev_wattage = await asyncio.to_thread(_fetch_prev_wattage)
        
    except Exception as e:
        # ログレベルを warning から debug に下げておく（初回起動時などはデータがないため）
        logger.debug(f"Prev power fetch skipped for {device_name}: {e}")

    # 2. データを保存
    await save_log_async(
        config.SQLITE_TABLE_POWER_USAGE,
        ["device_id", "device_name", "wattage", "timestamp"],
        (device_id, device_name, wattage, get_now_iso())
    )
    
    # 3. 通知判定 (閾値クロス検知)
    threshold = notify_settings.get("threshold")
    if threshold is None:
        return

    msg = None
    target_platform = notify_settings.get("target", "discord")
    
    # OFF -> ON
    if prev_wattage < threshold and wattage >= threshold:
        msg = f"💡【使用開始】\n{device_name} がONになりました ({wattage}W)"
        
    # ON -> OFF
    elif prev_wattage >= threshold and wattage < threshold:
        msg = f"🌑【使用終了】\n{device_name} がOFFになりました"

    if msg:
        logger.info(f"Power Notification Triggered: {msg}")
        await asyncio.to_thread(
            send_push,
            config.LINE_USER_ID,
            [{"type": "text", "text": msg}],
            None, target_platform, "notify"
        )