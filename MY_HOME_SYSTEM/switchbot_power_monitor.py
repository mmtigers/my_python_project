# HOME_SYSTEM/switchbot_power_monitor.py
import requests
import sys
import common
import config
import switchbot_get_device_list as sb_tool

# ロガー
logger = common.setup_logging("power_monitor")

def insert_power_record(name, device_id, device_type, power_w, temp_c, humidity_p, threshold_w):
    cols = ["timestamp", "device_name", "device_id", "device_type", "power_watts", "temperature_celsius", 
            "humidity_percent", "threshold_watts"]
    vals = (common.get_now_iso(), name, device_id, device_type, power_w, temp_c, humidity_p, threshold_w)
    
    if common.save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals):
        log_parts = []
        if power_w is not None: log_parts.append(f"{power_w:.1f}W")
        if temp_c is not None: log_parts.append(f"{temp_c:.1f}°C")
        logger.info(f"記録: {name} -> {', '.join(log_parts)}")

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
                # ★修正: weightが0なら、電圧×電流から計算する「二段構え」ロジック
                watts = float(body.get('weight', 0))
                
                # weightが0で、かつ電流(electricCurrent)がある場合、計算で補完する
                if watts == 0:
                    volts = float(body.get('voltage', 0))
                    amps = float(body.get('electricCurrent', 0)) / 1000.0 # mAをAに変換
                    if volts > 0 and amps > 0:
                        watts = volts * amps
                        
                result['power'] = round(watts, 1)

            elif device_type.startswith('Meter'):
                result['temperature'] = float(body.get('temperature', 0))
                result['humidity'] = float(body.get('humidity', 0))
            return result
        return None
    except Exception as e:
        logger.warning(f"{device_id} 取得失敗: {e}")
        return None

def get_prev_power(device_id):
    with common.get_db_cursor() as cur:
        if not cur: return 0.0
        try:
            cur.execute(f"SELECT power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,))
            row = cur.fetchone()
            return row["power_watts"] if row and row["power_watts"] is not None else 0.0
        except: return 0.0

if __name__ == "__main__":
    logger.info("=== SwitchBot 定期監視 ===")
    if not sb_tool.fetch_device_name_cache(): sys.exit(1)
    
    for s in config.MONITOR_DEVICES:
        tid, ttype = s.get("id"), s.get("type")
        if not (ttype.startswith("Plug") or ttype.startswith("Meter")): continue
        
        tname = sb_tool.get_device_name_by_id(tid) or "Unknown"
        data = fetch_device_data(tid, ttype)
        
        if data:
            pw = data.get('power')
            tc = data.get('temperature')
            hp = data.get('humidity')
            th = s.get("notify_settings", {}).get("power_threshold_watts")
            
            insert_power_record(tname, tid, ttype, pw, tc, hp, th)

            mode = s.get("notify_settings", {}).get("notify_mode", "LOG_ONLY")
            if pw is not None and th is not None and mode != "LOG_ONLY":
                prev = get_prev_power(tid)
                msg = None
                if mode == "ON_START" and pw >= th and prev < th:
                    msg = f"🍚【炊飯通知】\n{tname} が動き出したよ！ ({pw}W)"
                elif mode == "ON_END_SUMMARY" and pw < th and prev >= th:
                    msg = f"💡【使用終了】\n{tname} の電源が切れたみたい"
                elif mode == "CONTINUOUS" and pw >= th:
                    msg = f"🚨【電力アラート】\n{tname} がまだついてるよ！ ({pw}W)"
                
                if msg:
                    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord")
                    logger.info(f"通知送信: {tname}")
    logger.info("=== チェック完了 ===")