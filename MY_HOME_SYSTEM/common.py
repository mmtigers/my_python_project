# HOME_SYSTEM/common.py
import sqlite3
import requests
import json
import datetime
import pytz
import config

# === データベース関連 (変更なし) ===
def get_db_connection():
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"[FATAL] DB接続エラー: {e}")
        return None

def save_log_generic(table, columns_list, values_list):
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

# === 通知関連 (LINE / Discord 自動振り分け) ===
def send_line_push(user_id, messages):
    """
    通知メッセージを送信する共通関数
    config.NOTIFICATION_TARGET の設定に従って LINE または Discord に送信します。
    """
    target = getattr(config, "NOTIFICATION_TARGET", "line")

    if target == "discord":
        return _send_discord_webhook(messages)
    else:
        return _send_line_push_api(user_id, messages)

def send_line_reply(reply_token, messages):
    """
    返信メッセージ（LINE専用）
    ※Discordには「返信」という概念がWebhookにないため、これはLINE専用として残します
    """
    return _send_line_reply_api(reply_token, messages)


# --- 内部関数: LINE送信 ---
def _send_line_push_api(user_id, messages):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"to": user_id, "messages": messages}
    return _post_api(url, headers, payload, "LINE Push")

def _send_line_reply_api(reply_token, messages):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"replyToken": reply_token, "messages": messages}
    return _post_api(url, headers, payload, "LINE Reply")

# --- 内部関数: Discord送信 ---
def _send_discord_webhook(messages):
    """LINE形式のメッセージオブジェクトをDiscord形式に変換して送信"""
    url = config.DISCORD_WEBHOOK_URL
    if not url:
        print("[ERROR] Discord Webhook URLが設定されていません")
        return False

    # LINEのメッセージ(リスト)を、Discordのテキストに変換して結合
    content_list = []
    for msg in messages:
        if msg.get("type") == "text":
            content_list.append(msg.get("text", ""))
        # 必要ならスタンプや画像などの変換ロジックもここに追加可能
    
    full_content = "\n\n".join(content_list)
    
    payload = {
        "content": full_content
    }
    # DiscordはヘッダーなしでもJSONなら通るが、念のため
    headers = {"Content-Type": "application/json"}
    
    return _post_api(url, headers, payload, "Discord")

# --- 内部関数: 共通POST処理 ---
def _post_api(url, headers, payload, action_name):
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        # Discordは204 No Contentが成功ステータスの場合がある
        if response.status_code not in [200, 204]:
            print(f"[ERROR] {action_name} 失敗: {response.status_code} {response.text}")
            return False
        return True
    except Exception as e:
        print(f"[ERROR] {action_name} 接続エラー: {e}")
        return False

# === 日時関連 (変更なし) ===
def get_now_iso():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date_str():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")