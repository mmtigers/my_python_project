# HOME_SYSTEM/switchbot_power_monitor.py
import requests
import sys
import common
import config
import switchbot_get_device_list as sb_tool

def fetch_device_data(device_id):
    url = f"https://api.switch-bot.com/v1.1/devices/{device_id}/status"
    try:
        headers = sb_tool.create_switchbot_auth_headers()
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        if data.get('statusCode') == 100: return data.get('body', {})
    except Exception as e:
        print(f"[WARN] {device_id} 取得失敗: {e}")
    return None

def get_prev_power(device_id):
    conn = common.get_db_connection()
    if not conn: return 0.0
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,))
        row = cur.fetchone()
        return row["power_watts"] if row and row["power_watts"] is not None else 0.0
    finally: conn.close()

if __name__ == "__main__":
    print(f"\n=== SwitchBot 定期監視 ({common.get_now_iso()}) ===")
    if not sb_tool.fetch_device_name_cache(): sys.exit(1)
    
    for s in config.MONITOR_DEVICES:
        tid, ttype = s.get("id"), s.get("type")
        if not (ttype.startswith("Plug") or ttype.startswith("Meter")): continue
        
        tname = sb_tool.get_device_name_by_id(tid) or "Unknown"
        data = fetch_device_data(tid)
        
        if data:
            pw = float(data.get('weight', 0)) if ttype.startswith("Plug") else None
            tc = float(data.get('temperature', 0)) if ttype.startswith("Meter") else None
            hp = float(data.get('humidity', 0)) if ttype.startswith("Meter") else None
            
            # DB記録
            cols = ["timestamp", "device_name", "device_id", "device_type", "power_watts", "temperature_celsius", "humidity_percent", "threshold_watts"]
            vals = (common.get_now_iso(), tname, tid, ttype, pw, tc, hp, s.get("notify_settings", {}).get("power_threshold_watts"))
            common.save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals)
            print(f"[SUCCESS] 記録: {tname}")

            # 通知判定
            conf = s.get("notify_settings", {})
            th = conf.get("power_threshold_watts")
            mode = conf.get("notify_mode", "LOG_ONLY")
            
            if pw is not None and th is not None and mode != "LOG_ONLY":
                prev = get_prev_power(tid)
                msg = None
                
                if mode == "ON_START" and pw >= th and prev < th:
                    msg = f"🍚【炊飯通知】\n{tname} が稼働開始しました ({pw}W)"
                elif mode == "ON_END_SUMMARY" and pw < th and prev >= th:
                    msg = f"💡【使用終了】\n{tname} の使用が終わりました"
                elif mode == "CONTINUOUS" and pw >= th:
                    msg = f"🚨【電力アラート】\n{tname} が稼働中です ({pw}W)"
                
                if msg:
                    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}])
                    print(f"[ALERT] 通知送信: {tname}")

    print("=== 完了 ===\n")