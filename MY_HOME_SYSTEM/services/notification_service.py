# MY_HOME_SYSTEM/services/notification_service.py
import json
import logging
import requests
from typing import List, Optional, Any
# ▼▼▼ v3 Imports ▼▼▼
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    Message
)
# ▲▲▲ ▲▲▲
import config
from core.network import get_retry_session, retry_api_call

logger = logging.getLogger("service.notification")

# v3 Configuration
line_configuration = None
if config.LINE_CHANNEL_ACCESS_TOKEN:
    line_configuration = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)

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
        # v3オブジェクトの場合は text 属性などを取得
        if hasattr(msg, "text"):
            text = msg.text
        elif hasattr(msg, "alt_text"):
            text = msg.alt_text
        elif isinstance(msg, dict):
            text = msg.get("text") or msg.get("altText") or "（画像またはスタンプ）"
        else:
            text = "（メッセージ）"
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

def _send_line_push(user_id: str, messages: List[Any]) -> bool:
    """LINE Push API送信 (v3対応版)"""
    if not line_configuration:
        return False
    
    sdk_messages = []
    
    try:
        for msg in messages:
            # A. 既に v3 オブジェクトの場合
            if isinstance(msg, Message): 
                sdk_messages.append(msg)
            
            # B. 辞書型の場合 (互換性維持)
            elif isinstance(msg, dict):
                msg_type = msg.get("type")
                if msg_type == "text":
                    sdk_messages.append(TextMessage(text=msg.get("text")))
                elif msg_type == "flex":
                    # FlexMessageオブジェクトへの変換は複雑なため、
                    # 可能な限り呼び出し元でオブジェクト化することを推奨
                    pass 
                # 必要に応じて ImageMessage 等も追加
            
        if not sdk_messages:
            logger.warning("LINE送信対象のメッセージがありません")
            return False

        # v3 送信処理
        with ApiClient(line_configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=sdk_messages
                )
            )
        return True

    except Exception as e:
        logger.error(f"LINE Push Error: {e}")
        return False

def send_push(user_id: str, messages: List[Any], image_data: bytes = None, target: str = "both", channel: str = "notify") -> bool:
    """統合プッシュ通知関数"""
    success = True
    
    # 1. Discord送信
    if target in ["discord", "both"]:
        if not _send_discord_webhook(messages, image_data, channel):
            logger.warning("Discordへの通知に失敗しました")
            success = False

    # 2. LINE送信 (image_dataはLINEには送らない簡易実装)
    if target in ["line", "both"]:
        # 画像がある場合はテキストで注記を追加
        line_msgs = list(messages)
        if image_data:
            line_msgs.append(TextMessage(text="※画像はDiscordを確認してください"))

        if not _send_line_push(user_id, line_msgs):
            # LINE失敗時はDiscordのエラーチャンネルに通知
            logger.error("LINE送信失敗。Discordへフォールバック通知を行います。")
            fallback = [{"type": "text", "text": "⚠️ LINE送信失敗: (詳細ログ確認)"}]
            _send_discord_webhook(fallback, None, 'error')
            success = False

    return success

def send_reply(reply_token: str, messages: List[Any]) -> bool:
    """LINE Reply API送信 (v3対応版)"""
    if not line_configuration: return False
    
    sdk_messages = []
    for msg in messages:
        if isinstance(msg, Message):
            sdk_messages.append(msg)
        elif isinstance(msg, dict) and msg.get('type') == 'text':
            sdk_messages.append(TextMessage(text=msg.get('text')))
            
    try:
        with ApiClient(line_configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=sdk_messages
                )
            )
        return True
    except Exception as e:
        logger.error(f"LINE Reply Error: {e}")
        return False

def get_line_message_quota():
    """LINE送信数確認 (v3対応版)"""
    if not line_configuration: return None
    try:
        with ApiClient(line_configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            # v3では get_message_quota_consumption は廃止、get_message_quota で統合情報を取得
            return line_bot_api.get_message_quota()
    except Exception as e:
        logger.error(f"Quota Check Error: {e}")
        return None