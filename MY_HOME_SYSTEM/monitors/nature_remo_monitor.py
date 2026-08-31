# MY_HOME_SYSTEM/monitors/nature_remo_monitor.py
import asyncio
import sys
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, List, Dict, Any, Tuple

# プロジェクトルートへのパス解決
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.logger import setup_logging
from services import sensor_service

# ロガー設定
logger = setup_logging("nature_remo")

# --- API Client Setup ---

def create_session() -> requests.Session:
    """リトライロジック付きセッションの作成"""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session

def fetch_data_sync(location: str, token: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Nature Remo APIからデータを取得・整形して返す (同期処理)
    
    Args:
        location (str): 拠点名（伊丹/高砂など）
        token (str): APIアクセストークン

    Returns:
        Dict[str, List[Dict[str, Any]]]: 
            {
                "appliances": [...], # 家電リスト (電力データ含む)
                "devices": [...]     # デバイスリスト (温湿度含む)
            }
    """
    if not token:
        return {}
    
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    session = create_session()
    result = {"appliances": [], "devices": []}

    try:
        # 1. Appliances (電力情報など)
        url_app = "https://api.nature.global/1/appliances"
        res_app = session.get(url_app, headers=headers, timeout=10)
        res_app.raise_for_status()
        result["appliances"] = res_app.json()

        # 2. Devices (センサー情報など)
        url_dev = "https://api.nature.global/1/devices"
        res_dev = session.get(url_dev, headers=headers, timeout=10)
        res_dev.raise_for_status()
        result["devices"] = res_dev.json()
        
    except Exception as e:
        # 通信エラー等は介入が必要な可能性があるため ERROR/WARNING で出力
        logger.error("API Error at %s: %s", location, e)
    
    return result

# --- Main Logic (Async) ---

async def process_location(location: str, token: str) -> None:
    """
    1つの拠点(伊丹/高砂)のデータを処理する
    
    Args:
        location (str): 拠点名
        token (str): APIトークン
    """
    if not token:
        return

    # APIコールはブロッキングなのでスレッドに逃がす
    data = await asyncio.to_thread(fetch_data_sync, location, token)
    
    # 1. 電力データの処理 (Appliances)
    for app in data.get("appliances", []):
        # スマートメーター (Nature Remo E Lite) の判定
        if app.get("type") == "EL_SMART_METER":
            smart_meter = app.get("smart_meter", {})
            echonet_props = smart_meter.get("echonetlite_properties", [])
            
            # 瞬時電力計測値 (EPC: 0xE7) を探す
            power_val: Optional[float] = None
            for prop in echonet_props:
                if prop.get("epc") == 231: # 0xE7 = 231
                    val_str = prop.get("val")
                    # #235: str.isdigit()は符号付き文字列(例: "-120")に対してFalseを返すため、
                    # 太陽光発電等による逆潮流(売電)時の負の瞬時電力値が無警告で欠損していた。
                    # float()へのパースを直接試み、失敗時のみ警告ログを出す方式に変更。
                    try:
                        power_val = float(val_str)
                    except (TypeError, ValueError):
                        logger.warning(
                            "⚠️ 瞬時電力値のパースに失敗しました (location=%s, val=%r)", location, val_str
                        )
                    break
            
            if power_val is not None:
                dev_id = app.get("id", "unknown")
                dev_name = f"{location}_{app.get('nickname', 'SmartMeter')}"
                
                # Serviceへ委譲
                await sensor_service.process_power_data(dev_id, dev_name, power_val, {})
                
                # Log Level Adjustment: DEBUG for steady state
                # フォーマット処理の負荷を下げるため %s 記法を使用
                logger.debug("⚡ Power: %s = %sW", dev_name, power_val)

    # 2. センサーデータの処理 (Devices)
    for dev in data.get("devices", []):
        dev_id = dev.get("id", "unknown")
        dev_name = f"{location}_{dev.get('name', 'Remo')}"
        
        events = dev.get("newest_events", {})
        te_val: Optional[float] = None
        hu_val: Optional[float] = None
        
        if "te" in events: 
            te_val = float(events["te"]["val"])
        if "hu" in events: 
            hu_val = float(events["hu"]["val"])
            
        if te_val is not None:
            # Serviceへ委譲
            await sensor_service.process_meter_data(
                dev_id, dev_name, te_val, hu_val if hu_val else 0.0
            )
            # Log Level Adjustment: DEBUG for steady state
            logger.debug("🌡️ Sensor: %s = %s°C", dev_name, te_val)


async def main() -> None:
    """メイン処理"""
    logger.debug("🚀 --- Nature Remo Monitor Started (New Architecture) ---")

    targets: List[Tuple[str, Optional[str]]] = [
        ("伊丹", config.NATURE_REMO_ACCESS_TOKEN),
        ("高砂", config.NATURE_REMO_ACCESS_TOKEN_TAKASAGO)
    ]

    for loc, token in targets:
        if token:
            await process_location(loc, token)

    logger.debug("🏁 --- Monitor Completed ---")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.critical("Unexpected Error: %s", e)