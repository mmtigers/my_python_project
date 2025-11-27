# HOME_SYSTEM/switchbot_power_monitor.py
import requests
import sys
import sqlite3 
from datetime import datetime 
import time
import hashlib
import hmac
import base64
import uuid

# === 必要な連携モジュールのインポート ===
try:
    import config
    import send_line
    import switchbot_get_device_list as sb_tool 
    print("[INFO] 必要な全モジュールの読み込みに成功しました。")
except ImportError as e:
    print(f"\n[FATAL ERROR] モジュール読み込みエラー: {e}")
    sys.exit(1)

# ==========================================
# データベース関連
# ==========================================
def initialize_database():
    """DB接続確認のみ（テーブル作成は init_unified_db.py で実施済み）"""
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH)
        conn.close()
        return True
    except sqlite3.Error as e:
        print(f"[ERROR] DB接続エラー: {e}")
        return False

def insert_power_record(name, device_id, device_type, power_w, temp_c, humidity_p, 
                        contact_s, movement_s, brightness_s, hub_onoff_s, cam_onoff_s, threshold_w):
    """取得したデータをデータベースに記録します。"""
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH)
        cursor = conn.cursor()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        insert_query = f"""
        INSERT INTO {config.SQLITE_TABLE_SENSOR} 
        (timestamp, device_name, device_id, device_type, power_watts, temperature_celsius, humidity_percent, 
         contact_state, movement_state, brightness_state, hub_onoff, cam_onoff, threshold_watts) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        cursor.execute(insert_query, (
            current_time, name, device_id, device_type,
            power_w, temp_c, humidity_p, 
            contact_s, movement_s, brightness_s, hub_onoff_s, 
            cam_onoff_s,
            threshold_w
        ))

        conn.commit()
        conn.close()
        
        # ログ表示用
        log_parts = []
        if power_w is not None: log_parts.append(f"{power_w:.2f} W")
        if temp_c is not None: log_parts.append(f"{temp_c:.1f}°C / {humidity_p:.1f}%")
        
        print(f"[SUCCESS] 記録完了: {name} -> {', '.join(log_parts)}")
        return True
    except sqlite3.Error as e:
        print(f"[ERROR] データ挿入エラー: {e}")
        return False

# ==========================================
# SwitchBot API データ取得
# ==========================================
def fetch_device_data(device_id, device_type):
    url = f"https://api.switch-bot.com/v1.1/devices/{device_id}/status"
    try:
        headers = sb_tool.create_switchbot_auth_headers()
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        
        if data.get('statusCode') == 100:
            body = data.get('body', {})
            result = {}

            if device_type.startswith('Plug'):
                result['power'] = float(body.get('weight', 0)) 
            elif device_type.startswith('Meter'):
                result['temperature'] = float(body.get('temperature', 0))
                result['humidity'] = float(body.get('humidity', 0))
            
            # ※Contact Sensorなどはここで処理しません（Webhookに任せるため）
            
            return result
        return None
    except Exception as e:
        print(f"[WARN] {device_id} の取得失敗: {e}")
        return None

# ==========================================
# メイン処理
# ==========================================
if __name__ == "__main__":
    print("\n=== SwitchBot 定期監視 (Plug & Meter Only) ===")

    # DBチェック
    if not initialize_database(): sys.exit(1)
        
    # デバイス名キャッシュ
    if not sb_tool.fetch_device_name_cache():
        print("[FATAL] デバイスリスト取得失敗")
        sys.exit(1)
        
    # 監視実行
    device_settings_list = config.MONITOR_DEVICES
    
    # 実行対象をカウント
    target_count = sum(1 for d in device_settings_list if d["type"].startswith("Plug") or d["type"].startswith("Meter"))
    print(f"[INFO] 全デバイス数: {len(device_settings_list)} / 今回の監視対象: {target_count}")
    
    for setting in device_settings_list:
        target_id = setting.get("id")
        target_type = setting.get("type") 
        threshold_watts = setting.get("notify_settings", {}).get("power_threshold_watts")

        # ★★★ フィルタリング処理 ★★★
        # "Plug" または "Meter" で始まるデバイス以外はスキップします
        if not (target_type.startswith("Plug") or target_type.startswith("Meter")):
            continue

        # 1. 名前解決
        target_name = sb_tool.get_device_name_by_id(target_id) or "Unknown"
        print(f"\n> 取得中: {target_name} ({target_type})")

        # 2. データ取得
        data = fetch_device_data(target_id, target_type)
        
        if data:
            p_w = data.get('power')
            t_c = data.get('temperature')
            h_p = data.get('humidity')
            
            # 3. DB記録 (センサー系データはNoneで渡す)
            insert_power_record(target_name, target_id, target_type,
                                p_w, t_c, h_p, None, None, None, None, None, threshold_watts)
            
            # 4. 電力通知判定 (Plug Miniのみ)
            if p_w is not None and threshold_watts is not None:
                if p_w >= threshold_watts:
                    msg = f"🚨【電力アラート】\n{target_name} が {p_w:.1f}W を記録しました (閾値: {threshold_watts}W)"
                    send_line.send_push_message(msg)
                    print("[ALERT] 通知を送信しました")

    print("\n=== 監視完了 ===\n")