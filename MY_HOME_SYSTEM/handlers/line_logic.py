# MY_HOME_SYSTEM/handlers/line_logic.py
import config
import asyncio
import datetime
from dataclasses import dataclass
from typing import Callable, Dict, Optional
from urllib.parse import parse_qsl

# ▼▼▼ v3 Imports ▼▼▼
from linebot.v3.messaging import (
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
    QuickReply
)
from linebot.v3.webhooks import PostbackEvent
# ▲▲▲ ▲▲▲

# Local Modules
# ▼▼▼ 修正箇所: ロガーの初期化方法を変更 ▼▼▼
# from core.logger import logger  <-- 削除
from core.logger import setup_logging
logger = setup_logging("line_logic")
# ▲▲▲ ▲▲▲
from core.utils import get_now_iso, get_today_date_str, get_display_date, get_meal_time_category_from_now
from core.database import save_log_async, save_logs_batch_async, get_ro_connection
from models.line import LinePostbackData

TARGET_MEMBERS = config.FAMILY_SETTINGS["members"]

# --- Helper Functions ---

def sync_run(coro):
    """
    スレッドプール内で非同期関数(DB保存等)を実行するためのヘルパー。
    Webhookハンドラは別スレッドで動いているため、asyncio.run()で
    新しいイベントループを作って実行して完了を待機する。
    戻り値はコルーチンの戻り値。実行時に例外が発生した場合はFalseを返す。
    """
    try:
        return asyncio.run(coro)
    except Exception as e:
        logger.error(f"Sync execution error: {e}")
        return False

def send_reply_text(api: MessagingApi, reply_token: str, text: str, quick_reply: QuickReply = None):
    """テキストメッセージ返信のショートカット"""
    try:
        # v3では TextMessage オブジェクトを作成して送信
        msg = TextMessage(text=text, quickReply=quick_reply)
        api.reply_message(
            ReplyMessageRequest(
                replyToken=reply_token,
                messages=[msg]
            ),
            _request_timeout=config.LINE_API_REQUEST_TIMEOUT
        )
    except Exception as e:
        logger.error(f"Reply Error: {e}")

def get_user_name(event, line_bot_api: MessagingApi) -> str:
    """プロファイル取得 (v3対応)"""
    try:
        user_id = event.source.user_id
        if event.source.type == "group":
            group_id = event.source.group_id
            profile = line_bot_api.get_group_member_profile(group_id, user_id, _request_timeout=config.LINE_API_REQUEST_TIMEOUT)
            return profile.display_name
        elif event.source.type == "user":
            profile = line_bot_api.get_profile(user_id, _request_timeout=config.LINE_API_REQUEST_TIMEOUT)
            return profile.display_name
    except Exception:
        pass
    return "家族のみんな"


# --- Logic & UI Generators ---

def create_health_carousel_flex():
    """詳細入力用カルーセルを作成 (v3 FlexContainer変換)"""
    bubbles = []
    styles = config.FAMILY_SETTINGS["styles"]

    for name in TARGET_MEMBERS:
        st = styles.get(name, {"color": "#333333", "age": "", "icon": "🙂"})
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": st["color"],
                "contents": [
                    {"type": "text", "text": f"{st['icon']} {name}", "color": "#FFFFFF", "weight": "bold", "size": "xl"}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [{"type": "text", "text": "体調を選択してください", "size": "sm", "color": "#666666"}]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "button", "style": "primary", "color": st["color"], "height": "sm",
                     "action": {"type": "postback", "label": "💮 元気！", "data": f"action=child_check&child={name}&status=genki"}},
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "🤒 熱あり", "data": f"action=child_check&child={name}&status=fever"}},
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "🤧 鼻水・他", "data": f"action=child_check&child={name}&status=cold"}},
                    {"type": "button", "style": "secondary", "height": "sm",
                     "action": {"type": "postback", "label": "✏️ その他（手入力）", "data": f"action=child_check&child={name}&status=other"}},
                    {"type": "separator", "margin": "md"},
                    {"type": "button", "style": "link", "height": "sm", "margin": "md",
                     "action": {"type": "postback", "label": "📊 今日の記録確認", "data": "action=check_status"}}
                ]
            }
        }
        bubbles.append(bubble)

    # 辞書からFlexContainerオブジェクトへ変換
    return FlexContainer.from_dict({"type": "carousel", "contents": bubbles})

