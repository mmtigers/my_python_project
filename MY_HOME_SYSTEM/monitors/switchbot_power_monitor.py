# MY_HOME_SYSTEM/monitors/switchbot_power_monitor.py
import asyncio
import sys
import os
import time
import json
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

# 監視対象とするデバイスタイプ（これ以外はスキップしてログを汚さない）
TARGET_DEVICE_TYPES = [
    "Meter", "MeterPlus", "Hub 2", "WoIOSensor",  # 温湿度計
    "Plug", "Plug Mini (JP)", "Plug Mini (US)", "Strip",  # 電源プラグ
    "Nature Remo E Lite"  # 電力計（例外的にここで扱う場合）
]

def fetch_device_status_sync(device_id: str, device_type: str) -> Optional[Dict[str, Any]]:
    """
    SwitchBot APIからステータスを取得する（同期処理ラッパー）。
    エラーハンドリングはここで行う。
    """
    try:
        status = sb_tool.get_device_status(device_id)
        if not status:
            logger.warning(f"⚠️ Status unavailable for {device_id} (Type: {device_type})")
            return None
            
        # ステータスコードのチェック
        if status.get("statusCode") != 100:
            logger.error(f"❌ API Error [ID:{device_id}]: {status.get('message')}")
            return None

        # データ本体の取得
        data = status.get("body", {})
        result = {}
        
        # 1. 電力計データの抽出 (Plug Mini / Nature Remo E Lite)
        p_val = None
        candidates = [data.get("watt"), data.get("weight"), data.get("power")]
        for c in candidates:
            if c is not None:
                try:
                    # 文字列 "on"/"off" は float変換でエラーになるのでスキップされる
                    val = float(c)
                    if val >= 0:
                        p_val = val
                        break
                except (ValueError, TypeError):
                    continue
        
        if p_val is not None:
            result["power"] = p_val

        # 2. 温湿度計 (Meter / Hub 2)
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

async def main():
    logger.info("🚀 --- SwitchBot Monitor Started (Fixed Architecture v2) ---")
    
    # config.py からデバイス定義を読み込む
    devices = config.MONITOR_DEVICES
    processed_count = 0

    if not devices:
        logger.warning("⚠️ No devices found in config.MONITOR_DEVICES.")
        return

    for i, device in enumerate(devices):
        did = device.get("id")
        dname = device.get("name", "Unknown")
        
        # 修正: キー名 "type" を優先し、念のため "device_type" も見る
        dtype = device.get("type") or device.get("device_type") or "Unknown"

        if not did:
            continue

        # 対象外のデバイスタイプはスキップ
        is_target = any(t in dtype for t in TARGET_DEVICE_TYPES)
        if not is_target:
            # logger.debug(f"⏭️ Skipping non-target device: {dname} ({dtype})")
            continue

        # APIコール
        status = await asyncio.to_thread(fetch_device_status_sync, did, dtype)
        
        if status:
            has_data = False
            # 1. 電力データの処理
            if "power" in status:
                await sensor_service.process_power_data(
                    did, dname, status["power"], device.get("notify_settings", {})
                )
                has_data = True
            
            # 2. 温湿度データの処理
            if "temperature" in status:
                await sensor_service.process_meter_data(
                    did, dname, status["temperature"], status["humidity"]
                )
                has_data = True
            
            if has_data:
                processed_count += 1
                logger.info(f"✅ Processed: {dname}")
            else:
                # ターゲットタイプだが有効なデータが取れなかった場合のみ警告
                logger.warning(f"⚠️ No valid data extracted for: {dname} (ID: {did})")
        else:
            # 取得失敗時は fetch_device_status_sync 内でログが出ている
            pass 

        # APIレートリミット対策
        await asyncio.sleep(2)

    if processed_count == 0:
        logger.warning("⚠️ --- Monitor Completed but 0 devices were processed. Check 'type' in devices.json ---")
    else:
        logger.info(f"🏁 --- Monitor Completed ({processed_count} devices processed) ---")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Monitor interrupted by user.")
    except Exception as e:
        logger.critical(f"Critical Error: {e}")