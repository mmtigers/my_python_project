# MY_HOME_SYSTEM/monitors/switchbot_power_monitor.py
import requests
import sys
import os
import time
from typing import Dict, Any, Optional, List, Tuple

# プロジェクトルートへのパス解決 (unified_server.py 等と整合性を保つ)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 自作モジュール
import config
from services import switchbot_service as sb_tool
from core.logger import setup_logging
from core.database import save_log_generic, get_db_cursor
from core.utils import get_now_iso
from services.notification_service import send_push

# ロガー設定
logger = setup_logging("device_monitor")

def insert_power_record(device_id: str, device_name: str, wattage: float) -> bool:
    """
    電力消費データを power_usage テーブルに記録する (設計書 3.2 準拠)。
    """
    cols: List[str] = ["device_id", "device_name", "wattage", "timestamp"]
    vals: Tuple[Any, ...] = (device_id, device_name, wattage, get_now_iso())
    
    success: bool = save_log_generic(config.SQLITE_TABLE_POWER_USAGE, cols, vals)
    if success:
        logger.debug(f"💾 Power record saved: {device_name} ({wattage}W)")
    return success

def insert_meter_record(device_id: str, device_name: str, temp: float, humid: float) -> bool:
    """
    温湿度データを switchbot_meter_logs テーブルに記録する (設計書 3.2 準拠)。
    """
    cols: List[str] = ["device_id", "device_name", "temperature", "humidity", "timestamp"]
    vals: Tuple[Any, ...] = (device_id, device_name, temp, humid, get_now_iso())
    
    success: bool = save_log_generic(config.SQLITE_TABLE_SWITCHBOT_LOGS, cols, vals)
    if success:
        logger.debug(f"💾 Meter record saved: {device_name} ({temp}°C / {humid}%)")
    return success

def insert_legacy_record(name: str, device_id: str, device_type: str, data: Dict[str, Any]) -> None:
    """
    後方互換性のため、旧 device_records テーブルにも記録を継続する。
    """
    cols: List[str] = [
        "timestamp", "device_name", "device_id", "device_type", 
        "power_watts", "temperature_celsius", "humidity_percent", 
        "contact_state", "movement_state", "brightness_state"
    ]
    vals: Tuple[Any, ...] = (
        get_now_iso(), name, device_id, device_type, 
        data.get('power'), data.get('temperature'), data.get('humidity'),
        data.get('contact'), data.get('motion'), data.get('brightness')
    )
    save_log_generic("device_records", cols, vals)

def calculate_plug_power(body: Dict[str, Any]) -> float:
    """
    プラグの電力を計算・補正する。
    """
    watts: float = float(body.get('weight', 0))
    if watts == 0:
        volts: float = float(body.get('voltage', 0))
        amps: float = float(body.get('electricCurrent', 0)) / 1000.0
        if volts > 0 and amps > 0:
            watts = volts * amps
    return round(watts, 1)

def fetch_device_status(device_id: str, device_type: str) -> Optional[Dict[str, Any]]:
    """
    SwitchBot APIからデバイスの状態を取得する。Fail-Safe実装。
    """
    url: str = f"https://api.switch-bot.com/v1.1/devices/{device_id}/status"
    try:
        headers: Dict[str, str] = sb_tool.create_switchbot_auth_headers()
        # 再試行ロジックを含むAPIリクエスト (設計書 9.3 準拠)
        data: Dict[str, Any] = sb_tool.request_switchbot_api(url, headers)
        
        if data.get('statusCode') != 100:
            logger.warning(f"⚠️ API Status Error [{device_id}]: {data.get('statusCode')}")
            return None

        body: Dict[str, Any] = data.get('body', {})
        result: Dict[str, Any] = {}
        
        if "Plug" in device_type:
            result['power'] = calculate_plug_power(body)
        elif "Meter" in device_type:
            result['temperature'] = float(body.get('temperature', 0))
            result['humidity'] = float(body.get('humidity', 0))
        elif "Contact" in device_type:
            result['contact'] = body.get('openState', 'unknown')
        elif "Motion" in device_type:
            result['motion'] = "detected" if body.get('moveDetected') else "clear"
        
        return result

    except requests.exceptions.HTTPError as e:
        # [追加] 429エラー(レート制限)はWarningレベルでハンドリングし、スタックトレースを出さない
        if e.response is not None and e.response.status_code == 429:
            logger.warning(f"⚠️ API Rate Limit Reached for [{device_id}]. Skipping this turn.")
            return None
        # その他のHTTPエラーはこれまで通り
        logger.error(f"❌ HTTP Error for [{device_id}]: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected Error for [{device_id}]: {e}")
        return None

def get_prev_power(device_id: str) -> float:
    """
    DBから直近の電力値を取得する。
    """
    with get_db_cursor() as cur:
        if not cur: return 0.0
        try:
            sql: str = f"SELECT wattage FROM {config.SQLITE_TABLE_POWER_USAGE} WHERE device_id=? ORDER BY id DESC LIMIT 1"
            cur.execute(sql, (device_id,))
            row: Optional[sqlite3.Row] = cur.fetchone()
            return float(row["wattage"]) if row else 0.0
        except Exception:
            return 0.0

def process_notifications(name: str, device_id: str, current_power: float, settings: Dict[str, Any]) -> None:
    """
    電力変化に基づく通知処理。
    """
    threshold: Optional[float] = settings.get("power_threshold_watts")
    mode: str = settings.get("notify_mode", "LOG_ONLY")
    if threshold is None or mode == "LOG_ONLY": return

    prev_power: float = get_prev_power(device_id)
    msg: Optional[str] = None

    if mode == "ON_START" and current_power >= threshold and prev_power < threshold:
        msg = f"🍚【炊飯通知】\n{name} が動き出したよ！ ({current_power}W)"
    elif mode == "ON_END_SUMMARY" and current_power < threshold and prev_power >= threshold:
        msg = f"💡【使用終了】\n{name} の電源が切れたみたい"

    if msg:
        send_push(config.LINE_USER_ID or "", [{"type": "text", "text": msg}], target=settings.get("target", "discord"))

def main() -> None:
    """
    メインループ。全デバイスの巡回監視。
    """
    logger.info("🚀 --- SwitchBot Monitor Started (New Schema Mode) ---")
    
    # devices.json からロードされた全デバイスを処理
    monitor_devices: List[Dict[str, Any]] = config.MONITOR_DEVICES
    
    for device in monitor_devices:
        did: str = device.get("id", "")
        dtype: str = device.get("type", "")
        dname: str = device.get("name", "Unknown")
        
        if not did or not dtype: continue

        status: Optional[Dict[str, Any]] = fetch_device_status(did, dtype)
        if not status: continue

        # [追加] APIバースト防止のため、リクエスト間に2秒のインターバルを設ける
        time.sleep(2)

        # 1. 新テーブルへの振り分け保存
        if "power" in status:
            insert_power_record(did, dname, status["power"])
            process_notifications(dname, did, status["power"], device.get("notify_settings", {}))
            
        if "temperature" in status:
            insert_meter_record(did, dname, status["temperature"], status["humidity"])

        # 2. 後方互換性のための旧テーブル保存
        # insert_legacy_record(dname, did, dtype, status)

    logger.info(f"🏁 --- Monitor Completed ({len(monitor_devices)} devices processed) ---")

if __name__ == "__main__":
    main()