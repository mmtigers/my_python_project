# MY_HOME_SYSTEM/services/sensor_service.py
import asyncio
import time
from typing import Dict, Optional, List

import config
from core.logger import setup_logging
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
    """センサー検知メインロジック"""
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
        # 非同期で通知送信
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