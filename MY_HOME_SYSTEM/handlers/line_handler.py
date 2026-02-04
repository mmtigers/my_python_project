# MY_HOME_SYSTEM/handlers/line_handler.py
import asyncio
import os
import sys
from typing import Optional, List

from fastapi import Request, HTTPException

# LINE Bot SDK v3
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, PostbackEvent
from linebot.v3.exceptions import InvalidSignatureError

import config
from core.logger import setup_logging
from models.line import LinePostbackData
from services import line_service, ai_service

# ロガー設定
logger = setup_logging("line_handler")

# === LINE API Initialization ===
line_handler: Optional[WebhookHandler] = None
line_bot_api: Optional[MessagingApi] = None

if config.LINE_CHANNEL_ACCESS_TOKEN and config.LINE_CHANNEL_SECRET:
    try:
        line_conf = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)
        line_bot_api = MessagingApi(ApiClient(line_conf))
        line_handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
        logger.info("✅ LINE Bot API v3 initialized in Handler")
    except Exception as e:
        logger.error(f"LINE initialization failed: {e}")

# === Helper Methods ===
def reply_message(reply_token: str, messages: List[any]):
    """メッセージ返信のラッパー"""
    if not line_bot_api: return
    try:
        if not isinstance(messages, list):
            messages = [messages]
            
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages
            )
        )
    except Exception as e:
        logger.error(f"LINE Reply Failed: {e}")

# === Event Handlers ===

if line_handler:
    @line_handler.add(MessageEvent, message=TextMessageContent)
    def handle_message(event: MessageEvent):
        """テキストメッセージ受信時の処理"""
        user_id = event.source.user_id
        msg_text = event.message.text.strip()
        reply_token = event.reply_token

        user_name = "Unknown"
        try:
            if line_bot_api:
                profile = line_bot_api.get_profile(user_id)
                user_name = profile.display_name
        except Exception:
            pass

        logger.info(f"📩 Recv [{user_name}]: {msg_text}")
        
        asyncio.run(
            _process_message_async(user_id, user_name, msg_text, reply_token)
        )

    async def _process_message_async(user_id: str, user_name: str, msg_text: str, reply_token: str):
        """非同期メッセージ処理ロジック"""
        
        # 1. Family Quest Commands (優先度高)
        if msg_text == "ステータス":
            resp = await line_service.get_user_status_message(user_id)
            reply_message(reply_token, resp)
            return

        if msg_text == "クエスト":
            resp = await line_service.get_active_quests_message(user_id)
            reply_message(reply_token, resp)
            return
            
        if msg_text.startswith("承認") or msg_text.startswith("却下"):
            resp = await line_service.process_approval_command(user_id, msg_text)
            reply_message(reply_token, resp)
            return

        # 2. Health & Life Log Commands
        if "子供記録" in msg_text or "体調" in msg_text:
            for child in config.FAMILY_SETTINGS["members"]:
                if child in msg_text:
                    cond = "元気" if "元気" in msg_text else ("風邪" if "風邪" in msg_text else "不明")
                    resp = await line_service.log_child_health(user_id, user_name, child, cond)
                    reply_message(reply_token, resp)
                    return

        # 3. AI Analysis (Fallback)
        try:
            ai_resp_text = await ai_service.analyze_text_and_execute(
                user_id, user_name, msg_text
            )
            if ai_resp_text:
                reply_message(reply_token, TextMessage(text=ai_resp_text))
        except Exception as e:
            logger.error(f"AI Processing Error: {e}")
            reply_message(reply_token, TextMessage(text="😓 すみません、うまく処理できませんでした。"))

    @line_handler.add(PostbackEvent)
    def handle_postback(event: PostbackEvent):
        """Postbackイベント（ボタン押下など）の処理"""
        user_id = event.source.user_id
        data_str = event.postback.data
        reply_token = event.reply_token
        
        logger.info(f"📩 Postback [{user_id}]: {data_str}")

        if data_str.startswith("approve:") or data_str.startswith("reject:"):
            cmd_map = {"approve": "承認", "reject": "却下"}
            action, hist_id = data_str.split(":")
            cmd_text = f"{cmd_map[action]} {hist_id}"
            asyncio.run(_process_message_async(user_id, "Postback", cmd_text, reply_token))
        else:
            reply_message(reply_token, TextMessage(text=f"Unknown Action: {data_str}"))

# 外部からの呼び出し用エントリーポイント
def handle_request(request: Request, body: str, signature: str):
    if not line_handler:
        return
    try:
        line_handler.handle(body, signature)
    except InvalidSignatureError:
        logger.warning("Invalid Signature")
        raise HTTPException(status_code=400, detail="Invalid signature")