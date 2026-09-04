# MY_HOME_SYSTEM/handlers/line_logic.py
import config
import asyncio
import sqlite3
import datetime
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
from core.utils import get_now_iso, get_today_date_str, get_display_date
from core.database import save_log_async, save_logs_batch_async
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
            )
        )
    except Exception as e:
        logger.error(f"Reply Error: {e}")

def get_user_name(event, line_bot_api: MessagingApi) -> str:
    """プロファイル取得 (v3対応)"""
    try:
        user_id = event.source.user_id
        if event.source.type == "group":
            group_id = event.source.group_id
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
            return profile.display_name
        elif event.source.type == "user":
            profile = line_bot_api.get_profile(user_id)
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
    
    # common.get_db_cursor の代わりに直接接続
    try:
        with sqlite3.connect(config.SQLITE_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
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
                    icon = "✅" if "元気" in status else "⚠️"
                    summary_lines.append(f"{icon} {name}: {status} ({time_str})")
                else:
                    summary_lines.append(f"❓ {name}: (未記録)")
    except Exception as e:
        logger.error(f"DB Read Error: {e}")
        return "（データ取得エラー）"
    
    return "\n".join(summary_lines)


# --- Handlers ---

def handle_postback(event: PostbackEvent, line_bot_api: MessagingApi):
    """
    Postbackイベント（ボタン押下等）を処理するハンドラ関数。
    
    Args:
        event (PostbackEvent): LINEプラットフォームからのPostbackイベントオブジェクト
        line_bot_api (MessagingApi): LINE Messaging APIクライアントインスタンス
    """
    try:
        # ユーザー情報の取得
        user_id = event.source.user_id
        reply_token = event.reply_token
        user_name = get_user_name(event, line_bot_api)
        
        # Postbackデータのパース
        # data形式例: "action=child_check&child=Taro&status=genki"
        raw_dict = dict(parse_qsl(event.postback.data))

        # 保守性(#410): 以前はここに「LinePostbackData(**raw_dict)がバリデーション
        # エラーを送出した場合、actionのみでモデルを再構築するフォールバック」の
        # try/exceptがあったが、pydanticのBaseModelは既定でモデルに定義の無い
        # フィールドを無視する(extra="forbid"等は設定していない)ため、
        # raw_dictにactionキーさえ含まれていれば例外は送出されず、このフォールバックは
        # 到達不能だった。本ボットが生成するpostback.dataは常に"action=..."を含むため
        # 実質発火しない分岐だった。想定外の入力(action無し等)で万一送出された場合は
        # 呼び出し元 handle_postback の末尾except Exceptionでログ・握り潰しされる。
        pb = LinePostbackData(**raw_dict)

        # アクションの取得（空白除去で堅牢化）
        action = raw_dict.get("action", "").strip()
        target_name = pb.child

        # === 1. 全員元気 (一括記録) ===
        if action == "all_genki":
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
                [(user_id, user_name, name, "😊 元気いっぱい", timestamp) for name in TARGET_MEMBERS]
            ))

            if not save_all_ok:
                logger.error(f"all_genki の記録保存に失敗しました (user_id={user_id})")
                send_reply_text(line_bot_api, reply_token, "⚠️ 記録に失敗しました。もう一度お試しください。")
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
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=reply_token,
                        messages=[FlexMessage(altText="記録完了", contents=FlexContainer.from_dict(button_flex))]
                    )
                )

        # === 2. 詳細入力パネル表示 ===
        elif action == "show_health_input":
            flex_container = create_health_carousel_flex()
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=[
                        TextMessage(text="気になる方の体調を入力してください👇"),
                        FlexMessage(altText="体調入力パネル", contents=flex_container)
                    ]
                )
            )

        # === 3. 個別記録 ===
        elif action == "child_check":
            status_map = {
                "genki": "😊 元気いっぱい",
                "fever": "🤒 お熱がある",
                "cold": "🤧 鼻水・咳・他",
                "other": "✏️ その他"
            }
            condition_text = status_map.get(pb.status or "", "その他")
            
            if pb.status == "other" and target_name:
                # 次の自由文メッセージは line_handler.py の AI フォールバック(ai_service)経由で処理される
                send_reply_text(line_bot_api, reply_token, f"了解です。{target_name}の様子をメッセージで送ってください📝")
            
            elif target_name:
                save_ok = sync_run(save_log_async(
                    config.SQLITE_TABLE_CHILD,
                    ["user_id", "user_name", "child_name", "condition", "timestamp"],
                    (user_id, user_name, target_name, condition_text, get_now_iso())
                ))

                if not save_ok:
                    logger.error(f"child_check の記録保存に失敗しました (user_id={user_id}, child={target_name})")
                    send_reply_text(line_bot_api, reply_token, "⚠️ 記録に失敗しました。もう一度お試しください。")
                else:
                    reply_text = f"📝 {target_name}: {condition_text}\n記録しました。"

                    # サマリ確認ボタン
                    button_flex = {
                        "type": "bubble",
                        "body": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": reply_text}]},
                        "footer": {
                            "type": "box", "layout": "vertical",
                            "contents": [{"type": "button", "action": {"type": "postback", "label": "📊 今日の記録確認", "data": "action=check_status"}}]
                        }
                    }
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            replyToken=reply_token,
                            messages=[FlexMessage(altText="記録完了", contents=FlexContainer.from_dict(button_flex))]
                        )
                    )

        # === 4. 記録確認 & 修正 ===
        elif action == "check_status":
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
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=[FlexMessage(altText="記録サマリ", contents=FlexContainer.from_dict(flex_content))]
                )
            )

        # === 5. 食事アンケート回答 ===
        elif action == "food_record_direct":
            category = raw_dict.get("category", "その他")
            item = raw_dict.get("item", "").strip() or "不明なメニュー"
            
            final_rec = f"{category}: {item}"

            save_ok = sync_run(save_log_async(
                config.SQLITE_TABLE_FOOD,
                ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
                (user_id, user_name, get_today_date_str(), "Dinner", final_rec, get_now_iso())
            ))

            if not save_ok:
                logger.error(f"food_record_direct の記録保存に失敗しました (user_id={user_id})")
                send_reply_text(line_bot_api, reply_token, "⚠️ 記録に失敗しました。もう一度お試しください。")
            else:
                reply_text = f"🍽️ 記録しました！\n【{category}】{item}\n\n今日も一日お疲れ様でした🍵"
                send_reply_text(line_bot_api, reply_token, reply_text)

        elif action == "food_manual":
            category = raw_dict.get("category", "その他")
            # 次の自由文メッセージは line_handler.py の AI フォールバック(ai_service)経由で処理される

            if "外食" in category:
                prompt_text = "お店の名前（または食べたもの）を入力してください 🍜"
            elif "自炊" in category:
                prompt_text = "作ったメニューを入力してください 🍳"
            else:
                prompt_text = "食べたものを入力してください 📝"
                
            send_reply_text(line_bot_api, reply_token, f"了解です！\n{prompt_text}")

        # === Fail-Safe: 未定義のアクション ===
        else:
            logger.warning(f"Unknown action received: '{action}' from user: {user_id}")
            # ユーザーへのフィードバック
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=[TextMessage(text="⚠️ 不明な操作、または未対応のアクションです。")]
                )
            )

    except Exception as e:
        logger.error(f"Handle Postback Error: {e}", exc_info=True)
