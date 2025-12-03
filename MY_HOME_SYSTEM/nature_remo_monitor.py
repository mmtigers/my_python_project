# HOME_SYSTEM/nature_remo_monitor.py
import requests
import common
import config
import sys

def fetch_nature_remo_data():
    """Nature Remo APIから電力データを取得"""
    token = config.NATURE_REMO_ACCESS_TOKEN
    if not token:
        print("[ERROR] Nature Remo トークンが設定されていません")
        return None

    url = "https://api.nature.global/1/appliances"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            print(f"[ERROR] API取得失敗: {res.status_code} {res.text}")
            return None
        return res.json()
    except Exception as e:
        print(f"[ERROR] 接続エラー: {e}")
        return None

def extract_power_data(appliances):
    """取得したデータからスマートメーター（E Lite）の電力を抽出"""
    results = []
    
    for app in appliances:
        # "EL_SMART_METER" が E Lite (スマートメーター) です
        if app.get("type") == "EL_SMART_METER":
            device_name = app.get("nickname", "スマートメーター")
            device_id = app.get("id")
            
            # スマートメーターのプロパティ (EPC: 0xE7 = 瞬時電力計測値)
            smart_meter = app.get("smart_meter", {})
            properties = smart_meter.get("echonetlite_properties", [])
            
            power_val = None
            for p in properties:
                if p.get("epc") == 231: # 231 = 0xE7 (瞬時電力)
                    power_val = int(p.get("val"))
                    break
            
            if power_val is not None:
                results.append({
                    "name": device_name,
                    "id": device_id,
                    "power": float(power_val)
                })
    return results

if __name__ == "__main__":
    print(f"\n=== Nature Remo 監視 ({common.get_now_iso()}) ===")
    
    # DB接続チェック
    conn = common.get_db_connection()
    if not conn: sys.exit(1)
    conn.close()

    # データ取得
    data = fetch_nature_remo_data()
    if not data: sys.exit(1)

    # 抽出と記録
    targets = extract_power_data(data)
    
    if not targets:
        print("[WARN] スマートメーターが見つかりませんでした。")
    
    for t in targets:
        # DBへの保存 (SwitchBotと同じ device_records テーブルを使用)
        # power_watts 以外の項目は None で埋めます
        cols = ["timestamp", "device_name", "device_id", "device_type", "power_watts"]
        vals = (common.get_now_iso(), t["name"], t["id"], "Nature Remo E Lite", t["power"])
        
        if common.save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals):
            print(f"[SUCCESS] 記録: {t['name']} -> {t['power']}W")

    print("=== 完了 ===\n")