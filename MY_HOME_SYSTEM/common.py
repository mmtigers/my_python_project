# HOME_SYSTEM/common.py
import sqlite3
import requests
import json
import datetime
import pytz
import config

# === データベース関連 ===
def get_db_connection():
    """データベース接続を取得する共通関数 (タイムアウト設定済み)"""
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row  # カラム名でアクセスできるようにする
        return conn
    except Exception as e:
        print(f"[FATAL] DB接続エラー: {e}")
        return None

def save_log_generic(table, columns_list, values_list):
    """汎用的なログ保存関数"""
    conn = get_db_connection()
    if not conn: return False
    
    try:
        placeholders = ", ".join(["?"] * len(values_list))
        columns = ", ".join(columns_list)
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        
        conn.execute(sql, values_list)
        conn.commit()
        return True
    except Exception as e:
        print(f"[ERROR] DB保存失敗 ({table}): {e}")
        return False
    finally:
        conn.close()

# === LINE API関連 (Direct) ===
def send_line_reply(reply_token, messages):
    """LINE Reply API (Direct)"""
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"replyToken": reply_token, "messages": messages}
    return _post_line_api(url, headers, payload, "Reply")

def send_line_push(user_id, messages):
    """LINE Push API (Direct)"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"to": user_id, "messages": messages}
    return _post_line_api(url, headers, payload, "Push")

def _post_line_api(url, headers, payload, action_name):
    """内部用: API送信実行"""
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200:
            print(f"[ERROR] LINE {action_name} 失敗: {response.status_code} {response.text}")
            return False
        return True
    except Exception as e:
        print(f"[ERROR] LINE {action_name} 接続エラー: {e}")
        return False

# === 日時関連 ===
def get_now_iso():
    """現在時刻(ISO形式)を取得"""
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date_str():
    """今日の日付(YYYY-MM-DD)を取得"""
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date():
    """表示用日付(MM/DD)を取得"""
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")