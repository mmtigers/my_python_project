# HOME_SYSTEM/common.py
import sqlite3
import requests
import json
import datetime
import pytz
import config

# === データベース関連 ===
def get_db_connection():
    """データベース接続を取得する (Rowファクトリ付き)"""
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"[FATAL] DB接続エラー: {e}")
        return None

def save_log_generic(table, columns_list, values_list):
    """汎用ログ保存関数"""
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

# === メッセージ送信関連 ===
def send_push(user_id, messages):
    """
    プッシュ通知（能動的な送信）
    設定(NOTIFICATION_TARGET)に従い、LINE または Discord に送信する。
    ※LINEの場合、月間送信数制限の対象。
    """
    target = getattr(config, "NOTIFICATION_TARGET", "line")

    if target == "discord":
        return _send_discord_webhook(messages)
    else:
        return _send_line_api("push", {"to": user_id, "messages": messages})

def send_reply(reply_token, messages):
    """
    返信メッセージ（応答）
    ※LINEの仕様上、Replyは無料・無制限のため、常にLINEに送信する。
    """
    return _send_line_api("reply", {"replyToken": reply_token, "messages": messages})

# --- 内部関数 ---
def _send_line_api(endpoint, payload):
    url = f"https://api.line.me/v2/bot/message/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        if res.status_code != 200:
            print(f"[ERROR] LINE {endpoint} 失敗: {res.status_code} {res.text}")
            return False
        return True
    except Exception as e:
        print(f"[ERROR] LINE接続エラー: {e}")
        return False

def _send_discord_webhook(messages):
    url = config.DISCORD_WEBHOOK_URL
    if not url:
        print("[ERROR] Discord URL未設定")
        return False
    
    # メッセージリストをテキストに結合
    text_content = ""
    for msg in messages:
        text = msg.get("text") or msg.get("altText") or "（スタンプ/画像）"
        text_content += f"{text}\n\n"
    
    try:
        res = requests.post(url, json={"content": text_content})
        if res.status_code not in [200, 204]:
            print(f"[ERROR] Discord失敗: {res.status_code}")
            return False
        return True
    except Exception as e:
        print(f"[ERROR] Discord接続エラー: {e}")
        return False

# === ユーティリティ ===
def get_now_iso():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")