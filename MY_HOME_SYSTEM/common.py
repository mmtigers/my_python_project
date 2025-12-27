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
from linebot.exceptions import LineBotApiError
from linebot import LineBotApi
from linebot.exceptions import LineBotApiError
from requests.adapters import HTTPAdapter # 追加
from urllib3.util.retry import Retry # 追加

# LineBotApiの初期化
if config.LINE_CHANNEL_ACCESS_TOKEN:
    line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
else:
    line_bot_api = None

    
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

# === データベース関連 (強化版) ===
@contextmanager
def get_db_cursor(commit: bool = False):
    """DB接続コンテキストマネージャ (リトライ機能付き)"""
    conn = None
    max_retries = 5
    retry_delay = 1.0

    for attempt in range(max_retries):
        try:
            # timeoutを長めに設定 (デフォルトは5秒だが、並列処理が多い場合は20-30秒推奨)
            conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=30.0)
            conn.row_factory = sqlite3.Row
            
            # WALモード有効化 (同時実行性能の向上) - 毎回呼んでも低コスト
            conn.execute("PRAGMA journal_mode=WAL;")
            
            yield conn.cursor()
            
            if commit:
                conn.commit()
            break # 成功したらループを抜ける

        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                # ロックエラーなら待機してリトライ
                logger.warning(f"⚠️ DB is locked. Retrying... ({attempt+1}/{max_retries})")
                if conn:
                    conn.close()
                time.sleep(retry_delay)
            else:
                # その他のエラーは即座にraise
                logger.error(f"データベース操作エラー: {e}")
                if conn: conn.rollback()
                raise e
        except Exception as e:
            logger.error(f"予期せぬDBエラー: {e}")
            if conn: conn.rollback()
            raise e
    else:
        # ループがbreakされずに終了した場合（リトライ上限）
        logger.error("❌ DB Retry limit reached.")
        if conn: conn.close()

    # finallyでのcloseは、成功時のみ行う (yield先でエラーが出た場合もcloseされるようcontextmanagerの仕様に委ねるが、明示的に書く)
    if conn:
        try:
            conn.close()
        except:
            pass

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

# === 通信関連 (新規追加) ===
def get_retry_session(retries=3, backoff_factor=1.0):
    """リトライ機能付きのRequestsセッションを作成"""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

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
        # リトライセッションを使用
        session = get_retry_session()
        res = session.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        
        if res.status_code != 200:
            logger.error(f"LINE API Error: {res.status_code} {res.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE接続エラー: {e}")
        return False    

def send_push(user_id: str, messages: List[dict], image_data: bytes = None, target: str = "discord", channel: str = "notify") -> bool:
    """     
    メッセージを送信するラッパー関数
    - target='line': LINEに送信 (失敗時、429エラーならDiscordへフォールバック)
    - target='discord': Discordに送信
    """
    if target is None:
        target = config.NOTIFICATION_TARGET
    
    if target.lower() == 'line':
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
            }
            data = {
                "to": user_id,
                "messages": messages
            }
            # リトライセッションを使用
            session = get_retry_session()
            response = session.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data, timeout=10)
            
            if response.status_code == 429:
                logger.warning("LINE API limit reached (429). Falling back to Discord.")
                return send_push(user_id, messages, target='discord', channel=channel)
            
            elif response.status_code != 200:
                logger.error(f"LINE API Error: {response.status_code} {response.text}")
                # 失敗時、Discordへ転送
                fallback_msg = [{"type": "text", "text": f"⚠️ LINE送信失敗により転送:\n{messages[0].get('text', '')}"}]
                return send_push(user_id, fallback_msg, target='discord', channel='error')

            return True

        except Exception as e:
            logger.error(f"LINE send exception: {e}")
            return False

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

def get_line_message_quota():
    """
    LINE Messaging APIの今月のメッセージ送信数を取得する。
    Returns:
        dict: {'total_usage': int, 'type': 'none'|'limited', 'value': int|None, 'remain': int|None}
        エラー時は None を返す。
    """
    if not line_bot_api:
        return None

    try:
        # 消費数を取得 (Get consumption)
        consumption = line_bot_api.get_message_quota_consumption()
        total_usage = consumption.total_usage

        # 上限を取得 (Get quota) - 未設定(none)の場合は目安がないためNone
        # 無料プラン(フリー)の場合は通常 200通 (2025年現在、変更の可能性あり)
        try:
            quota = line_bot_api.get_message_quota()
            quota_type = quota.type # 'none' (無制限/従量) or 'limited' (上限あり)
            quota_value = quota.value # 上限数
        except LineBotApiError:
            # 権限不足などで取得できない場合はデフォルト値を仮定
            quota_type = 'unknown'
            quota_value = 200 # フリープランの一般的な上限
        
        remain = None
        if quota_value is not None:
            remain = max(0, quota_value - total_usage)

        return {
            "total_usage": total_usage,
            "type": quota_type,
            "limit": quota_value,
            "remain": remain
        }

    except Exception as e:
        # ロガーが未定義の場合はprintで代用 (通常は定義済み)
        if 'logger' in globals():
            logger.error(f"Failed to get LINE quota: {e}")
        else:
            print(f"Failed to get LINE quota: {e}")
        return None


# === ユーティリティ ===
def get_now_iso() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date_str() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")