# MY_HOME_SYSTEM/monitors/nature_remo_monitor.py
import requests
import sys
import os
import time
from typing import Optional, List, Dict, Any, Tuple

# プロジェクトルートへのパス解決
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso

# ロガー設定
logger = setup_logging("nature_remo")

def fetch_api(endpoint: str, token: str) -> Optional[List[Dict[str, Any]]]:
    """
    Nature Remo APIからデータを取得する共通関数。
    """
    try:
        headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}
        url: str = f"https://api.nature.global/1/{endpoint}"
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code != 200:
            logger.error(f"⚠️ API Error [{endpoint}]: {res.status_code}")
            return None
            
        return res.json()
    except Exception as e:
        logger.error(f"❌ Connection failed [{endpoint}]: {e}")
        return None

def process_appliances(location: str, token: str) -> None:
    """
    家電情報（スマートメーターの電力等）を取得・保存する。
    """
    data: Optional[List[Dict[str, Any]]] = fetch_api("appliances", token)
    if not data: return

    for app in data:
        # スマートメーター (Echonet Lite) の電力取得
        smart_meter: Optional[Dict[str, Any]] = app.get("smart_meter")
        if smart_meter:
            echonet: List[Dict[str, Any]] = smart_meter.get("echonetlite_properties", [])
            # EPC 231 (瞬間電力計測値) を検索
            power_prop: Optional[Dict[str, Any]] = next((p for p in echonet if p.get("epc") == 231), None)
            
            if power_prop:
                try:
                    val_str: str = power_prop.get("val", "0")
                    power_val: float = float(val_str)
                    
                    device_name: str = f"{location}_{app.get('nickname', 'SmartMeter')}"
                    device_id: str = app.get("id", "unknown")

                    # 1. 新テーブル (power_usage)
                    save_log_generic(config.SQLITE_TABLE_POWER_USAGE,
                        ["device_id", "device_name", "wattage", "timestamp"],
                        (device_id, device_name, power_val, get_now_iso())
                    )
                    
                    # # 2. 旧テーブル (device_records) - 互換性
                    # save_log_generic("device_records",
                    #     ["timestamp", "device_name", "device_id", "device_type", "power_watts"],
                    #     (get_now_iso(), device_name, device_id, "SmartMeter", power_val)
                    # )
                    
                    logger.debug(f"⚡ Power: {device_name} = {power_val}W")

                except (ValueError, TypeError) as e:
                    logger.warning(f"Power parse error for {app.get('nickname')}: {e}")

def process_devices(location: str, token: str) -> None:
    """
    デバイス情報（Remo本体の温湿度センサー）を取得・保存する。
    """
    data: Optional[List[Dict[str, Any]]] = fetch_api("devices", token)
    if not data: return

    for dev in data:
        events: Dict[str, Any] = dev.get("newest_events", {})
        if not events: continue

        device_name: str = f"{location}_{dev.get('name', 'Remo')}"
        device_id: str = dev.get("id", "unknown")
        
        # 温湿度データの抽出
        te_val: Optional[float] = None
        hu_val: Optional[float] = None
        il_val: Optional[float] = None

        if "te" in events: te_val = float(events["te"]["val"])
        if "hu" in events: hu_val = float(events["hu"]["val"])
        if "il" in events: il_val = float(events["il"]["val"])

        # データがあれば保存
        if te_val is not None:
            # 1. 新テーブル (switchbot_meter_logs を温湿度ログとして共用)
            # Nature Remoですが、スキーマ（device_id, temp, humid）が同じためここに統合します
            save_log_generic(config.SQLITE_TABLE_SWITCHBOT_LOGS,
                ["device_id", "device_name", "temperature", "humidity", "timestamp"],
                (device_id, device_name, te_val, hu_val if hu_val else 0.0, get_now_iso())
            )
            
            # # 2. 旧テーブル (device_records)
            # save_log_generic("device_records",
            #     ["timestamp", "device_name", "device_id", "device_type", 
            #      "temperature_celsius", "humidity_percent", "brightness_state"],
            #     (get_now_iso(), device_name, device_id, "NatureRemo", 
            #      te_val, hu_val, str(il_val) if il_val else "")
            # )
            
            logger.debug(f"🌡️ Sensor: {device_name} = {te_val}°C / {hu_val}%")

def main() -> None:
    """メイン処理: 登録された全拠点のトークンで監視を実行"""
    logger.info("🚀 --- Nature Remo Monitor Started ---")

    # 監視対象リスト (拠点名, トークン)
    # config.py に定義があればリストに追加
    targets: List[Tuple[str, Optional[str]]] = [
        ("伊丹", config.NATURE_REMO_ACCESS_TOKEN),
        ("高砂", config.NATURE_REMO_ACCESS_TOKEN_TAKASAGO)
    ]

    for location, token in targets:
        if not token:
            continue
            
        logger.info(f"📍 Checking location: {location}")
        process_appliances(location, token)
        process_devices(location, token)
        
        # APIレートリミット考慮 (短時間の連続アクセス回避)
        time.sleep(1)

    logger.info("🏁 --- Nature Remo Monitor Completed ---")

if __name__ == "__main__":
    main()