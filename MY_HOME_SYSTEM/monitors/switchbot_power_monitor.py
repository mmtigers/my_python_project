# MY_HOME_SYSTEM/monitors/switchbot_power_monitor.py
import asyncio
import sys
import os
import time
import json
from typing import Dict, Any, Optional, List

# プロジェクトルートへのパス解決
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from services import switchbot_service as sb_tool
from services import sensor_service
from core.logger import setup_logging

logger = setup_logging("device_monitor")

TARGET_DEVICE_TYPES: List[str] = [
    "Meter", "MeterPlus", "Hub 2", "WoIOSensor",
    "Plug", "Plug Mini (JP)", "Plug Mini (US)", "Strip",
    "Nature Remo E Lite"
]

# 状態変化検知用のインメモリキャッシュ
_last_device_states: Dict[str, Dict[str, Any]] = {}

def fetch_device_status_sync(device_id: str, device_type: str) -> Optional[Dict[str, Any]]:
    """SwitchBot APIからステータスを取得する（同期処理ラッパー）。"""
    try:
        status: Optional[Dict[str, Any]] = sb_tool.get_device_status(device_id)
        if not status:
            logger.warning(f"⚠️ Status unavailable for {device_id} (Type: {device_type})")
            return None
            
        if status.get("statusCode") != 100:
            logger.error(f"❌ API Error [ID:{device_id}]: {status.get('message')}")
            return None

        data: Dict[str, Any] = status.get("body", {})
        result: Dict[str, Any] = {}
        
        # 1. 電力計データの抽出
        p_val: Optional[float] = None
        candidates: List[Any] = [data.get("watt"), data.get("weight"), data.get("power")]
        for c in candidates:
            if c is not None:
                try:
                    val: float = float(c)
                    if val >= 0:
                        p_val = val
                        break
                except (ValueError, TypeError):
                    continue
        
        if p_val is not None:
            result["power"] = p_val

        # 2. 温湿度計
        if "temperature" in data or "humidity" in data:
            try:
                result["temperature"] = float(data.get("temperature", 0.0))
                result["humidity"] = float(data.get("humidity", 0.0))
            except (ValueError, TypeError):
                pass
            
        return result

    except Exception as e:
        logger.error(f"❌ Fetch Error [{device_id}]: {e}")
        return None

async def main() -> None:
    # 定常起動はDEBUGに降格
    logger.debug("🚀 --- SwitchBot Monitor Started (Fixed Architecture v2) ---")
    
    devices: List[Dict[str, Any]] = getattr(config, "MONITOR_DEVICES", [])
    processed_count: int = 0

    if not devices:
        logger.warning("⚠️ No devices found in config.MONITOR_DEVICES.")
        return

    for i, device in enumerate(devices):
        did: str = device.get("id", "")
        dname: str = device.get("name", "Unknown")
        dtype: str = device.get("type") or device.get("device_type") or "Unknown"

        if not did:
            continue

        is_target: bool = any(t in dtype for t in TARGET_DEVICE_TYPES)
        if not is_target:
            continue

        status: Optional[Dict[str, Any]] = await asyncio.to_thread(fetch_device_status_sync, did, dtype)
        
        if status:
            # 差分検知ロジック
            last_status: Optional[Dict[str, Any]] = _last_device_states.get(did)
            if last_status != status:
                logger.info(f"🔄 Device state changed: {dname} (ID: {did}) -> {status}")
                _last_device_states[did] = status
            else:
                # 変化なしはDEBUG
                logger.debug(f"✅ Device state unchanged: {dname}")

            has_data: bool = False
            
            if "power" in status:
                await sensor_service.process_power_data(
                    did, dname, status["power"], device.get("notify_settings", {})
                )
                has_data = True
            
            if "temperature" in status:
                await sensor_service.process_meter_data(
                    did, dname, status["temperature"], status["humidity"]
                )
                has_data = True
            
            if has_data:
                processed_count += 1
        else:
            pass 

        await asyncio.sleep(2)

    if processed_count == 0:
        logger.warning("⚠️ --- Monitor Completed but 0 devices were processed. Check 'type' in devices.json ---")
    else:
        logger.debug(f"🏁 --- Monitor Completed ({processed_count} devices processed) ---")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # 重要なライフサイクルイベント（ユーザーによる中断）はINFOで維持
        logger.info("Monitor interrupted by user.")
    except Exception as e:
        logger.critical(f"Critical Error: {e}", exc_info=True)