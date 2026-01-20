# MY_HOME_SYSTEM/services/notification_service.py
import json
import logging
import requests
from typing import List, Optional
from linebot import LineBotApi
from linebot.models import TextSendMessage
from linebot.exceptions import LineBotApiError
import config
from core.network import get_retry_session, retry_api_call

logger = logging.getLogger("service.notification")

# LineBotApiの初期化
line_bot_api = None
if config.LINE_CHANNEL_ACCESS_TOKEN:
    line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)

def _send_discord_webhook(messages: List[dict], image_data: bytes = None, channel: str = "notify") -> bool:
    """DiscordへのWebhook送信"""
    if channel == "error":
        url = config.DISCORD_WEBHOOK_ERROR
    elif channel == "report":
        url = config.DISCORD_WEBHOOK_REPORT
    else:
        url = config.DISCORD_WEBHOOK_NOTIFY or config.DISCORD_WEBHOOK_URL
    
    if not url:
        return False
    
    text_content = ""
    for msg in messages:
        text = msg.get("text") or msg.get("altText") or "（画像またはスタンプ）"
        text_content += f"{text}\n\n"
    
    try:
        if image_data:
            files = {'file': ('snapshot.jpg', image_data, 'image/jpeg')}
            res = requests.post(url, files=files, data={'content': text_content}, timeout=10)
        else:
            res = requests.post(url, json={"content": text_content}, timeout=10)
        
        return res.status_code in [200, 204]
    except Exception as e:
        logger.error(f"Discord送信失敗: {e}")
        return False

def _send_line_push(user_id: str, messages: List[dict]) -> bool:
    """LINE Push API送信 (Messaging API)"""
    if not line_bot_api:
        logger.warning("LINE Bot API is not initialized.")
        return False

    # dict形式のメッセージをSDKのモデルに変換 (現在はTextのみ簡易対応)
    # 本格的にやるならFlexMessageなども対応が必要ですが、まずはTextで実装
    sdk_messages = []
    for msg in messages:
        if msg.get('type') == 'text':
            sdk_messages.append(TextSendMessage(text=msg.get('text')))
        # 必要に応じてImageSendMessageなども追加
    
    if not sdk_messages:
        return False

    try:
        line_bot_api.push_message(user_id, sdk_messages)
        return True
    except LineBotApiError as e:
        logger.error(f"LINE API Error: {e.status_code} {e.message}")
        if e.status_code == 429:
            logger.warning("⚠️ LINE API limit reached.")
        return False
    except Exception as e:
        logger.error(f"LINE送信失敗: {e}")
        return False

@retry_api_call
def send_push(user_id: str, messages: List[dict], image_data: bytes = None, target: str = None, channel: str = "notify", priority: str = "normal") -> bool:
    """
    メッセージ送信ラッパー
    
    Args:
        priority (str): 'high' なら本番環境でLINEに通知。'normal' ならDiscordのみ。
    """
    
    # 1. 宛先決定ロジック
    # 開発環境なら強制的にDiscordのみ
    is_production = (config.ENV == "production")
    
    # ターゲット指定がない場合の自動判定
    should_send_line = False
    should_send_discord = True # デフォルトはDiscordにログを残す
    
    if is_production:
        if priority == "high":
            should_send_line = True
        elif target and target.lower() == "line":
             should_send_line = True
    else:
        # 開発環境でLINE指定があっても、誤送信防止のためログに出してDiscordへ
        if priority == "high" or (target and target.lower() == "line"):
             logger.info("[DEV MODE] LINE送信をスキップし、Discordに転送します")

    success = True

    # 2. Discord送信 (ログ保存・通知用)
    if should_send_discord:
        prefix = ""
        if should_send_line and is_production:
            prefix = "📱 [LINE送信] "
        elif not is_production and (priority == "high" or target == "line"):
            prefix = "🧪 [DEV/LINE転送] "
            
        # メッセージのコピーを作成してプレフィックス付与
        discord_msgs = []
        for m in messages:
            dm = m.copy()
            if 'text' in dm:
                dm['text'] = prefix + dm['text']
            discord_msgs.append(dm)

        if not _send_discord_webhook(discord_msgs, image_data, channel):
            success = False # Discord失敗はシステム的に失敗扱いにするか要検討（今回はログだけ残す形でもよい）

    # 3. LINE送信 (本番かつ重要通知のみ)
    if should_send_line and is_production:
        # 画像はLINE Pushで送ると高コスト/複雑なので、Discordに送った旨だけ伝える簡易実装推奨
        line_msgs = messages
        if image_data:
            line_msgs = list(messages)
            line_msgs.append({"type": "text", "text": "※画像はDiscordを確認してください"})

        if not _send_line_push(user_id, line_msgs):
            # LINE失敗時はDiscordのエラーチャンネルに通知
            logger.error("LINE送信失敗。Discordへフォールバック通知を行います。")
            fallback = [{"type": "text", "text": f"⚠️ LINE送信失敗:\n{messages[0].get('text', '')}"}]
            _send_discord_webhook(fallback, None, 'error')
            success = False

    return success

# ... (send_reply, get_line_message_quota は変更なしでOK) ...
def send_reply(reply_token: str, messages: List[dict]) -> bool:
    """LINE Reply API送信"""
    if not line_bot_api: return False
    sdk_messages = []
    for msg in messages:
        if msg.get('type') == 'text':
            sdk_messages.append(TextSendMessage(text=msg.get('text')))
    try:
        line_bot_api.reply_message(reply_token, sdk_messages)
        return True
    except Exception as e:
        logger.error(f"LINE Reply Error: {e}")
        return False

def get_line_message_quota():
    """LINE送信数確認"""
    if not line_bot_api: return None
    try:
        consumption = line_bot_api.get_message_quota_consumption()
        quota = line_bot_api.get_message_quota()
        return {
            "total_usage": consumption.total_usage,
            "type": quota.type,
            "limit": quota.value,
            "remain": max(0, quota.value - consumption.total_usage)
        }
    except Exception:
        return None