# HOME_SYSTEM/common.py
import sqlite3
import requests
import json
import datetime
import pytz
import logging
import traceback
from typing import List, Any, Optional, Union
from contextlib import contextmanager
import config

# === ロギング設定 ===
class DiscordErrorHandler(logging.Handler):
    """エラーログをDiscordに通知するハンドラ"""
    def emit(self, record):
        # ERROR以上のみ、かつ自分自身のログ（再帰防止）でない場合
        if record.levelno >= logging.ERROR and "Discord" not in record.msg:
            try:
                msg = self.format(record)
                # エラー専用Webhookを使用
                url = config.DISCORD_WEBHOOK_ERROR
                if url:
                    payload = {"content": f"😰 **システムエラー発生**\n```{msg[:1800]}```"} # 2000文字制限対策
                    requests.post(url, json=payload, timeout=5)
            except Exception:
                # ここでのエラーは握りつぶす（無限ループ防止）
                pass

def setup_logging(name: str) -> logging.Logger:
    """ロガーのセットアップ"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # ハンドラが重複しないようにクリア
    if logger.handlers:
        logger.handlers.clear()
    
    # 標準出力
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    # Discord通知 (エラー時)
    if config.DISCORD_WEBHOOK_ERROR:
        discord_handler = DiscordErrorHandler()
        discord_handler.setFormatter(formatter)
        discord_handler.setLevel(logging.ERROR)
        logger.addHandler(discord_handler)
    
    # 外部ライブラリのノイズ抑制
    logging.getLogger("zeep").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    return logger

logger = setup_logging("common")

# === データベース関連 ===
@contextmanager
def get_db_cursor(commit: bool = False):
    """DB接続コンテキストマネージャ"""
    conn = None
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        yield conn.cursor()
        if commit:
            conn.commit()
    except Exception as e:
        logger.error(f"データベース操作エラー: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def save_log_generic(table: str, columns_list: List[str], values_list: tuple) -> bool:
    """汎用データ保存関数"""
    with get_db_cursor(commit=True) as cur:
        if cur:
            try:
                placeholders = ", ".join(["?"] * len(values_list))
                columns = ", ".join(columns_list)
                sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
                cur.execute(sql, values_list)
                return True
            except Exception as e:
                logger.error(f"データ保存失敗 ({table}): {e}")
    return False

# === 通知関連 ===
def _send_discord_webhook(messages: List[dict], image_data: bytes = None, channel: str = "notify") -> bool:
    """DiscordへのWebhook送信"""
    # チャンネル振り分け
    if channel == "error":
        url = config.DISCORD_WEBHOOK_ERROR
    elif channel == "report":
        url = config.DISCORD_WEBHOOK_REPORT
    else:
        url = config.DISCORD_WEBHOOK_NOTIFY or config.DISCORD_WEBHOOK_URL
    
    if not url:
        logger.warning(f"Discord Webhook URL未設定 (channel={channel})")
        return False
    
    # テキスト結合
    text_content = ""
    for msg in messages:
        # LINE形式のメッセージオブジェクトからテキストを抽出
        text = msg.get("text") or msg.get("altText") or "（画像またはスタンプ）"
        text_content += f"{text}\n\n"
    
    try:
        if image_data:
            files = {'file': ('snapshot.jpg', image_data, 'image/jpeg')}
            res = requests.post(url, files=files, data={'content': text_content}, timeout=10)
        else:
            res = requests.post(url, json={"content": text_content}, timeout=10)
        
        if res.status_code in [200, 204]:
            return True
        else:
            logger.error(f"Discord API Error: {res.status_code} {res.text}")
            return False
    except Exception as e:
        logger.error(f"Discord送信失敗: {e}")
        return False

def _send_line_push(user_id: str, messages: List[dict]) -> bool:
    """LINE Push API送信"""
    if not config.LINE_CHANNEL_ACCESS_TOKEN:
        logger.error("LINE Token未設定")
        return False

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"to": user_id, "messages": messages}
    
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if res.status_code != 200:
            logger.error(f"LINE API Error: {res.status_code} {res.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE接続エラー: {e}")
        return False

def send_push(user_id: str, messages: List[dict], image_data: bytes = None, target: str = None, channel: str = "notify") -> bool:
    """
    プッシュ通知送信の統合関数
    :param target: 'line' or 'discord'. Noneの場合はconfig.NOTIFICATION_TARGETを使用
    """
    if target is None:
        target = config.NOTIFICATION_TARGET

    if target.lower() == "discord":
        return _send_discord_webhook(messages, image_data, channel)
    else:
        if image_data:
            logger.warning("LINEへの画像直接送信は未実装です (Discordへフォールバックします)")
            _send_discord_webhook(messages, image_data, channel)
            messages.append({"type": "text", "text": "※画像はDiscordに送信しました"})
        
        return _send_line_push(user_id, messages)

def send_reply(reply_token: str, messages: List[dict]) -> bool:
    """LINE Reply API送信"""
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json", 
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"replyToken": reply_token, "messages": messages}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if res.status_code != 200:
            logger.error(f"LINE Reply Error: {res.status_code} {res.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE Reply 接続エラー: {e}")
        return False

# === ユーティリティ ===
def get_now_iso() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date_str() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")