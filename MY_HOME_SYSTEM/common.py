import sqlite3
import requests
import json
import datetime
import pytz
import logging
import traceback
import os  # 追加
import asyncio
import tenacity
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import List, Any, Optional, Union
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler # 追加
import config
from linebot.exceptions import LineBotApiError
from linebot import LineBotApi
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# LineBotApiの初期化
if config.LINE_CHANNEL_ACCESS_TOKEN:
    line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
else:
    line_bot_api = None

    
# === ロギング設定 ===
class DiscordErrorHandler(logging.Handler):
    """エラーログをDiscordに通知するハンドラ"""
    def emit(self, record):
        if record.levelno >= logging.ERROR and "Discord" not in record.msg:
            try:
                msg = self.format(record)
                url = config.DISCORD_WEBHOOK_ERROR
                if url:
                    payload = {"content": f"😰 **システムエラー発生**\n```{msg[:1800]}```"}
                    requests.post(url, json=payload, timeout=5)
            except Exception:
                pass

def setup_logging(name: str) -> logging.Logger:
    """ロガーのセットアップ (ローテーション機能付き)"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        logger.handlers.clear()
    
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # 1. 標準出力 (開発確認用)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # 2. ファイル出力 (ローテーション付き)
    # logsディレクトリの確保
    log_dir = os.path.join(config.BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # 毎日深夜0時にローテーション、7世代(1週間分)保持
    log_file = os.path.join(log_dir, "home_system.log")
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when='midnight',
        interval=1,
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 3. Discord通知 (エラー時)
    if config.DISCORD_WEBHOOK_ERROR:
        discord_handler = DiscordErrorHandler()
        discord_handler.setFormatter(formatter)
        discord_handler.setLevel(logging.ERROR)
        logger.addHandler(discord_handler)
    
    logging.getLogger("zeep").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    return logger



logger = setup_logging("common")

def retry_api_call(func):
    """
    API呼び出しにリトライロジックを付与するデコレータ。
    - 最大3回試行
    - 指数バックオフ（2秒, 4秒, 8秒...と間隔を広げる）
    - ネットワークエラー（requests.exceptions.RequestException）時に発動
    """
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        before_sleep=tenacity.before_sleep_log(logging.getLogger("common"), logging.WARNING),
        reraise=True
    )(func)

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

async def save_log_async(table: str, columns_list: List[str], values_list: tuple) -> bool:
    """save_log_generic の非同期ラッパー"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, save_log_generic, table, columns_list, values_list)

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
    """LINE Push API送信 (エラーハンドリング強化版)"""
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
        session = get_retry_session()
        res = session.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        
        # --- 修正: 429 (レート制限) を特別扱いする ---
        if res.status_code == 429:
            logger.warning("⚠️ LINE API limit reached (429).")
            return False
        # ----------------------------------------

        if res.status_code != 200:
            logger.error(f"LINE API Error: {res.status_code} {res.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE接続エラー: {e}")
        return False

@retry_api_call
def send_push(user_id: str, messages: List[dict], image_data: bytes = None, target: str = "discord", channel: str = "notify") -> bool:
    """
    メッセージを送信するラッパー関数 (修正版)
    - LINE送信失敗時(特に429)は自動的にDiscordへフォールバックします
    """
    if target is None:
        target = config.NOTIFICATION_TARGET
    
    target_lower = target.lower()
    
    # 送信先の判定
    should_send_discord = target_lower in ["discord", "all", "both"]
    should_send_line = target_lower in ["line", "all", "both"]
    
    # ターゲット指定がない(elseルート)場合のデフォルト挙動維持
    if not should_send_discord and not should_send_line:
        should_send_line = True
        # 画像がある場合はDiscordにも送る（既存ロジック踏襲）
        if image_data:
            should_send_discord = True

    success = True

    # 1. Discord送信
    if should_send_discord:
        if not _send_discord_webhook(messages, image_data, channel):
            success = False

    # 2. LINE送信 (Discordへのフォールバック機能付き)
    if should_send_line:
        # 画像直接送信は未実装のため、Discordへ送っていない場合はDiscordへ逃がす
        if image_data and not should_send_discord:
            logger.warning("LINEへの画像直接送信は未実装です (Discordへフォールバックします)")
            _send_discord_webhook(messages, image_data, channel)
            # LINEには画像を見ろというメッセージを送る
            messages = list(messages) # コピー
            messages.append({"type": "text", "text": "※画像はDiscordに送信しました"})

        # LINE送信実行
        if not _send_line_push(user_id, messages):
            # 失敗した場合、かつDiscordにまだ送っていない情報であればフォールバック
            if not should_send_discord:
                logger.warning("Falling back to Discord due to LINE error.")
                fallback_msg = [{"type": "text", "text": f"⚠️ LINE送信失敗により転送:\n{messages[0].get('text', '')}"}]
                _send_discord_webhook(fallback_msg, None, 'error')
            
            # LINEのみへの送信が失敗した場合はFalseとする
            if not should_send_discord:
                success = False

    return success

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