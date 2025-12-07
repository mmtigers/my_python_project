# HOME_SYSTEM/common.py
import sqlite3
import requests
import json
import datetime
import pytz
import config
import logging
from contextlib import contextmanager

# === カスタムログハンドラー ===
class DiscordErrorHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            try:
                msg = self.format(record)
                if "Discord" in msg: return
                url = config.DISCORD_WEBHOOK_URL
                if url:
                    payload = {"content": f"😰 **システムエラー発生**\n```{msg}```"}
                    requests.post(url, json=payload, timeout=5)
            except: pass

# === ログ設定 ===
def setup_logging(name=None):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    if logger.handlers: logger.handlers = []
    
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    if config.DISCORD_WEBHOOK_URL:
        discord_handler = DiscordErrorHandler()
        discord_handler.setFormatter(formatter)
        discord_handler.setLevel(logging.ERROR)
        logger.addHandler(discord_handler)
    
    logging.getLogger("zeep").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    return logger

logger = setup_logging("common")

# === データベース関連 ===
@contextmanager
def get_db_cursor(commit=False):
    """安全なDB接続用コンテキストマネージャ"""
    conn = None
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        yield conn.cursor()
        if commit: conn.commit()
    except Exception as e:
        logger.error(f"データベースエラー: {e}")
    finally:
        if conn: conn.close()

def get_db_connection():
    """旧互換用: DB接続を取得"""
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"DB接続エラー: {e}")
        return None

def save_log_generic(table, columns_list, values_list):
    with get_db_cursor(commit=True) as cur:
        if cur:
            try:
                placeholders = ", ".join(["?"] * len(values_list))
                columns = ", ".join(columns_list)
                sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
                cur.execute(sql, values_list)
                return True
            except Exception as e:
                logger.error(f"データ保存失敗: {e}")
    return False

def get_device_location(device_id):
    """
    デバイスIDから設定された場所(location)を取得する。
    見つからない場合は '伊丹' (デフォルト) を返す。
    """
    # SwitchBot/NatureRemoデバイス
    for d in config.MONITOR_DEVICES:
        if d.get("id") == device_id:
            return d.get("location", "伊丹")
            
    # カメラデバイス
    if hasattr(config, "CAMERAS"):
        for c in config.CAMERAS:
            if c.get("id") == device_id:
                return c.get("location", "伊丹")
                
    return "伊丹"

# === 通知関連 ===
def send_push(user_id, messages, image_data=None, target=None):
    if target is None:
        target = getattr(config, "NOTIFICATION_TARGET", "line")

    if target == "discord":
        return _send_discord_webhook(messages, image_data)
    else:
        if image_data: logger.warning("LINEへの画像送信は未対応です")
        return _send_line_api("push", {"to": user_id, "messages": messages})

def send_reply(reply_token, messages):
    return _send_line_api("reply", {"replyToken": reply_token, "messages": messages})

def _send_line_api(endpoint, payload):
    url = f"https://api.line.me/v2/bot/message/{endpoint}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if res.status_code != 200:
            logger.error(f"LINEエラー: {res.status_code} {res.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE接続エラー: {e}")
        return False

def _send_discord_webhook(messages, image_data=None):
    url = config.DISCORD_WEBHOOK_URL
    if not url: return False
    
    text_content = ""
    for msg in messages:
        text = msg.get("text") or msg.get("altText") or ""
        text_content += f"{text}\n\n"
    
    try:
        if image_data:
            files = {'file': ('snapshot.jpg', image_data, 'image/jpeg')}
            res = requests.post(url, files=files, data={'content': text_content}, timeout=10)
        else:
            res = requests.post(url, json={"content": text_content}, timeout=10)
        return res.status_code in [200, 204]
    except Exception as e:
        logger.error(f"Discord接続エラー: {e}")
        return False

# === ユーティリティ ===
def get_now_iso():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date_str():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")