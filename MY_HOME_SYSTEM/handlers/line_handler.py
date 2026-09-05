# MY_HOME_SYSTEM/handlers/line_handler.py
import asyncio
import threading
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
    PushMessageRequest,
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
# 保守性(#410): TTLは「エントリが古いか」の判定にのみ使われ、キャッシュから自動で
# エントリを削除する仕組みが無かったため、ユニークな話者が増えるほど_profile_cacheが
# 無制限に成長し続けていた(プロセスは長時間稼働するため実質的なメモリリーク)。
# 上限を設け、超過時は最終アクセス時刻が古いエントリから削除する。
_PROFILE_CACHE_MAX_SIZE = 500

# Issue #376: AI経路(Gemini呼び出し + tenacityリトライ × 最大 MAX_TOOL_ROUNDS 回)の総時間上限。
# LINE の reply token は短命(約1分)で、超過すると reply_message が 400 になり無応答になる。
# 上限内に終わらなければ AI 処理を打ち切り、その旨をユーザーへ返す。
AI_REPLY_TIMEOUT_SEC = 20


def _is_redelivery(event) -> bool:
    """
    Issue #376: LINE の Webhook 再配信(deliveryContext.isRedelivery=true)かどうか。

    再配信を有効化していると、応答が遅れた同一イベントが再送され、冪等性チェックの無い
    記録処理(体調・食事)が二重登録される。SDK の bool 値が厳密に True の場合のみ
    再配信とみなす(テスト用の MagicMock 等、真偽値以外を誤って再配信扱いしない)。
    """
    ctx = getattr(event, "delivery_context", None)
    return getattr(ctx, "is_redelivery", False) is True


# Issue #376: webhookEventId ベースの冪等化キャッシュ。
# LINE の Webhook 配信は「少なくとも1回」の到達を保証する仕様であり、isRedelivery で
# 明示される再配信以外にも、ネットワーク遅延等により同一イベントが複数回届く可能性がある。
# webhookEventId(ULID形式。line-bot-sdk 3.21.0 では Event 基底クラスの必須フィールド)を
# 直近処理済みイベントとして記録し、二重処理(体調・食事等の記録の二重登録、AI呼び出しの
# 二重実行)を防ぐ。単一プロセス・LAN限定の個人用サービスであるため新規DBテーブルは設けず、
# `_profile_cache` と同様にプロセス内メモリ・サイズ上限つきの辞書で管理する
# (プロセス再起動で消える点は許容: 再起動を跨いだ再配信は実運用上ほぼ発生しない)。
_SEEN_EVENT_IDS: Dict[str, float] = {}  # webhook_event_id -> 検知時刻
_SEEN_EVENT_IDS_MAX_SIZE = 500
_seen_event_ids_lock = threading.Lock()
# BackgroundTasks はスレッドプール(run_in_threadpool)で実行されるため、複数の
# Webhookリクエストがほぼ同時に届いた場合に備え、確認と記録をロックで保護する。


def _evict_oldest_seen_event_ids() -> None:
    """`_SEEN_EVENT_IDS`が上限を超えている場合、検知時刻が古いものから削除する(呼び出し元でロック取得済みが前提)。"""
    overflow = len(_SEEN_EVENT_IDS) - _SEEN_EVENT_IDS_MAX_SIZE
    if overflow <= 0:
        return
    oldest_ids = sorted(_SEEN_EVENT_IDS, key=lambda eid: _SEEN_EVENT_IDS[eid])[:overflow]
    for eid in oldest_ids:
        del _SEEN_EVENT_IDS[eid]


def _is_duplicate_event(event) -> bool:
    """
    Issue #376: webhookEventId 単位の冪等化チェック。

    未処理のIDなら記録した上で False を返し、直近処理済みのIDなら True を返す
    (呼び出し側は処理をスキップする)。webhook_event_id が取得できないイベント
    (テスト用のモック等、想定外の形式)は冪等化できないため、誤って処理を止めない
    よう False(重複ではない)を返す。
    """
    event_id = getattr(event, "webhook_event_id", None)
    if not event_id:
        return False
    with _seen_event_ids_lock:
        if event_id in _SEEN_EVENT_IDS:
            return True
        _SEEN_EVENT_IDS[event_id] = time.time()
        _evict_oldest_seen_event_ids()
    return False


