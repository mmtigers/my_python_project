# HOME_SYSTEM/switchbot_power_monitor.py
import requests
import sys
import common
import config
import switchbot_get_device_list as sb_tool

def insert_power_record(name, device_id, device_type, power_w, temp_c, humidity_p, threshold_w):
    cols = ["timestamp", "device_name", "device_id", "device_type", "power_watts", "temperature_celsius", 
            "humidity_percent", "threshold_watts"]
    vals = (common.get_now_iso(), name, device_id, device_type, power_w, temp_c, humidity_p, threshold_w)
    
    if common.save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals):
        log_parts = []
        if power_w is not None: log_parts.append(f"{power_w:.1f}W")
        if temp_c is not None: log_parts.append(f"{temp_c:.1f}°C")
        print(f"[SUCCESS] 記録: {name} -> {', '.join(log_parts)}")

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

if __name__ == "__main__":
    print(f"\n=== SwitchBot 定期監視 ({common.get_now_iso()}) ===")
    
    if not sb_tool.fetch_device_name_cache():
        sys.exit(1)
        
    device_settings_list = config.MONITOR_DEVICES
    
    for setting in device_settings_list:
        target_id = setting.get("id")
        target_type = setting.get("type")
        notify_conf = setting.get("notify_settings", {})
        threshold = notify_conf.get("power_threshold_watts")
        mode = notify_conf.get("notify_mode", "LOG_ONLY") # デフォルトは静かに記録のみ

        if not (target_type.startswith("Plug") or target_type.startswith("Meter")):
            continue

        target_name = sb_tool.get_device_name_by_id(target_id) or "Unknown"
        data = fetch_device_data(target_id, target_type)
        
        if data:
            p_w = data.get('power')
            t_c = data.get('temperature')
            h_p = data.get('humidity')
            
            insert_power_record(target_name, target_id, target_type, p_w, t_c, h_p, threshold)
            
            # LOG_ONLYの場合はここで通知処理をスキップ（これが節約の肝）
            if mode == "LOG_ONLY":
                continue

            # 以下、CONTINUOUSなどの旧設定用（必要なら残す）
            if p_w is not None and threshold is not None and mode == "CONTINUOUS":
                if p_w >= threshold:
                    msg = {"type": "text", "text": f"🚨【電力アラート】\n{target_name} が {p_w:.1f}W を記録しました"}
                    common.send_line_push(config.LINE_USER_ID, [msg])
                    print(f"[ALERT] 送信: {target_name}")

    print("=== 完了 ===\n")