def get_daily_health_summary():
    """今日の記録サマリを取得 (SQLite直接接続版)"""
    today_str = get_today_date_str() # YYYY-MM-DD
    summary_lines = []
    
    # #661: 読み取り専用の接続は core/database.get_ro_connection に一本化した
    # (timeout 30秒。以前は既定の5秒で、毎日04:00のバックアップや保持期間削除と
    # 重なると "database is locked" で LINE の応答が失敗しうる。close も同ヘルパーが行う)。
    try:
        with get_ro_connection() as conn:
            cur = conn.cursor()
            
            for name in TARGET_MEMBERS:
                # 今日の最新の記録を取得
                cur.execute(f"""
                    SELECT condition, timestamp FROM {config.SQLITE_TABLE_CHILD}
                    WHERE child_name = ? AND timestamp LIKE ?
                    ORDER BY id DESC LIMIT 1
                """, (name, f"{today_str}%"))
                row = cur.fetchone()
                
                if row:
                    try:
                        dt = datetime.datetime.fromisoformat(row["timestamp"])
                        time_str = dt.strftime("%H:%M")
                    except Exception:
                        time_str = "??:??"
                    status = row["condition"]
                    # #571: 部分文字列マッチ("元気" in status)だと、Issue #375で否定表現用に
                    # 正規化される固定文字列(handlers/line_handler.py の
                    # CONDITION_NOT_GENKI = "元気なし")も"元気"を含むため誤って
                    # ✅(元気)と判定していた(「元気ない」等の否定入力が意味の反転した
                    # 表示になっていた)。否定表現を先に判定する。
                    icon = "⚠️" if "元気なし" in status else ("✅" if "元気" in status else "⚠️")
                    summary_lines.append(f"{icon} {name}: {status} ({time_str})")
                else:
                    summary_lines.append(f"❓ {name}: (未記録)")
    except Exception as e:
        logger.error(f"DB Read Error: {e}")
        return "（データ取得エラー）"
    
    return "\n".join(summary_lines)


# --- Handlers ---

@dataclass(frozen=True)
class PostbackContext:
    """1件の Postback を処理するのに必要な入力をまとめたもの(#662)。

    以前は handle_postback が232行・ネスト9段の1関数で、6アクション分の分岐を
    直書きし、全体を単一の try/except で包んでいた。そのためどの分岐で失敗したのかが
    ログから判別しづらく、追加・変更のたびに関数が伸び続けていた。
    """

    event: PostbackEvent
    line_bot_api: MessagingApi
    user_id: str
    user_name: str
    reply_token: str
    raw: Dict[str, str]
    pb: LinePostbackData

    @property
    def target_name(self) -> Optional[str]:
        return self.pb.child


def _handle_all_genki(ctx: "PostbackContext") -> None:
    """全員の「元気」を一括記録する(単一トランザクション。#231)。"""
    timestamp = get_now_iso()

    # #231: 以前はTARGET_MEMBERS分のsave_log_asyncをそれぞれ独立に呼んでおり、
    # 各呼び出しが個別にcommitされていた。1件でも失敗すると「全体を失敗扱い」
    # として案内しユーザーに再試行を促す(H-7)一方、既に成功した分はコミット
    # 済みのまま残り、案内どおり再試行すると成功済み分まで再度INSERTされ
    # 重複行が生じていた。全メンバー分を単一トランザクションでまとめて保存し、
    # 1件でも失敗すれば全件ロールバックすることで、案内どおりDB状態も真に
    # all-or-nothingにし、再試行を安全にする。
    save_all_ok = sync_run(save_logs_batch_async(
        config.SQLITE_TABLE_CHILD,
        ["user_id", "user_name", "child_name", "condition", "timestamp"],
        [(ctx.user_id, ctx.user_name, name, "😊 元気いっぱい", timestamp) for name in TARGET_MEMBERS]
    ))

    if not save_all_ok:
        logger.error(f"all_genki の記録保存に失敗しました (user_id={ctx.user_id})")
        send_reply_text(ctx.line_bot_api, ctx.reply_token, "⚠️ 記録に失敗しました。もう一度お試しください。")
    else:
        # 完了メッセージの生成
        reply_text = "✅ 全員の「元気」を記録しました！\n今日も一日頑張りましょう✨"

        # 確認用ボタン付きメッセージ（Flex Message）
        button_flex = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [{"type": "text", "text": reply_text, "wrap": True}]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "action": {"type": "postback", "label": "📊 記録を確認・修正", "data": "action=check_status"}
                    }
                ]
            }
        }
        ctx.line_bot_api.reply_message(
            ReplyMessageRequest(
                replyToken=ctx.reply_token,
                messages=[FlexMessage(altText="記録完了", contents=FlexContainer.from_dict(button_flex))]
            ),
            _request_timeout=config.LINE_API_REQUEST_TIMEOUT
        )


