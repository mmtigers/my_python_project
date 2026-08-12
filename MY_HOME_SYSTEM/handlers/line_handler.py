# MY_HOME_SYSTEM/handlers/line_handler.py
import asyncio
import os
import sys
import json
import time
from typing import Optional, List, Any, Dict

import handlers.line_logic as line_logic

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

# プロフィール表示名のキャッシュ (ログ用のためだけに毎回 LINE API を叩かないようにする)
_PROFILE_CACHE_TTL_SEC = 3600
_profile_cache: Dict[str, tuple] = {}  # user_id -> (display_name, cached_at)


def _get_display_name(user_id: str) -> str:
    """LINEのユーザー表示名を取得する。TTL付きでキャッシュし、API呼び出し頻度を抑える。"""
    cached = _profile_cache.get(user_id)
    if cached and (time.time() - cached[1]) < _PROFILE_CACHE_TTL_SEC:
        return cached[0]

    user_name = "Unknown"
    try:
        if line_bot_api:
            profile = line_bot_api.get_profile(user_id)
            user_name = profile.display_name
    except Exception:
        pass

    _profile_cache[user_id] = (user_name, time.time())
    return user_name


# === Helper Methods ===
def reply_message(reply_token: str, messages: List[Any]):
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
# 注: ディスパッチロジック自体はLINE SDKの初期化有無に関わらず常に定義する。
# SDKへの登録(line_handler.add)のみを `if line_handler:` 配下で行うことで、
# 認証情報が無い環境(テスト等)でもロジック単体をimport・実行できるようにしている。

def handle_message(event: MessageEvent):
    """テキストメッセージ受信時の処理"""
    user_id = event.source.user_id
    msg_text = event.message.text.strip()
    reply_token = event.reply_token

    user_name = _get_display_name(user_id)

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

def handle_postback(event: PostbackEvent):
    """Postbackイベント（ボタン押下など）の処理"""
    user_id = event.source.user_id
    data_str = event.postback.data
    reply_token = event.reply_token

    logger.info(f"📩 Postback [{user_id}]: {data_str}")

    # 1. Family Quest (承認/却下) の処理
    if data_str.startswith("approve:") or data_str.startswith("reject:"):
        cmd_map = {"approve": "承認", "reject": "却下"}
        try:
            action, hist_id = data_str.split(":")
            cmd_text = f"{cmd_map[action]} {hist_id}"
            # 非同期で処理を実行（承認処理は時間がかかる場合があるため）
            asyncio.run(_process_message_async(user_id, "Postback", cmd_text, reply_token))
        except ValueError:
            logger.error(f"Invalid Postback format: {data_str}")
        return

    # 2. 既存ロジック (line_logic.py) への委譲
    # show_health_input, child_check, その他のボタン操作はここで処理
    try:
        # line_logic側に処理を丸投げする
        line_logic.handle_postback(event, line_bot_api)
    except Exception as e:
        logger.error(f"Logic Delegation Error: {e}")
        # 万が一のエラー時はユーザーに通知（任意）
        # reply_message(reply_token, TextMessage(text="⚠️ 処理中にエラーが発生しました。"))

if line_handler:
    line_handler.add(MessageEvent, message=TextMessageContent)(handle_message)
    line_handler.add(PostbackEvent)(handle_postback)