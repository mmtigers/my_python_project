# MY_HOME_SYSTEM/monitors/nature_remo_monitor.py
import requests
import sys
import os
# import common <-- 削除

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso



logger = setup_logging("nature_remo")

def fetch_nature_remo_data():
    token = config.NATURE_REMO_ACCESS_TOKEN
    if not token:
        logger.error("Nature Remo トークン未設定")
        return None
    try:
        res = requests.get("https://api.nature.global/1/appliances", 
                           headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if res.status_code != 200:
            logger.error(f"APIエラー: {res.status_code}")
            return None
        return res.json()
    except Exception as e:
        logger.error(f"接続エラー: {e}")
        return None

def extract_power_data(appliances):
    results = []
    for app in appliances:
        if app.get("type") == "EL_SMART_METER":
            power_val = None
            for p in app.get("smart_meter", {}).get("echonetlite_properties", []):
                if p.get("epc") == 231:
                    power_val = int(p.get("val"))
                    break
            if power_val is not None:
                results.append({"name": app.get("nickname", "Meter"), "id": app.get("id"), "power": float(power_val)})
    return results

if __name__ == "__main__":
    logger.info(f"=== Nature Remo 監視開始 ===")
    
    data = fetch_nature_remo_data()
    if not data: sys.exit(1)

    targets = extract_power_data(data)
    if not targets: logger.warning("スマートメーターが見つかりませんでした。")
    
    for t in targets:
        # common.save_log_generic -> save_log_generic
        if save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                 ["timestamp", "device_name", "device_id", "device_type", "power_watts"],
                                 (get_now_iso(), t["name"], t["id"], "Nature Remo E Lite", t["power"])):
            logger.info(f"💾 Record: {t['name']} = {t['power']}W")
        else:
            logger.error("DB保存失敗")