def _handle_show_health_input(ctx: "PostbackContext") -> None:
    """体調の詳細入力パネル(カルーセル)を返す。"""
    flex_container = create_health_carousel_flex()
    ctx.line_bot_api.reply_message(
        ReplyMessageRequest(
            replyToken=ctx.reply_token,
            messages=[
                TextMessage(text="気になる方の体調を入力してください👇"),
                FlexMessage(altText="体調入力パネル", contents=flex_container)
            ]
        ),
        _request_timeout=config.LINE_API_REQUEST_TIMEOUT
    )


def _handle_child_check(ctx: "PostbackContext") -> None:
    """個別の体調を記録する(status=other は自由文入力の案内のみ)。"""
    status_map = {
        "genki": "😊 元気いっぱい",
        "fever": "🤒 お熱がある",
        "cold": "🤧 鼻水・咳・他",
        "other": "✏️ その他"
    }
    condition_text = status_map.get(ctx.pb.status or "", "その他")
    
    if ctx.pb.status == "other" and ctx.target_name:
        # 次の自由文メッセージは line_handler.py の AI フォールバック(ai_service)経由で処理される
        send_reply_text(ctx.line_bot_api, ctx.reply_token, f"了解です。{ctx.target_name}の様子をメッセージで送ってください📝")
    
    elif ctx.target_name:
        save_ok = sync_run(save_log_async(
            config.SQLITE_TABLE_CHILD,
            ["user_id", "user_name", "child_name", "condition", "timestamp"],
            (ctx.user_id, ctx.user_name, ctx.target_name, condition_text, get_now_iso())
        ))

        if not save_ok:
            logger.error(f"child_check の記録保存に失敗しました (user_id={ctx.user_id}, child={ctx.target_name})")
            send_reply_text(ctx.line_bot_api, ctx.reply_token, "⚠️ 記録に失敗しました。もう一度お試しください。")
        else:
            reply_text = f"📝 {ctx.target_name}: {condition_text}\n記録しました。"

            # サマリ確認ボタン
            button_flex = {
                "type": "bubble",
                "body": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": reply_text}]},
                "footer": {
                    "type": "box", "layout": "vertical",
                    "contents": [{"type": "button", "action": {"type": "postback", "label": "📊 今日の記録確認", "data": "action=check_status"}}]
                }
            }
            ctx.line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=ctx.reply_token,
                    messages=[FlexMessage(altText="記録完了", contents=FlexContainer.from_dict(button_flex))]
                ),
                _request_timeout=config.LINE_API_REQUEST_TIMEOUT
            )


def _handle_check_status(ctx: "PostbackContext") -> None:
    """本日の記録サマリを返す。"""
    summary = get_daily_health_summary()
    # L-L2 (#410): naive datetime.datetime.now()(サーバーのローカルタイムゾーン
    # 依存)は get_today_date_str() 等が前提とするJSTとズレうる。既存の
    # core.utils.get_display_date()(JST基準・同じ"%m/%d"形式)を使う。
    today_disp = get_display_date()
    
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"📅 {today_disp} の記録", "weight": "bold", "size": "md"},
                {"type": "separator", "margin": "md"},
                {"type": "text", "text": summary, "wrap": True, "margin": "md", "lineSpacing": "6px"}
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [
                {
                    "type": "button", 
                    "style": "secondary", 
                    "action": {
                        "type": "postback", 
                        "label": "✏️ 修正する (入力パネル)", 
                        "data": "action=show_health_input"
                    }
                }
            ]
        }
    }
    ctx.line_bot_api.reply_message(
        ReplyMessageRequest(
            replyToken=ctx.reply_token,
            messages=[FlexMessage(altText="記録サマリ", contents=FlexContainer.from_dict(flex_content))]
        ),
        _request_timeout=config.LINE_API_REQUEST_TIMEOUT
    )


