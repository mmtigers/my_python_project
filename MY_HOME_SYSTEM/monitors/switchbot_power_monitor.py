# MY_HOME_SYSTEM/monitors/switchbot_power_monitor.py
import requests
import sys
import logging
from typing import Dict, Any, Optional, List, Union, Tuple

# 自作モジュール
import config
from services import switchbot_service as sb_tool
# import common <-- 削除
from core.logger import setup_logging
from core.database import save_log_generic, get_db_cursor
from core.utils import get_now_iso
from services.notification_service import send_push

# ロガー設定
logger = setup_logging("device_monitor")

def insert_device_record(name: str, device_id: str, device_type: str, data: Dict[str, Any]) -> None:
    """
    デバイスのステータスをDBに記録する
    """
    cols: List[str] = [
        "timestamp", "device_name", "device_id", "device_type", 
        "power_watts", "temperature_celsius", "humidity_percent", 
        "contact_state", "movement_state", "brightness_state", "threshold_watts"
    ]
    
    threshold: Optional[float] = data.get('threshold')
    
    vals: Tuple[Any, ...] = (
        get_now_iso(), 
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
    
    if save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals):
        # ログ出力用メッセージ作成
        log_parts: List[str] = []
        if data.get('power') is not None: 
            log_parts.append(f"{data['power']}W")
        if data.get('temperature') is not None: 
            log_parts.append(f"{data['temperature']}°C")
        if data.get('contact'): 
            log_parts.append(f"開閉:{data['contact']}")
        if data.get('motion'): 
            log_parts.append(f"動き:{data['motion']}")
        
        log_msg = ", ".join(log_parts) if log_parts else "No Data"
        logger.info(f"記録: {name} -> {log_msg}")

def calculate_plug_power(body: Dict[str, Any]) -> float:
    """プラグの電力を計算する（0W補正付き）"""
    watts: float = float(body.get('weight', 0))
    
    # 0Wの場合、電圧×電流で再計算（APIの仕様による補正）
    if watts == 0:
        volts: float = float(body.get('voltage', 0))
        # APIのelectricCurrentはmA単位の場合があるため Aに変換
        amps: float = float(body.get('electricCurrent', 0)) / 1000.0
        if volts > 0 and amps > 0:
            watts = volts * amps
            
    return round(watts, 1)

def fetch_device_status(device_id: str, device_type: str) -> Optional[Dict[str, Any]]:
    """APIからデバイスの状態を取得して辞書で返す"""
    url: str = f"https://api.switch-bot.com/v1.1/devices/{device_id}/status"
    try:
        headers = sb_tool.create_switchbot_auth_headers()
        data = sb_tool.request_switchbot_api(url, headers)
        
        if data.get('statusCode') != 100:
            logger.warning(f"API Error [{device_id}]: {data}")
            return None

        body: Dict[str, Any] = data.get('body', {})
        result: Dict[str, Any] = {}
        
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


    except requests.exceptions.Timeout:
        # タイムアウトはWARNINGレベルに留める（Discord通知しない）
        logger.warning(f"[{device_id}] ステータス取得タイムアウト (API遅延)")
        return None
    except Exception as e:
        # その他の予期せぬエラーはERRORレベル
        logger.error(f"[{device_id}] ステータス取得失敗: {e}")
        return None 

def get_prev_power(device_id: str) -> float:
    """DBから前回の電力値を取得"""
    with get_db_cursor() as cur:
        if not cur: 
            return 0.0
        try:
            sql = f"SELECT power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id=? ORDER BY id DESC LIMIT 1"
            cur.execute(sql, (device_id,))
            row = cur.fetchone()
            if row:
                val = row["power_watts"] if isinstance(row, (dict, list)) or hasattr(row, "__getitem__") else row[0]
                return float(val) if val is not None else 0.0
            return 0.0
        except Exception:
            return 0.0

def process_power_notification(name: str, device_id: str, current_power: float, settings: Dict[str, Any], location: str) -> None:
    """電力に基づく通知判定を行う"""
    threshold: Optional[float] = settings.get("power_threshold_watts")
    mode: str = settings.get("notify_mode", "LOG_ONLY")
    target: str = settings.get("target", config.NOTIFICATION_TARGET)

    if threshold is None or mode == "LOG_ONLY":
        return

    prev_power: float = get_prev_power(device_id)
    msg: Optional[str] = None

    # 通知ロジック
    if mode == "ON_START" and current_power >= threshold and prev_power < threshold:
        msg = f"🍚【炊飯通知】\n{name} が動き出したよ！ ({current_power}W)"
    
    elif mode == "ON_END_SUMMARY" and current_power < threshold and prev_power >= threshold:
        msg = f"💡【使用終了】\n{name} の電源が切れたみたい"
    
    elif mode == "CONTINUOUS" and current_power >= threshold:
        msg = f"🚨【電力アラート】\n{name} がまだついてるよ！ ({current_power}W)"

    if msg:
        # common.send_push -> send_push
        send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target=target)
        logger.info(f"通知送信 ({target}): {name}")

def main() -> None:
    logger.info("=== 全デバイス定期チェック開始 ===")
    
    # デバイス名のキャッシュ更新
    if not sb_tool.fetch_device_name_cache():
        logger.warning("デバイスリスト取得失敗。config定義名を使用して継続します。")
    # sys.exit(1) を削除し、処理を続行させる
    
    # config.MONITOR_DEVICES は List[Dict] を想定
    for s in config.MONITOR_DEVICES:
        try:
            tid: str = s.get("id", "")
            ttype: str = s.get("type", "")
            
            # ▼▼▼ 修正: 名前解決の優先順位変更 (API > Config > Unknown) ▼▼▼
            api_name = sb_tool.get_device_name_by_id(tid)
            config_name = s.get("name")
            tname: str = api_name or config_name or "Unknown"
            # ▲▲▲ 修正終了 ▲▲▲
            
            tloc: str = s.get("location", "家") 
            
            if not tid or not ttype:
                continue

            # データ取得
            data = fetch_device_status(tid, ttype)
            
            if data:
                # 閾値情報の付与
                notify_settings: Dict[str, Any] = s.get("notify_settings", {})
                data['threshold'] = notify_settings.get("power_threshold_watts")
                
                # DB記録 (ここで最新の tname が保存される)
                insert_device_record(tname, tid, ttype, data)

                # プラグなら通知判定
                if "Plug" in ttype and data.get('power') is not None:
                    process_power_notification(tname, tid, float(data['power']), notify_settings, tloc)
                    
        except Exception as e:
            logger.error(f"デバイス処理エラー [{s.get('name', 'Unknown')}]: {e}")
            continue

    logger.info("=== チェック完了 ===")

if __name__ == "__main__":
    main()