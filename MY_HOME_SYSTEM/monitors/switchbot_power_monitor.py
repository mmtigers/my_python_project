# MY_HOME_SYSTEM/monitors/switchbot_power_monitor.py
import requests
import sys
import os
from typing import Dict, Any, Optional, List, Tuple

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 自作モジュール
import config
from services import switchbot_service as sb_tool
from core.logger import setup_logging
from core.database import save_log_generic, get_db_cursor
from core.utils import get_now_iso
from services.notification_service import send_push

# ロガー設定
logger = setup_logging("device_monitor")

def insert_device_record(name: str, device_id: str, device_type: str, data: Dict[str, Any]) -> None:
    """
    デバイスのステータスをDBに記録する。
    """
    cols: List[str] = [
        "timestamp", "device_name", "device_id", "device_type", 
        "power_watts", "temperature_celsius", "humidity_percent", 
        "contact_state", "movement_state", "brightness_state", "threshold_watts"
    ]
    
    threshold: Optional[float] = data.get('threshold')
    
    vals: Tuple[Any, ...] = (
        get_now_iso(), 
        name, 
        device_id, 
        device_type, 
        data.get('power'), 
        data.get('temperature'), 
        data.get('humidity'),
        data.get('contact'),
        data.get('motion'),
        data.get('brightness'),
        threshold
    )
    
    if save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals):
        # ログ出力用の詳細情報を構築
        log_parts: List[str] = []
        if data.get('power') is not None: 
            log_parts.append(f"{data['power']}W")
        if data.get('temperature') is not None: 
            log_parts.append(f"{data['temperature']}°C")
        if data.get('contact'): 
            log_parts.append(f"開閉:{data['contact']}")
        if data.get('motion'): 
            log_parts.append(f"動き:{data['motion']}")
        
        log_msg: str = ", ".join(log_parts) if log_parts else "No Data"
        logger.info(f"💾 Record saved: {name} ({log_msg})")
    else:
        logger.error(f"❌ Failed to save record for {name}")

def calculate_plug_power(body: Dict[str, Any]) -> float:
    """
    プラグの電力を計算する。APIの仕様により、0Wと報告されても
    電圧と電流がある場合は再計算を行う補正ロジック。
    """
    watts: float = float(body.get('weight', 0))
    
    if watts == 0:
        volts: float = float(body.get('voltage', 0))
        # electricCurrent(mA) を A に変換して計算
        amps: float = float(body.get('electricCurrent', 0)) / 1000.0
        if volts > 0 and amps > 0:
            watts = volts * amps
            
    return round(watts, 1)

def fetch_device_status(device_id: str, device_type: str) -> Optional[Dict[str, Any]]:
    """
    SwitchBot APIからデバイスの状態を取得する。
    """
    url: str = f"https://api.switch-bot.com/v1.1/devices/{device_id}/status"
    try:
        headers: Dict[str, str] = sb_tool.create_switchbot_auth_headers()
        data: Dict[str, Any] = sb_tool.request_switchbot_api(url, headers)
        
        if data.get('statusCode') != 100:
            logger.warning(f"⚠️ API Status Error [{device_id}]: {data.get('statusCode')}")
            return None

        body: Dict[str, Any] = data.get('body', {})
        result: Dict[str, Any] = {}
        
        # デバイスタイプ別のパース処理
        if "Plug" in device_type:
            result['power'] = calculate_plug_power(body)
        elif "Meter" in device_type:
            result['temperature'] = float(body.get('temperature', 0))
            result['humidity'] = float(body.get('humidity', 0))
        elif "Contact" in device_type:
            result['contact'] = body.get('openState', 'unknown')
            result['brightness'] = body.get('brightness', 'unknown')
        elif "Motion" in device_type:
            result['motion'] = "detected" if body.get('moveDetected') else "clear"
            result['brightness'] = body.get('brightness', 'unknown')
        
        return result

    except requests.exceptions.Timeout:
        logger.warning(f"⌛ Timeout fetching status for [{device_id}]")
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected error fetching status for [{device_id}]: {e}")
        return None 

def get_prev_power(device_id: str) -> float:
    """
    データベースから該当デバイスの直近の電力値を取得する。
    """
    with get_db_cursor() as cur:
        if not cur: 
            return 0.0
        try:
            sql: str = f"SELECT power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id=? ORDER BY id DESC LIMIT 1"
            cur.execute(sql, (device_id,))
            row: Optional[Tuple[Any]] = cur.fetchone()
            if row:
                # 辞書形式またはタプル形式の両方に対応
                val: Any = row["power_watts"] if isinstance(row, dict) else row[0]
                return float(val) if val is not None else 0.0
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching previous power for {device_id}: {e}")
            return 0.0

def process_power_notification(name: str, device_id: str, current_power: float, settings: Dict[str, Any]) -> None:
    """
    電力の変化に基づき通知を判定・実行する。
    """
    threshold: Optional[float] = settings.get("power_threshold_watts")
    mode: str = settings.get("notify_mode", "LOG_ONLY")
    target: str = settings.get("target", config.NOTIFICATION_TARGET)

    if threshold is None or mode == "LOG_ONLY":
        return

    prev_power: float = get_prev_power(device_id)
    msg: Optional[str] = None

    # 通知ロジックの判定
    if mode == "ON_START" and current_power >= threshold and prev_power < threshold:
        msg = f"🍚【炊飯通知】\n{name} が動き出したよ！ ({current_power}W)"
    elif mode == "ON_END_SUMMARY" and current_power < threshold and prev_power >= threshold:
        msg = f"💡【使用終了】\n{name} の電源が切れたみたい"
    elif mode == "CONTINUOUS" and current_power >= threshold:
        msg = f"🚨【電力アラート】\n{name} がまだついてるよ！ ({current_power}W)"

    if msg:
        send_push(config.LINE_USER_ID or "", [{"type": "text", "text": msg}], target=target)
        logger.info(f"📢 Notification sent ({target}): {name}")

def main() -> None:
    """
    メインループ。設定された全デバイスのステータスを確認する。
    """
    logger.info("🚀 --- SwitchBot Device Power Monitor Started ---")
    
    # デバイス名の最新キャッシュを取得
    if not sb_tool.fetch_device_name_cache():
        logger.warning("Could not refresh device name cache. Using names from config.")
    
    monitor_devices: List[Dict[str, Any]] = getattr(config, "MONITOR_DEVICES", [])
    
    for s in monitor_devices:
        try:
            tid: str = s.get("id", "")
            ttype: str = s.get("type", "")
            
            if not tid or not ttype:
                continue

            # 名前解決 (APIキャッシュ > Config > デフォルト)
            api_name: Optional[str] = sb_tool.get_device_name_by_id(tid)
            tname: str = api_name or s.get("name") or "Unknown Device"
            
            # APIから最新状態を取得
            data: Optional[Dict[str, Any]] = fetch_device_status(tid, ttype)
            
            if data:
                # 閾値設定をマージ
                notify_settings: Dict[str, Any] = s.get("notify_settings", {})
                data['threshold'] = notify_settings.get("power_threshold_watts")
                
                # 1. データベースに記録
                insert_device_record(tname, tid, ttype, data)

                # 2. 電力ベースの通知処理 (プラグ限定)
                if "Plug" in ttype and data.get('power') is not None:
                    process_power_notification(tname, tid, float(data['power']), notify_settings)
                    
        except Exception as e:
            logger.error(f"🔥 Error processing device {s.get('name', 'Unknown')}: {e}")
            continue

    logger.info("🏁 --- Device Check Completed ---")

if __name__ == "__main__":
    main()