def _handle_food_record_direct(ctx: "PostbackContext") -> None:
    """選択された食事メニューをそのまま記録する。"""
    category = ctx.raw.get("category", "その他")
    item = ctx.raw.get("item", "").strip() or "不明なメニュー"
    
    final_rec = f"{category}: {item}"

    # Issue #583: meal_time_categoryは以前固定文字列"Dinner"だった。実際の記録時刻
    # から時間帯を判定する(ここでのcategoryは麺類等の食品ジャンルであり時間帯を
    # 表さないため、時間帯カテゴリは記録時刻そのものを基準にする)。
    save_ok = sync_run(save_log_async(
        config.SQLITE_TABLE_FOOD,
        ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
        (ctx.user_id, ctx.user_name, get_today_date_str(), get_meal_time_category_from_now(), final_rec, get_now_iso())
    ))

    if not save_ok:
        logger.error(f"food_record_direct の記録保存に失敗しました (user_id={ctx.user_id})")
        send_reply_text(ctx.line_bot_api, ctx.reply_token, "⚠️ 記録に失敗しました。もう一度お試しください。")
    else:
        reply_text = f"🍽️ 記録しました！\n【{category}】{item}\n\n今日も一日お疲れ様でした🍵"
        send_reply_text(ctx.line_bot_api, ctx.reply_token, reply_text)


def _handle_food_manual(ctx: "PostbackContext") -> None:
    """自由文でのメニュー入力を促す(次の発話は AI フォールバックが処理)。"""
    category = ctx.raw.get("category", "その他")
    # 次の自由文メッセージは line_handler.py の AI フォールバック(ai_service)経由で処理される

    if "外食" in category:
        prompt_text = "お店の名前（または食べたもの）を入力してください 🍜"
    elif "自炊" in category:
        prompt_text = "作ったメニューを入力してください 🍳"
    else:
        prompt_text = "食べたものを入力してください 📝"
        
    send_reply_text(ctx.line_bot_api, ctx.reply_token, f"了解です！\n{prompt_text}")


# アクション名 -> ハンドラ。新しい Postback を足すときはここに1行追加する(#662)。
POSTBACK_HANDLERS: Dict[str, Callable[["PostbackContext"], None]] = {
    "all_genki": _handle_all_genki,
    "show_health_input": _handle_show_health_input,
    "child_check": _handle_child_check,
    "check_status": _handle_check_status,
    "food_record_direct": _handle_food_record_direct,
    "food_manual": _handle_food_manual,
}


def handle_postback(event: PostbackEvent, line_bot_api: MessagingApi):
    """
    Postbackイベント（ボタン押下等）を処理するハンドラ関数。

    #662: アクション名 -> ハンドラの dict ディスパッチに分解した。失敗時のログには
    どのアクションで落ちたかが残る(以前は232行を単一の try/except で包んでおり、
    "Handle Postback Error" だけでは原因の分岐を特定できなかった)。

    Args:
        event (PostbackEvent): LINEプラットフォームからのPostbackイベントオブジェクト
        line_bot_api (MessagingApi): LINE Messaging APIクライアントインスタンス
    """
    action = ""
    try:
        user_id = event.source.user_id
        reply_token = event.reply_token
        user_name = get_user_name(event, line_bot_api)

        # data形式例: "action=child_check&child=Taro&status=genki"
        raw_dict = dict(parse_qsl(event.postback.data))

        # 保守性(#410): 以前はここに「LinePostbackData(**raw_dict)がバリデーション
        # エラーを送出した場合、actionのみでモデルを再構築するフォールバック」の
        # try/exceptがあったが、pydanticのBaseModelは既定でモデルに定義の無い
        # フィールドを無視する(extra="forbid"等は設定していない)ため、
        # raw_dictにactionキーさえ含まれていれば例外は送出されず、このフォールバックは
        # 到達不能だった。
        pb = LinePostbackData(**raw_dict)

        # アクションの取得（空白除去で堅牢化）
        action = raw_dict.get("action", "").strip()

        ctx = PostbackContext(
            event=event,
            line_bot_api=line_bot_api,
            user_id=user_id,
            user_name=user_name,
            reply_token=reply_token,
            raw=raw_dict,
            pb=pb,
        )

        handler = POSTBACK_HANDLERS.get(action)
        if handler is None:
            # Fail-Safe: 未定義のアクション
            logger.warning(f"Unknown action received: '{action}' from user: {user_id}")
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=[TextMessage(text="⚠️ 不明な操作、または未対応のアクションです。")]
                ),
                _request_timeout=config.LINE_API_REQUEST_TIMEOUT
            )
            return

        handler(ctx)

    except Exception as e:
        logger.error(f"Handle Postback Error (action={action!r}): {e}", exc_info=True)
