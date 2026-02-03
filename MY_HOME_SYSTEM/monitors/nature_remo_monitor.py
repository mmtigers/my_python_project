# MY_HOME_SYSTEM/monitors/nature_remo_monitor.py
import requests
import sys
import os
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, List, Dict, Any, Tuple

# プロジェクトルートへのパス解決
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso

# ロガー設定
logger = setup_logging("nature_remo")

# --- セッションとリトライ設定 (Design 9.3, 9.8) ---
def create_session() -> requests.Session:
    """
    リトライロジックを組み込んだセッションを作成する。
    - total=3: 最大3回リトライ
    - backoff_factor=1: 1秒, 2秒, 4秒...と待機時間を増やす
    - status_forcelist: 500, 502, 503, 504 などのサーバーエラー時はリトライ
    """
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# グローバルセッション (Design 9.5: TCPコネクション再利用)
_session = create_session()

def fetch_api(endpoint: str, token: str) -> Optional[List[Dict[str, Any]]]:
    """
    Nature Remo APIからデータを取得する共通関数。
    """
    headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}
    url: str = f"https://api.nature.global/1/{endpoint}"

    try:
        # タイムアウトを少し長めに確保 (Design 9.8)
        res = _session.get(url, headers=headers, timeout=15)
        
        if res.status_code != 200:
            # 4xx系エラー（認証失敗など）は設定ミスや契約切れの可能性があるため WARNING or ERROR
            # ここではAPI仕様変更などを考慮し WARNING とする
            logger.warning(f"⚠️ API Error [{endpoint}]: Status {res.status_code} - {res.text}")
            return None
            
        return res.json()

    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        # Design 8.2: ネットワーク起因の一時エラーは WARNING (通知なし)
        # リトライ(max_retries=3)後の最終的な失敗のみここに到達する
        logger.warning(f"⚠️ Network Issue [{endpoint}]: Connection failed after retries. ({str(e)})")
        return None

    except Exception as e:
        # Design 8.2: 想定外の論理エラー（パース失敗、コードバグ）のみ ERROR (通知あり)
        logger.error(f"❌ Unexpected Error [{endpoint}]: {e}", exc_info=True)
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

                    save_log_generic(config.SQLITE_TABLE_POWER_USAGE,
                        ["device_id", "device_name", "wattage", "timestamp"],
                        (device_id, device_name, power_val, get_now_iso())
                    )
                    
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
        
        te_val: Optional[float] = None
        hu_val: Optional[float] = None

        if "te" in events: te_val = float(events["te"]["val"])
        if "hu" in events: hu_val = float(events["hu"]["val"])

        if te_val is not None:
            save_log_generic(config.SQLITE_TABLE_SWITCHBOT_LOGS,
                ["device_id", "device_name", "temperature", "humidity", "timestamp"],
                (device_id, device_name, te_val, hu_val if hu_val else 0.0, get_now_iso())
            )
            
            logger.debug(f"🌡️ Sensor: {device_name} = {te_val}°C / {hu_val}%")

def main() -> None:
    """メイン処理"""
    logger.info("🚀 --- Nature Remo Monitor Started ---")

    targets: List[Tuple[str, Optional[str]]] = [
        ("伊丹", config.NATURE_REMO_ACCESS_TOKEN),
        ("高砂", config.NATURE_REMO_ACCESS_TOKEN_TAKASAGO)
    ]

    try:
        for location, token in targets:
            if not token:
                continue
                
            logger.info(f"📍 Checking location: {location}")
            process_appliances(location, token)
            process_devices(location, token)
            
            time.sleep(2) # Design 9.4: APIレート制限対策 (Interval確保)
            
    finally:
        # Design 9.5: リソースの明示的解放
        _session.close()

    logger.info("🏁 --- Nature Remo Monitor Completed ---")

if __name__ == "__main__":
    main()