# HOME_SYSTEM/common.py
import sqlite3
import requests
import json
import datetime
import pytz
import config
import logging
from contextlib import contextmanager

# === カスタムログハンドラー: Discordへエラー通知 ===
class DiscordErrorHandler(logging.Handler):
    def emit(self, record):
        # エラー以上の場合のみ通知
        if record.levelno >= logging.ERROR:
            try:
                msg = self.format(record)
                # 無限ループ防止（自身の送信エラーは無視）
                if "Discord" in msg: return
                
                # エラー通知は見やすく整形
                url = config.DISCORD_WEBHOOK_URL
                if url:
                    payload = {"content": f"😰 **システムエラー発生**\n```{msg}```"}
                    requests.post(url, json=payload, timeout=5)
            except:
                pass

# === ログ設定 ===
def setup_logging(name=None):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # ハンドラーが重複しないようにクリア
    if logger.handlers:
        logger.handlers = []
    
    # 1. コンソール出力
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    # 2. Discordエラー通知 (要件対応)
    if config.DISCORD_WEBHOOK_URL:
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
def get_db_cursor(commit=False):
    conn = None
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        yield conn.cursor()
        if commit: conn.commit()
    except Exception as e:
        logger.error(f"データベースの調子が悪いみたい💦: {e}")
    finally:
        if conn: conn.close()

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
                logger.error(f"データの保存に失敗しちゃった: {e}")
    return False

# === 通知関連 ===
def send_push(user_id, messages, image_data=None, target=None):
    """通知送信 (LINE/Discord自動振り分け)"""
    if target is None:
        target = getattr(config, "NOTIFICATION_TARGET", "line")

    if target == "discord":
        return _send_discord_webhook(messages, image_data)
    else:
        if image_data:
            logger.warning("LINEへの画像送信はまだ勉強中なの...ごめんね🙏 (テキストのみ送ります)")
        return _send_line_api("push", {"to": user_id, "messages": messages})

def send_reply(reply_token, messages):
    """返信 (常にLINE)"""
    return _send_line_api("reply", {"replyToken": reply_token, "messages": messages})

def _send_line_api(endpoint, payload):
    url = f"https://api.line.me/v2/bot/message/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if res.status_code != 200:
            logger.error(f"LINE({endpoint})に送れなかったわ...: {res.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINEとの接続がおかしいみたい: {e}")
        return False

def _send_discord_webhook(messages, image_data=None):
    url = config.DISCORD_WEBHOOK_URL
    if not url:
        logger.error("Discordのアドレスが設定されてないよ！")
        return False
    
    text_content = ""
    for msg in messages:
        text = msg.get("text") or msg.get("altText") or "（スタンプ/画像）"
        text_content += f"{text}\n\n"
    
    try:
        if image_data:
            files = {'file': ('snapshot.jpg', image_data, 'image/jpeg')}
            data = {'content': text_content}
            res = requests.post(url, files=files, data=data, timeout=10)
        else:
            res = requests.post(url, json={"content": text_content}, timeout=10)

        if res.status_code not in [200, 204]:
            logger.error(f"Discordへの送信失敗: {res.status_code}")
            return False
        return True
    except Exception as e:
        logger.error(f"Discord送信エラー: {e}")
        return False

# === ユーティリティ ===
def get_now_iso():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date_str():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")