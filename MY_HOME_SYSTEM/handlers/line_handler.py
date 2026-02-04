# MY_HOME_SYSTEM/handlers/line_handler.py
import asyncio
from urllib.parse import parse_qsl
from typing import Optional

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
from models.line import LinePostbackData, UserInputState, InputMode
from services import line_service, ai_service
from views import line_flex

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

# ユーザーの状態管理
USER_INPUT_STATE = {}

# === Helper Methods ===

def reply_text(reply_token: str, text: str, quick_reply: QuickReply = None):
    """テキスト返信ショートカット"""
    if not line_bot_api: return
    try:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                replyToken=reply_token,
                messages=[TextMessage(text=text, quickReply=quick_reply)]
            )
        )
    except Exception as e:
        logger.error(f"Reply Error: {e}")

def create_quick_reply(items_data: list) -> QuickReply:
    items = []
    for label, text in items_data:
        items.append(QuickReplyItem(action=MessageAction(label=str(label)[:20], text=text)))
    return QuickReply(items=items)

def get_user_name(event) -> str:
    """ユーザー名取得"""
    try:
        if not line_bot_api: return "家族のみんな"
        user_id = event.source.user_id
        if event.source.type == "group":
            return line_bot_api.get_group_member_profile(event.source.group_id, user_id).display_name
        else:
            return line_bot_api.get_profile(user_id).display_name
    except:
        return "家族のみんな"

# === Event Handlers ===

def handle_postback(event: PostbackEvent):
    """Postbackイベント処理"""
    try:
        user_id = event.source.user_id
        reply_token = event.reply_token
        user_name = get_user_name(event)
        
        raw_dict = dict(parse_qsl(event.postback.data))
        action = raw_dict.get("action")
        
        # 1. 全員元気
        if action == "all_genki":
            timestamp = line_service.get_now_iso()
            for name in config.FAMILY_SETTINGS["members"]:
                asyncio.run(line_service.log_child_health(user_id, user_name, name, "😊 元気いっぱい"))
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=[
                        FlexMessage(
                            altText="記録完了", 
                            contents=line_flex.create_record_confirm_bubble("✅ 全員の「元気」を記録しました！")
                        )
                    ]
                )
            )

        # 2. 詳細入力パネル表示
        elif action == "show_health_input":
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=[
                        TextMessage(text="気になる方の体調を入力してください👇"),
                        FlexMessage(altText="体調入力パネル", contents=line_flex.create_health_carousel())
                    ]
                )
            )

        # 3. 個別記録
        elif action == "child_check":
            target = raw_dict.get("child")
            status_key = raw_dict.get("status")
            status_map = {
                "genki": "😊 元気いっぱい",
                "fever": "🤒 お熱がある",
                "cold": "🤧 鼻水・咳・他",
                "other": "✏️ その他"
            }
            condition = status_map.get(status_key, "その他")

            if status_key == "other" and target:
                USER_INPUT_STATE[user_id] = UserInputState(mode=InputMode.CHILD_HEALTH, target_name=target)
                reply_text(reply_token, f"了解です。{target}の様子をメッセージで送ってください📝")
            elif target:
                asyncio.run(line_service.log_child_health(user_id, user_name, target, condition))
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=reply_token,
                        messages=[
                            FlexMessage(
                                altText="記録完了",
                                contents=line_flex.create_record_confirm_bubble(f"📝 {target}: {condition}\n記録しました。")
                            )
                        ]
                    )
                )

        # 4. 記録確認
        elif action == "check_status":
            summary = line_service.get_daily_health_summary_text()
            today_disp = line_service.get_today_date_str()
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=[
                        FlexMessage(
                            altText="記録サマリ",
                            contents=line_flex.create_summary_bubble(today_disp, summary)
                        )
                    ]
                )
            )

        # 5. 食事記録 (ワンタップ)
        elif action == "food_record_direct":
            category = raw_dict.get("category", "その他")
            item = raw_dict.get("item", "不明なメニュー")
            asyncio.run(line_service.log_food_record(user_id, user_name, category, item, is_manual=False))
            reply_text(reply_token, f"🍽️ 記録しました！\n【{category}】{item}\n\n今日も一日お疲れ様でした🍵")

        # 6. 食事記録 (手入力モード)
        elif action == "food_manual":
            category = raw_dict.get("category", "その他")
            USER_INPUT_STATE[user_id] = UserInputState(mode=InputMode.MEAL, category=category)
            reply_text(reply_token, f"了解です！\n{category}のメニューを入力してください📝")

    except Exception as e:
        logger.error(f"Handle Postback Error: {e}")

