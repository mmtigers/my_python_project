# MY_HOME_SYSTEM/monitors/nature_remo_monitor.py
import requests
import sys
import os
from typing import Optional, List, Dict, Any

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso

# 統一ロガー設定 
logger = setup_logging("nature_remo")

def fetch_nature_remo_data() -> Optional[List[Dict[str, Any]]]:
    """
    Nature Remo APIから家電・デバイス情報を取得する。
    
    Returns:
        Optional[List[Dict[str, Any]]]: デバイス情報のリスト。失敗時はNone。
    """
    token: Optional[str] = config.NATURE_REMO_ACCESS_TOKEN
    if not token:
        logger.error("Nature Remo トークンが設定されていません (.envを確認してください)")
        return None
    try:
        res = requests.get(
            "https://api.nature.global/1/appliances", 
            headers={"Authorization": f"Bearer {token}"}, 
            timeout=10
        )
        if res.status_code != 200:
            logger.error(f"Nature Remo APIエラー: HTTP {res.status_code}")
            return None
        return res.json()
    except Exception as e:
        logger.error(f"Nature Remo への接続に失敗しました: {e}")
        return None

def extract_power_data(appliances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    デバイスリストからスマートメーターの電力データのみを抽出する。
    
    Args:
        appliances (List[Dict[str, Any]]): APIから取得した全デバイスデータ
        
    Returns:
        List[Dict[str, Any]]: 抽出された電力データリスト
    """
    results: List[Dict[str, Any]] = []
    for app in appliances:
        # スマートメーター（ECHONET Lite）を対象とする
        if app.get("type") == "EL_SMART_METER":
            power_val: Optional[float] = None
            smart_meter: Dict[str, Any] = app.get("smart_meter", {})
            
            # ECHONET Liteプロパティから「瞬時電力計測値 (EPC: 231)」を探す
            if smart_meter:
                for prop in smart_meter.get("echonetlite_properties", []):
                    if prop.get("epc") == 231:
                        try:
                            power_val = float(prop.get("val", 0))
                        except (ValueError, TypeError):
                            logger.warning(f"電力値のパースに失敗: {prop.get('val')}")
                        break
            
            if power_val is not None:
                results.append({
                    "name": app.get("nickname", "Smart Meter"),
                    "id": app.get("id"),
                    "power": power_val
                })
    return results

if __name__ == "__main__":
    logger.info("=== Nature Remo 電力監視開始 ===")
    
    # 1. データ取得
    raw_data = fetch_nature_remo_data()
    if not raw_data:
        sys.exit(1)

    # 2. 電力データ抽出
    targets = extract_power_data(raw_data)
    if not targets:
        logger.warning("スマートメーターの電力データが見つかりませんでした。")
        sys.exit(0)
    
    # 3. DBへ保存
    for t in targets:
        # センサーログテーブルに電力を記録
        success = save_log_generic(
            config.SQLITE_TABLE_SENSOR, 
            ["timestamp", "device_name", "device_id", "device_type", "power_watts"],
            (get_now_iso(), t["name"], t["id"], "SmartMeter", t["power"])
        )
        
        if success:
            logger.info(f"✅ 電力データを保存しました: {t['name']} = {t['power']} W")
        else:
            logger.error(f"❌ {t['name']} のDB保存に失敗しました")