def _evict_oldest_profile_cache_entries() -> None:
    """
    保守性(#410): `_profile_cache`が`_PROFILE_CACHE_MAX_SIZE`件を超えている場合、
    キャッシュ時刻(`cached_at`)が古いエントリから順に削除して上限内に収める。
    """
    overflow = len(_profile_cache) - _PROFILE_CACHE_MAX_SIZE
    if overflow <= 0:
        return
    oldest_user_ids = sorted(_profile_cache, key=lambda uid: _profile_cache[uid][1])[:overflow]
    for uid in oldest_user_ids:
        del _profile_cache[uid]


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
    _evict_oldest_profile_cache_entries()
    return user_name


# === Helper Methods ===
def reply_message(reply_token: str, messages: List[Any], user_id: Optional[str] = None):
    """
    メッセージ返信のラッパー。

    Issue #376: reply token の期限切れ(AI処理に時間がかかった場合等)で reply_message が
    失敗したとき、user_id が分かっていれば push_message へフォールバックしてユーザーに
    結果を届ける(以前はログのみで無応答だった)。
    """
    if not line_bot_api: return
    if not isinstance(messages, list):
        messages = [messages]
    try:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages
            )
        )
        return
    except Exception as e:
        logger.error(f"LINE Reply Failed: {e}")

    if not user_id:
        return
    try:
        logger.warning(f"LINE Reply failed; falling back to push_message (user_id={user_id})")
        line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=messages
            )
        )
    except Exception as e:
        logger.error(f"LINE Push Fallback Failed: {e}")

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
    # Issue #376 / L-L1: 複数イベント一括配信時、1件目の例外で SDK の handle() ループが
    # 中断し以降のイベントが処理されない(のに 200 が返る)ため、イベント単位で例外を隔離する。
    try:
        if _is_redelivery(event):
            logger.warning(f"⚠️ Skipping redelivered LINE event (webhook_event_id={getattr(event, 'webhook_event_id', None)})")
            return

        user_id = event.source.user_id
        # L-L6 (#410): グループでの発言時、プロフィール未共有等の理由でLINEの仕様上
        # user_idがNoneになりうる。_get_display_name(None)はget_profile(None)の例外を
        # 握り潰し"Unknown"を返すだけなので、以前はこの状態に気づかないまま処理が続行し、
        # user_id=NULLのまま体調・食事等の記録がDB保存されていた。記録の紐付け先が
        # 無いため、user_id不明のイベントはここで処理をスキップする。
        if user_id is None:
            logger.warning("⚠️ event.source.user_id が取得できないため処理をスキップします(グループでのプロフィール未共有等の可能性)")
            return

        msg_text = event.message.text.strip()
        reply_token = event.reply_token

        user_name = _get_display_name(user_id)

        logger.info(f"📩 Recv [{user_name}]: {msg_text}")

        asyncio.run(
            _process_message_async(user_id, user_name, msg_text, reply_token)
        )
    except Exception as e:
        logger.error(f"handle_message Error: {e}", exc_info=True)

