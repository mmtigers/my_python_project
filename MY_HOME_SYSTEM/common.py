# HOME_SYSTEM/common.py
import sqlite3
import requests
import json
import datetime
import pytz
import config
import logging
from contextlib import contextmanager

# === ログ設定共通化 ===
def setup_logging(name=None):
    """全スクリプト共通のログ設定"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # 外部ライブラリのログを抑制
    logging.getLogger("zeep").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger(name)

logger = setup_logging("common")

# === データベース関連 ===
def get_db_connection():
    """(旧) DB接続を取得する"""
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"DB接続エラー: {e}")
        return None

@contextmanager
def get_db_cursor(commit=False):
    """(新) 安全なDB接続用コンテキストマネージャ (with句で使える)"""
    conn = get_db_connection()
    if not conn:
        yield None
        return
    try:
        yield conn.cursor()
        if commit:
            conn.commit()
    except Exception as e:
        logger.error(f"DB操作エラー: {e}")
    finally:
        conn.close()

def save_log_generic(table, columns_list, values_list):
    """汎用ログ保存関数"""
    with get_db_cursor(commit=True) as cur:
        if cur:
            placeholders = ", ".join(["?"] * len(values_list))
            columns = ", ".join(columns_list)
            sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
            cur.execute(sql, values_list)
            return True
    return False

# === 通知関連 ===
def send_push(user_id, messages):
    target = getattr(config, "NOTIFICATION_TARGET", "line")
    if target == "discord":
        return _send_discord_webhook(messages)
    else:
        return _send_line_api("push", {"to": user_id, "messages": messages})

def send_reply(reply_token, messages):
    return _send_line_api("reply", {"replyToken": reply_token, "messages": messages})

def _send_line_api(endpoint, payload):
    url = f"https://api.line.me/v2/bot/message/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        if res.status_code != 200:
            logger.error(f"LINE {endpoint} 失敗: {res.status_code} {res.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE接続エラー: {e}")
        return False

def _send_discord_webhook(messages):
    url = config.DISCORD_WEBHOOK_URL
    if not url:
        logger.error("Discord URL未設定")
        return False
    
    text_content = ""
    for msg in messages:
        text = msg.get("text") or msg.get("altText") or "（スタンプ/画像）"
        text_content += f"{text}\n\n"
    
    try:
        res = requests.post(url, json={"content": text_content})
        if res.status_code not in [200, 204]:
            logger.error(f"Discord失敗: {res.status_code}")
            return False
        return True
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