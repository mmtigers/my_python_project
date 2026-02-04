# MY_HOME_SYSTEM/monitors/switchbot_power_monitor.py
import asyncio
import sys
import os
import time
from typing import Dict, Any, Optional, List

# プロジェクトルートへのパス解決
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 自作モジュール
import config
from services import switchbot_service as sb_tool
from services import sensor_service
from core.logger import setup_logging

# ロガー設定
logger = setup_logging("device_monitor")

def fetch_device_status_sync(device_id: str, device_type: str) -> Optional[Dict[str, Any]]:
    """
    SwitchBot APIからステータスを取得する（同期処理ラッパー）。
    エラーハンドリングはここで行う。
    """
    try:
        status = sb_tool.get_device_status(device_id)
        if not status:
            logger.warning(f"Status unavailable for {device_id}")
            return None
            
        # 必要なデータを正規化して返す
        result = {}
        
        # 1. 電力計 (Plug Mini / Nature Remo E Lite)
        if "weight" in status or "electricCurrent" in status or "voltage" in status or "power" in status:
             # Plug Mini (JP) returns 'weight' field sometimes misused or specific fields
             # API仕様依存: get_device_statusの実装に依存するが、通常は辞書が返る
             # ここでは sb_tool が整形済みデータを返すと仮定、あるいは生の辞書から抽出
             p = status.get("power") or status.get("weight") or 0.0 # APIの揺らぎ対応
             result["power"] = float(p)

        # 2. 温湿度計 (Meter / Hub 2)
        if "temperature" in status or "humidity" in status:
            result["temperature"] = float(status.get("temperature", 0.0))
            result["humidity"] = float(status.get("humidity", 0.0))
            
        return result

    except Exception as e:
        logger.error(f"Fetch Error [{device_id}]: {e}")
        return None

async def main() -> None:
    """
    メインループ。全デバイスの巡回監視 (Async版)。
    """
    logger.info("🚀 --- SwitchBot Monitor Started (New Architecture) ---")
    
    monitor_devices: List[Dict[str, Any]] = config.MONITOR_DEVICES
    processed_count = 0
    
    for device in monitor_devices:
        did: str = device.get("id", "")
        dtype: str = device.get("type", "")
        dname: str = device.get("name", "Unknown")
        
        if not did or not dtype: continue

        # 同期APIコールをスレッドで実行してイベントループをブロックさせない
        status = await asyncio.to_thread(fetch_device_status_sync, did, dtype)
        
        if status:
            # 1. 電力データの処理 (Serviceへ委譲)
            if "power" in status:
                await sensor_service.process_power_data(
                    did, dname, status["power"], device.get("notify_settings", {})
                )
            
            # 2. 温湿度データの処理 (Serviceへ委譲)
            if "temperature" in status:
                await sensor_service.process_meter_data(
                    did, dname, status["temperature"], status["humidity"]
                )
            
            processed_count += 1
            logger.info(f"✅ Processed: {dname}")

        # APIレートリミット対策 (Blocking sleep -> Await sleep)
        await asyncio.sleep(5)

    logger.info(f"🏁 --- Monitor Completed ({processed_count} devices processed) ---")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Monitor interrupted by user.")
    except Exception as e:
        logger.critical(f"Unexpected Error: {e}")