async def _process_message_async(user_id: str, user_name: str, msg_text: str, reply_token: str):
    """非同期メッセージ処理ロジック"""

    # 1. Family Quest Commands (優先度高)
    if msg_text == "ステータス":
        resp = await line_service.get_user_status_message(user_id)
        reply_message(reply_token, resp, user_id=user_id)
        return

    if msg_text == "クエスト":
        resp = await line_service.get_active_quests_message(user_id)
        reply_message(reply_token, resp, user_id=user_id)
        return

    if msg_text.startswith("承認") or msg_text.startswith("却下"):
        resp = await line_service.process_approval_command(user_id, msg_text)
        reply_message(reply_token, resp, user_id=user_id)
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
            reply_message(reply_token, responses[:5], user_id=user_id)
            return

    # 3. AI Analysis (Fallback)
    try:
        # Issue #376: AI経路の総時間に上限を設ける(reply token 期限切れ対策)。
        ai_resp_text = await asyncio.wait_for(
            ai_service.analyze_text_and_execute(user_id, user_name, msg_text),
            timeout=AI_REPLY_TIMEOUT_SEC,
        )
        if ai_resp_text:
            # Issue #377: Gemini応答は長さ無制限のため、LINEの5000字制限を超えうる。
            reply_message(reply_token, line_service.split_text_into_line_messages(ai_resp_text), user_id=user_id)
    except asyncio.TimeoutError:
        logger.error(f"AI Processing Timeout (> {AI_REPLY_TIMEOUT_SEC}s) for user {user_id}")
        reply_message(
            reply_token,
            TextMessage(text="⏳ 処理に時間がかかりすぎたため中断しました。記録が反映されているか確認のうえ、少し時間を置いて再度お試しください。"),
            user_id=user_id,
        )
    except Exception as e:
        logger.error(f"AI Processing Error: {e}")
        reply_message(reply_token, TextMessage(text="😓 すみません、うまく処理できませんでした。"), user_id=user_id)

def handle_postback(event: PostbackEvent):
    """Postbackイベント（ボタン押下など）の処理"""
    # Issue #376 / L-L1: handle_message と同様にイベント単位で例外を隔離し、再配信はスキップする。
    try:
        if _is_redelivery(event):
            logger.warning(f"⚠️ Skipping redelivered LINE postback (webhook_event_id={getattr(event, 'webhook_event_id', None)})")
            return

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
    except Exception as e:
        logger.error(f"handle_postback Error: {e}", exc_info=True)

if line_handler:
    line_handler.add(MessageEvent, message=TextMessageContent)(handle_message)
    line_handler.add(PostbackEvent)(handle_postback)


def dispatch_events(events: List[Any]) -> None:
    """
    Issue #376: routers/webhook_router.py が署名検証・パース済みのイベント一覧を
    BackgroundTasks 経由で渡してくる、実処理のエントリポイント。

    以前は `WebhookHandler.handle(body, signature)` が署名検証・パース・ディスパッチを
    HTTPレスポンス送信前に一括で行っていたため、AI呼び出し・DB書き込み・LINE返信の
    レイテンシがそのまま reply token(約1分で失効)の失効リスクに直結していた。
    ルーター側は `line_handler.parser.parse()` で署名検証とパースのみ済ませて即 200 を
    返し、実処理(このディスパッチ以降)はレスポンス送信後にバックグラウンドで行う。

    イベント種別ごとの振り分けは `line_handler.add(...)` で登録している内容
    (MessageEvent+TextMessageContent は handle_message、PostbackEvent は handle_postback)
    と同じにしてあり、それ以外のイベント種別は元の WebhookHandler と同様に無視する。

    webhookEventId ベースの冪等化チェックをここで一括して行う(handle_message/
    handle_postback 個別ではなく1箇所に集約することで、対象イベント種別が増えても
    冪等化漏れが起きないようにする)。1件のイベント処理で例外が起きても後続イベントの
    処理を止めない(handle_message/handle_postback 自体も内部で例外を握り潰すが、
    このループでも二重に防御する)。
    """
    for event in events:
        try:
            if _is_duplicate_event(event):
                logger.info(
                    f"⚠️ Skipping duplicate LINE event (webhook_event_id={getattr(event, 'webhook_event_id', None)})"
                )
                continue

            if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
                handle_message(event)
            elif isinstance(event, PostbackEvent):
                handle_postback(event)
        except Exception as e:
            logger.error(f"dispatch_events Error: {e}", exc_info=True)