def handle_message(event: MessageEvent):
    """メッセージイベント処理"""
    try:
        msg = event.message.text.strip()
        user_id = event.source.user_id
        reply_token = event.reply_token
        user_name = get_user_name(event)

        # === 1. 手入力モード処理 ===
        if user_id in USER_INPUT_STATE:
            # キャンセル/コマンド検知時はモード解除
            if msg.startswith(("食事", "子供", "キャンセル", "戻る")):
                del USER_INPUT_STATE[user_id]
                if msg in ["キャンセル", "戻る"]:
                    reply_text(reply_token, "キャンセルしました。")
                    return
            else:
                state = USER_INPUT_STATE[user_id]
                
                if state.mode == InputMode.CHILD_HEALTH:
                    asyncio.run(line_service.log_child_health(user_id, user_name, state.target_name, msg))
                    del USER_INPUT_STATE[user_id]
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            replyToken=reply_token,
                            messages=[
                                FlexMessage(
                                    altText="記録完了",
                                    contents=line_flex.create_record_confirm_bubble(f"📝 {state.target_name}: {msg}\n詳細を記録しました。")
                                )
                            ]
                        )
                    )
                    return

                elif state.mode == InputMode.MEAL:
                    asyncio.run(line_service.log_food_record(user_id, user_name, state.category, msg, is_manual=True))
                    del USER_INPUT_STATE[user_id]
                    # 外出アンケート
                    qr = create_quick_reply([("はい", "外出_はい"), ("いいえ", "外出_いいえ")])
                    reply_text(reply_token, f"「{state.category}: {msg}」を記録したよ📝\n今日はお出かけした？", qr)
                    return

        # === 2. コマンド処理 ===
        if msg.startswith("子供選択_"):
            target = msg.replace("子供選択_", "")
            actions = [(s, f"child_check_{target}_{s}") for s in config.CHILD_SYMPTOMS] # 簡易化
            # ここは実装省略（既存ロジック準拠）...
            return

        if msg.startswith("外出_"):
            val = msg.replace("外出_", "")
            asyncio.run(line_service.log_daily_action(user_id, user_name, "外出", val))
            qr = create_quick_reply([("はい", "面会_はい"), ("いいえ", "面会_いいえ")])
            reply_text(reply_token, "誰かと会ったりした？", qr)
            return
            
        if msg.startswith("面会_"):
            val = msg.replace("面会_", "")
            asyncio.run(line_service.log_daily_action(user_id, user_name, "面会", val))
            reply_text(reply_token, "記録しました！お疲れ様でした🍵")
            return

        # === 3. AI / その他 ===
        # おはよう
        kw = next((k for k in config.OHAYO_KEYWORDS if k in msg.lower()), None)
        if kw:
            asyncio.run(line_service.log_ohayo(user_id, user_name, msg, kw))
            reply_text(reply_token, f"{user_name}さん、おはようございます！☀️")
            return

        # AI Service Call
        ai_resp = ai_service.analyze_text_and_execute(msg, user_id, user_name)
        if ai_resp:
            reply_text(reply_token, ai_resp)

    except Exception as e:
        logger.error(f"Handle Message Error: {e}")

# Handler登録
if line_handler:
    line_handler.add(MessageEvent, message=TextMessageContent)(handle_message)
    line_handler.add(PostbackEvent)(handle_postback)
    