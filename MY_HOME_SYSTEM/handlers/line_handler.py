# MY_HOME_SYSTEM/handlers/line_handler.py
import asyncio
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
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, PostbackEvent

import config
from core.logger import setup_logging
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

# Issue #375: 「元気ない」「元気がない」「元気なし」は "元気" を部分文字列として含むため、
# 以前は `"元気" if "元気" in msg_text` で肯定の「元気」として記録され意味が反転していた。
# 否定表現を肯定判定より先に評価する。
_NEGATIVE_GENKI_PATTERNS = ("元気ない", "元気がない", "元気なし", "元気じゃない", "元気ではない")
CONDITION_NOT_GENKI = "元気なし"


def _detect_condition_keyword(text: str) -> str:
    """定型キーワードから体調の状態を判定する(否定表現を先に判定)。該当なしは「不明」。"""
    if any(p in text for p in _NEGATIVE_GENKI_PATTERNS):
        return CONDITION_NOT_GENKI
    if "元気" in text:
        return "元気"
    if "風邪" in text:
        return "風邪"
    return "不明"


def _extract_health_targets(msg_text: str) -> List[tuple]:
    """
    Issue #375: メッセージ中に登場する家族メンバー全員と、それぞれの体調キーワードを返す。

    以前は最初に一致した1名だけを処理し、「体調 智矢 元気 涼花 風邪」のような
    2名併記時は2人目以降を無言で捨てていた。各名前の直後〜次の名前までの区間から
    体調を判定し、区間内にキーワードが無ければメッセージ全体から判定した値
    (「体調 元気 智矢 涼花」のように名前より前にキーワードがある書き方)へフォールバックする。

    Returns:
        List[tuple]: 出現順の (メンバー名, 体調) のリスト。該当メンバーが無ければ空。
    """
    positions = []
    for member in config.FAMILY_SETTINGS["members"]:
        idx = msg_text.find(member)
        if idx >= 0:
            positions.append((idx, member))
    positions.sort()

    whole_cond = _detect_condition_keyword(msg_text)
    targets = []
    for i, (idx, member) in enumerate(positions):
        seg_start = idx + len(member)
        seg_end = positions[i + 1][0] if i + 1 < len(positions) else len(msg_text)
        seg_cond = _detect_condition_keyword(msg_text[seg_start:seg_end])
        targets.append((member, seg_cond if seg_cond != "不明" else whole_cond))
    return targets


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
        # Issue #375: 否定表現(元気ない等)を先に判定し、2名以上の併記は全員分を記録する。
        targets = _extract_health_targets(msg_text)
        if targets:
            responses = []
            for child, cond in targets:
                responses.append(await line_service.log_child_health(user_id, user_name, child, cond))
            # LINEのreplyは1回につき最大5メッセージ。メンバー数(4名)はこれに収まる。
            reply_message(reply_token, responses[:5])
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