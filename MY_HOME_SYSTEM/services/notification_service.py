import json
import logging
import requests
from typing import List
from linebot import LineBotApi
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
    """LINE Push API送信"""
    if not config.LINE_CHANNEL_ACCESS_TOKEN:
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
        
        if res.status_code == 429:
            logger.warning("⚠️ LINE API limit reached (429).")
            return False

        if res.status_code != 200:
            logger.error(f"LINE API Error: {res.status_code} {res.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"LINE接続エラー: {e}")
        return False

@retry_api_call
def send_push(user_id: str, messages: List[dict], image_data: bytes = None, target: str = "discord", channel: str = "notify") -> bool:
    """メッセージ送信ラッパー (LINE失敗時にDiscordへフォールバック)"""
    if target is None:
        target = config.NOTIFICATION_TARGET
    
    target_lower = target.lower()
    should_send_discord = target_lower in ["discord", "all", "both"]
    should_send_line = target_lower in ["line", "all", "both"]
    
    if not should_send_discord and not should_send_line:
        should_send_line = True
        if image_data:
            should_send_discord = True

    success = True

    if should_send_discord:
        if not _send_discord_webhook(messages, image_data, channel):
            success = False

    if should_send_line:
        if image_data and not should_send_discord:
            _send_discord_webhook(messages, image_data, channel)
            messages = list(messages)
            messages.append({"type": "text", "text": "※画像はDiscordに送信しました"})

        if not _send_line_push(user_id, messages):
            if not should_send_discord:
                logger.warning("Falling back to Discord due to LINE error.")
                fallback_msg = [{"type": "text", "text": f"⚠️ LINE送信失敗により転送:\n{messages[0].get('text', '')}"}]
                _send_discord_webhook(fallback_msg, None, 'error')
            
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
        return res.status_code == 200
    except Exception as e:
        logger.error(f"LINE Reply 接続エラー: {e}")
        return False

def get_line_message_quota():
    """LINE送信数確認"""
    if not line_bot_api:
        return None
    try:
        consumption = line_bot_api.get_message_quota_consumption()
        total_usage = consumption.total_usage
        try:
            quota = line_bot_api.get_message_quota()
            quota_type = quota.type
            quota_value = quota.value
        except LineBotApiError:
            quota_type = 'unknown'
            quota_value = 200
        
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
        logger.error(f"Failed to get LINE quota: {e}")
        return None