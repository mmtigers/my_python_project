# MY_HOME_SYSTEM/monitors/switchbot_power_monitor.py
import asyncio
import sys
import os
import time
import json
from typing import Dict, Any, Optional, List, Set

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
        
        # 1. 電力計データ（アナログ値）の抽出
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

        # 2. 温湿度計（アナログ値）の抽出
        if "temperature" in data or "humidity" in data:
            try:
                result["temperature"] = float(data.get("temperature", 0.0))
                result["humidity"] = float(data.get("humidity", 0.0))
            except (ValueError, TypeError):
                pass
                
        # 3. デジタル状態（ON/OFFなど）の抽出
        # 'power' が文字列 "ON"/"OFF" の場合、または 'powerState' が存在する場合に取得
        raw_power: Any = data.get("power")
        if isinstance(raw_power, str) and raw_power.upper() in ["ON", "OFF"]:
            result["power_state"] = raw_power.upper()
        elif "powerState" in data:
            result["power_state"] = str(data.get("powerState")).upper()
            
        return result

    except Exception as e:
        logger.error(f"❌ Fetch Error [{device_id}]: {e}")
        return None

def log_device_state_change(
    dname: str, 
    did: str, 
    last_status: Optional[Dict[str, Any]], 
    current_status: Dict[str, Any]
) -> None:
    """
    デバイスの状態変化を評価し、基本設計書 6.1 の Silence Policy に基づき適切なログレベルで出力する。

    基準:
    - INFO: 'power_state' などのデジタルな状態変化（ON/OFFの切り替わり等）が発生した場合。
            重要なイベントとしてシステムが把握すべき操作や状態変化に限定する。
    - DEBUG: 'power' (消費電力), 'temperature', 'humidity' などのアナログ値の微小な変動のみの場合、
             または状態に変化がない場合。ログファイルへのノイズを防ぐため降格させる。
    """
    if last_status == current_status:
        logger.debug(f"✅ Device state unchanged: {dname}")
        return
        
    if last_status is None:
        # 初回取得時は起動時のログフラッド（ノイズ）を防ぐため DEBUG レベルとする
        logger.debug(f"🔄 Initial device state: {dname} (ID: {did}) -> {current_status}")
        return

    # 差分のあるキーを抽出
    changed_keys: List[str] = [
        key for key, value in current_status.items()
        if last_status.get(key) != value
    ]

    # デジタルな状態変化とみなすキーの定義（必要に応じて "online" などを追加可能）
    digital_state_keys: Set[str] = {"power_state", "status", "online"}
    
    # デジタル状態の変化が含まれているか判定
    has_digital_change: bool = any(key in digital_state_keys for key in changed_keys)

    if has_digital_change:
        # デジタルな変化（ON->OFF等）が含まれる場合は INFO
        logger.info(f"🔄 Device state changed [Digital]: {dname} (ID: {did}) -> {current_status}")
    else:
        # アナログな変化（温度 24.8 -> 24.9 等）のみの場合は DEBUG
        logger.debug(f"🔄 Device state changed [Analog]: {dname} (ID: {did}) -> {current_status}")

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
            last_status: Optional[Dict[str, Any]] = _last_device_states.get(did)
            
            # ログ設計のポリシーに従い、変化の質を評価して出力
            log_device_state_change(dname, did, last_status, status)
            
            # キャッシュの更新
            if last_status != status:
                _last_device_states[did] = status

            has_data: bool = False
            
            if "power" in status:
                await sensor_service.process_power_data(
                    did, dname, status["power"], device.get("notify_settings", {})
                )
                has_data = True
            
            if "temperature" in status:
                await sensor_service.process_meter_data(
                    did, dname, status["temperature"], status.get("humidity", 0.0)
                )
                has_data = True
            
            if has_data:
                processed_count += 1

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