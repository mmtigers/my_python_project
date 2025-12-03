# HOME_SYSTEM/switchbot_power_monitor.py
import requests
import sys
import sqlite3 
from datetime import datetime, timedelta
import pytz
import common # 共通ライブラリ
import config
import switchbot_get_device_list as sb_tool

# === ヘルパー関数: 前回の記録を取得 ===
def get_previous_status(device_id):
    """DBから指定デバイスの直近の記録（1つ前）を取得"""
    conn = common.get_db_connection()
    if not conn: return None
    try:
        cursor = conn.cursor()
        # 最新の1件を取得 (idの降順)
        query = f"SELECT power_watts, timestamp FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id=? ORDER BY id DESC LIMIT 1"
        cursor.execute(query, (device_id,))
        row = cursor.fetchone()
        return row # (power, timestamp) または None
    except Exception as e:
        print(f"[ERROR] 前回データ取得失敗: {e}")
        return None
    finally:
        conn.close()

# === ヘルパー関数: 稼働開始時間を探す (テレビ用) ===
def find_start_time(device_id, threshold):
    """電力が閾値を超え続けている期間の開始時刻を探す"""
    conn = common.get_db_connection()
    if not conn: return None
    try:
        cursor = conn.cursor()
        # 過去のデータを新しい順に遡る (最大100件=約8時間分くらいを検索)
        query = f"SELECT power_watts, timestamp FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id=? ORDER BY id DESC LIMIT 100"
        cursor.execute(query, (device_id,))
        rows = cursor.fetchall()
        
        last_on_time = None
        
        # 遡って「閾値を超えている最も古いデータ」を探す
        for row in rows:
            p_w = row["power_watts"]
            t_str = row["timestamp"]
            
            if p_w is not None and p_w >= threshold:
                last_on_time = t_str
            else:
                # 閾値を下回る記録が見つかったら、その直前が開始時間なのでループ終了
                break
                
        return last_on_time
    finally:
        conn.close()

# === データ記録 ===
def insert_power_record(name, device_id, device_type, power_w, temp_c, humidity_p, threshold_w):
    cols = ["timestamp", "device_name", "device_id", "device_type", "power_watts", "temperature_celsius", 
            "humidity_percent", "threshold_watts"]
    vals = (common.get_now_iso(), name, device_id, device_type, power_w, temp_c, humidity_p, threshold_w)
    
    if common.save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals):
        log_parts = []
        if power_w is not None: log_parts.append(f"{power_w:.1f}W")
        if temp_c is not None: log_parts.append(f"{temp_c:.1f}°C")
        print(f"[SUCCESS] 記録: {name} -> {', '.join(log_parts)}")

# === APIデータ取得 ===
def fetch_device_data(device_id, device_type):
    url = f"https://api.switch-bot.com/v1.1/devices/{device_id}/status"
    try:
        headers = sb_tool.create_switchbot_auth_headers()
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        if data.get('statusCode') == 100:
            body = data.get('body', {})
            result = {}
            if device_type.startswith('Plug'):
                result['power'] = float(body.get('weight', 0)) 
            elif device_type.startswith('Meter'):
                result['temperature'] = float(body.get('temperature', 0))
                result['humidity'] = float(body.get('humidity', 0))
            return result
        return None
    except Exception as e:
        print(f"[WARN] {device_id} 取得失敗: {e}")
        return None

# === 日時フォーマット変換 ===
def format_time_str(iso_str):
    try:
        if not iso_str: return "不明"
        # ISO形式から "HH:MM" に変換
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M")
    except:
        return "不明"

# ==========================================
# メイン処理
# ==========================================
if __name__ == "__main__":
    print(f"\n=== SwitchBot 定期監視 ({common.get_now_iso()}) ===")
    
    if not sb_tool.fetch_device_name_cache():
        sys.exit(1)
        
    device_settings_list = config.MONITOR_DEVICES
    
    for setting in device_settings_list:
        target_id = setting.get("id")
        target_type = setting.get("type")
        
        # 通知設定の取得
        notify_conf = setting.get("notify_settings", {})
        threshold = notify_conf.get("power_threshold_watts")
        mode = notify_conf.get("notify_mode", "CONTINUOUS") # デフォルト設定

        # PlugとMeter以外はスキップ
        if not (target_type.startswith("Plug") or target_type.startswith("Meter")):
            continue

        target_name = sb_tool.get_device_name_by_id(target_id) or "Unknown"
        
        # 1. 前回の状態を取得 (今回の記録を行う「前」の状態を知るため)
        prev_data = get_previous_status(target_id)
        prev_power = prev_data["power_watts"] if prev_data and prev_data["power_watts"] is not None else 0.0
        
        # 2. 現在の状態を取得
        data = fetch_device_data(target_id, target_type)
        
        if data:
            p_w = data.get('power')
            t_c = data.get('temperature')
            h_p = data.get('humidity')
            
            # 3. DB記録
            insert_power_record(target_name, target_id, target_type, p_w, t_c, h_p, threshold)
            
            # 4. 通知ロジック (Plug Mini かつ 閾値設定がある場合のみ)
            if p_w is not None and threshold is not None:
                
                # --- A. 炊飯器モード (ONになった瞬間だけ通知) ---
                if mode == "ON_START":
                    # 今回ON(閾値以上) かつ 前回OFF(閾値未満)
                    if p_w >= threshold and prev_power < threshold:
                        msg = {"type": "text", "text": f"🍚【炊飯通知】\nご飯を炊き始めました！\n({target_name}: {p_w:.1f}W)"}
                        common.send_line_push(config.LINE_USER_ID, [msg])
                        print(f"[ALERT] ON通知送信: {target_name}")

                # --- B. テレビモード (OFFになったら時間を通知) ---
                elif mode == "ON_END_SUMMARY":
                    # 今回OFF(閾値未満) かつ 前回ON(閾値以上)
                    if p_w < threshold and prev_power >= threshold:
                        # 稼働開始時間を探す
                        start_iso = find_start_time(target_id, threshold)
                        end_iso = common.get_now_iso()
                        
                        start_str = format_time_str(start_iso)
                        end_str = format_time_str(end_iso)
                        
                        msg = {"type": "text", "text": f"📺【テレビ通知】\nテレビが消えました。\n視聴時間: {start_str} 〜 {end_str}"}
                        common.send_line_push(config.LINE_USER_ID, [msg])
                        print(f"[ALERT] OFF要約通知送信: {target_name}")

                # --- C. 従来モード (ついている間ずっと通知) ---
                elif mode == "CONTINUOUS":
                    if p_w >= threshold:
                        msg = {"type": "text", "text": f"🚨【電力アラート】\n{target_name} が {p_w:.1f}W を記録 (閾値: {threshold}W)"}
                        common.send_line_push(config.LINE_USER_ID, [msg])
                        print(f"[ALERT] 継続通知送信: {target_name}")

    print("=== 完了 ===\n")