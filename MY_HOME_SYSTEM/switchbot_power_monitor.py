# HOME_SYSTEM/switchbot_power_monitor.py
import requests
import sys
import common
import config
import switchbot_get_device_list as sb_tool

# ロガー設定
logger = common.setup_logging("device_monitor")

def insert_device_record(name, device_id, device_type, data):
    """
    デバイスのステータスをDBに記録する
    """
    cols = ["timestamp", "device_name", "device_id", "device_type", 
            "power_watts", "temperature_celsius", "humidity_percent", 
            "contact_state", "movement_state", "brightness_state", "threshold_watts"]
    
    threshold = data.get('threshold')
    
    vals = (
        common.get_now_iso(), 
        name, 
        device_id, 
        device_type, 
        data.get('power'), 
        data.get('temperature'), 
        data.get('humidity'),
        data.get('contact'),
        data.get('motion'),
        data.get('brightness'),
        threshold
    )
    
    if common.save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals):
        # ログ出力用メッセージ作成
        log_parts = []
        if data.get('power') is not None: log_parts.append(f"{data['power']}W")
        if data.get('temperature') is not None: log_parts.append(f"{data['temperature']}°C")
        if data.get('contact'): log_parts.append(f"開閉:{data['contact']}")
        if data.get('motion'): log_parts.append(f"動き:{data['motion']}")
        
        log_msg = ", ".join(log_parts) if log_parts else "No Data"
        logger.info(f"記録: {name} -> {log_msg}")

def calculate_plug_power(body):
    """プラグの電力を計算する（0W補正付き）"""
    watts = float(body.get('weight', 0))
    
    # 0Wの場合、電圧×電流で再計算（APIの仕様による補正）
    if watts == 0:
        volts = float(body.get('voltage', 0))
        # APIのelectricCurrentはmA単位の場合があるため Aに変換
        amps = float(body.get('electricCurrent', 0)) / 1000.0
        if volts > 0 and amps > 0:
            watts = volts * amps
            
    return round(watts, 1)

def fetch_device_status(device_id, device_type):
    """APIからデバイスの状態を取得して辞書で返す"""
    url = f"https://api.switch-bot.com/v1.1/devices/{device_id}/status"
    try:
        headers = sb_tool.create_switchbot_auth_headers()
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        
        if data.get('statusCode') != 100:
            logger.warning(f"API Error [{device_id}]: {data}")
            return None

        body = data.get('body', {})
        result = {}
        
        # デバイスタイプ別のデータ抽出
        if "Plug" in device_type:
            result['power'] = calculate_plug_power(body)

        elif "Meter" in device_type:
            result['temperature'] = float(body.get('temperature', 0))
            result['humidity'] = float(body.get('humidity', 0))

        elif "Contact" in device_type:
            result['contact'] = body.get('openState', 'unknown') # open, close, timeOutNotClose
            result['brightness'] = body.get('brightness', 'unknown')

        elif "Motion" in device_type:
            result['motion'] = "detected" if body.get('moveDetected') else "clear"
            result['brightness'] = body.get('brightness', 'unknown')
        
        return result

    except Exception as e:
        logger.error(f"[{device_id}] ステータス取得失敗: {e}")
        return None

def get_prev_power(device_id):
    """DBから前回の電力値を取得"""
    with common.get_db_cursor() as cur:
        if not cur: return 0.0
        try:
            sql = f"SELECT power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id=? ORDER BY id DESC LIMIT 1"
            cur.execute(sql, (device_id,))
            row = cur.fetchone()
            return row["power_watts"] if row and row["power_watts"] is not None else 0.0
        except Exception:
            return 0.0

def process_power_notification(name, device_id, current_power, settings):
    """電力に基づく通知判定を行う"""
    threshold = settings.get("power_threshold_watts")
    mode = settings.get("notify_mode", "LOG_ONLY")
    # 設定でターゲット指定があれば優先、なければデフォルト
    target = settings.get("target", config.NOTIFICATION_TARGET)

    if threshold is None or mode == "LOG_ONLY":
        return

    prev_power = get_prev_power(device_id)
    msg = None

    # 通知ロジック
    if mode == "ON_START" and current_power >= threshold and prev_power < threshold:
        msg = f"🍚【炊飯通知】\n{name} が動き出したよ！ ({current_power}W)"
    
    elif mode == "ON_END_SUMMARY" and current_power < threshold and prev_power >= threshold:
        msg = f"💡【使用終了】\n{name} の電源が切れたみたい"
    
    elif mode == "CONTINUOUS" and current_power >= threshold:
        msg = f"🚨【電力アラート】\n{name} がまだついてるよ！ ({current_power}W)"
        # アラート系は強制的にDiscordにも送りたい場合はここで制御可能
        # target = "discord" 

    if msg:
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target=target)
        logger.info(f"通知送信 ({target}): {name}")

def main():
    logger.info("=== 全デバイス定期チェック開始 ===")
    
    # デバイス名のキャッシュ更新
    if not sb_tool.fetch_device_name_cache():
        logger.error("デバイスリストの取得に失敗したため中断します")
        sys.exit(1)
    
    for s in config.MONITOR_DEVICES:
        try:
            tid = s.get("id")
            ttype = s.get("type")
            tname = s.get("name") or sb_tool.get_device_name_by_id(tid) or "Unknown"
            
            # データ取得
            data = fetch_device_status(tid, ttype)
            
            if data:
                # 閾値情報の付与
                notify_settings = s.get("notify_settings", {})
                data['threshold'] = notify_settings.get("power_threshold_watts")
                
                # DB記録
                insert_device_record(tname, tid, ttype, data)

                # プラグなら通知判定
                if "Plug" in ttype and data.get('power') is not None:
                    process_power_notification(tname, tid, data['power'], notify_settings)
                    
        except Exception as e:
            logger.error(f"デバイス処理エラー [{tname}]: {e}")
            continue

    logger.info("=== チェック完了 ===")

if __name__ == "__main__":
    main()