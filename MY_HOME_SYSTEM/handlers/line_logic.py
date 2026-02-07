# MY_HOME_SYSTEM/handlers/line_logic.py
import config
import asyncio
import json
import sqlite3
import datetime
from urllib.parse import parse_qsl

# ▼▼▼ v3 Imports ▼▼▼
from linebot.v3.messaging import (
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
    QuickReply,
    QuickReplyItem,
    MessageAction,
    PostbackAction
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent
# ▲▲▲ ▲▲▲

# Local Modules
# ▼▼▼ 修正箇所: ロガーの初期化方法を変更 ▼▼▼
# from core.logger import logger  <-- 削除
from core.logger import setup_logging
logger = setup_logging("line_logic")
# ▲▲▲ ▲▲▲
from core.utils import get_now_iso, get_today_date_str
from core.database import save_log_async
import handlers.ai_logic as ai_logic
from models.line import LinePostbackData, UserInputState, InputMode

# ユーザーの状態管理
USER_INPUT_STATE = {}
TARGET_MEMBERS = config.FAMILY_SETTINGS["members"]

# --- Helper Functions ---

def sync_run(coro):
    """
    スレッドプール内で非同期関数(DB保存等)を実行するためのヘルパー。
    Webhookハンドラは別スレッドで動いているため、asyncio.run()で
    新しいイベントループを作って実行して完了を待機する。
    """
    try:
        return asyncio.run(coro)
    except Exception as e:
        logger.error(f"Sync execution error: {e}")

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

def create_quick_reply(items_data: list) -> QuickReply:
    """QuickReply生成 (v3モデル使用)"""
    items = []
    for label, text in items_data:
        # ラベルは最大20文字制限
        safe_label = str(label)[:20]
        items.append(QuickReplyItem(
            action=MessageAction(label=safe_label, text=text)
        ))
    return QuickReply(items=items)

def get_quota_text(api: MessagingApi):
    """今月のメッセージ残数を取得 (v3対応)"""
    try:
        quota = api.get_message_quota()
        if quota and quota.value is not None:
             # total_usage などのプロパティ名はSDKのバージョンによるが、
             # 一般的に value (残り) や totalUsage (使用量) が返る
             return f"\n(当月送信数: {quota.total_usage}通)" 
    except:
        pass
    return ""

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
                    except:
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
        
        # モデルへのマッピング（バリデーション用だが、未知のフィールド許容のためtry-except）
        try:
            pb = LinePostbackData(**raw_dict)
        except Exception:
            # Pydanticモデルに定義されていないフィールドがある場合のフォールバック
            pb = LinePostbackData(action=raw_dict.get("action", "unknown"))

        # アクションの取得（空白除去で堅牢化）
        action = raw_dict.get("action", "").strip()
        target_name = pb.child

        # === 1. 全員元気 (一括記録) ===
        if action == "all_genki":
            timestamp = get_now_iso()
            
            # 全対象メンバーのログを保存
            for name in TARGET_MEMBERS:
                sync_run(save_log_async(
                    config.SQLITE_TABLE_CHILD,
                    ["user_id", "user_name", "child_name", "condition", "timestamp"],
                    (user_id, user_name, name, "😊 元気いっぱい", timestamp)
                ))
            
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
                USER_INPUT_STATE[user_id] = UserInputState(
                    mode=InputMode.CHILD_HEALTH, 
                    target_name=target_name
                )
                send_reply_text(line_bot_api, reply_token, f"了解です。{target_name}の様子をメッセージで送ってください📝")
            
            elif target_name:
                sync_run(save_log_async(
                    config.SQLITE_TABLE_CHILD,
                    ["user_id", "user_name", "child_name", "condition", "timestamp"],
                    (user_id, user_name, target_name, condition_text, get_now_iso())
                ))
                            
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
            today_disp = datetime.datetime.now().strftime("%m/%d")
            
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
            
            sync_run(save_log_async(
                config.SQLITE_TABLE_FOOD,
                ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
                (user_id, user_name, get_today_date_str(), "Dinner", final_rec, get_now_iso())
            ))
            
            reply_text = f"🍽️ 記録しました！\n【{category}】{item}\n\n今日も一日お疲れ様でした🍵"
            send_reply_text(line_bot_api, reply_token, reply_text)

        elif action == "food_manual":
            category = raw_dict.get("category", "その他")
            USER_INPUT_STATE[user_id] = UserInputState(mode=InputMode.MEAL, category=category)
            
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

def handle_message(event, line_bot_api: MessagingApi):
    """メッセージ処理"""
    msg = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token
    user_name = get_user_name(event, line_bot_api)

    # === 1. 手入力モード処理 ===
    if user_id in USER_INPUT_STATE:
        # 割り込みコマンド検知時はモード解除
        if msg.startswith(("食事カテゴリ_", "食事記録_", "子供選択_", "子供記録_", "外出_", "面会_", "お腹記録_")):
            del USER_INPUT_STATE[user_id]
        else:
            state = USER_INPUT_STATE[user_id]
            
            if msg.startswith(("キャンセル", "戻る")):
                del USER_INPUT_STATE[user_id]
                send_reply_text(line_bot_api, reply_token, "キャンセルしました。")
                return
            
            # --- A. 子供の体調入力 ---
            if state.mode == InputMode.CHILD_HEALTH:
                target_child = state.target_name
                sync_run(save_log_async(config.SQLITE_TABLE_CHILD,
                    ["user_id", "user_name", "child_name", "condition", "timestamp"],
                    (user_id, user_name, target_child, msg, get_now_iso())))
                
                del USER_INPUT_STATE[user_id]
                
                # 確認ボタンFlex
                button_flex = {
                    "type": "bubble",
                    "body": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": f"📝 {target_child}: {msg}\n詳細を記録しました。"}]},
                    "footer": {
                        "type": "box", "layout": "vertical",
                        "contents": [{"type": "button", "action": {"type": "postback", "label": "📊 記録を確認", "data": "action=check_status"}}]
                    }
                }
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=reply_token,
                        messages=[FlexMessage(altText="記録完了", contents=FlexContainer.from_dict(button_flex))]
                    )
                )
                return

            # --- B. 食事記録入力 ---
            elif state.mode == InputMode.MEAL:
                category = state.category or "その他"
                if len(msg) > 50:
                    send_reply_text(line_bot_api, reply_token, "長すぎるよ💦 50文字以内でお願い！")
                    return

                final_rec = f"{category}: {msg} (手入力)"
                sync_run(save_log_async(config.SQLITE_TABLE_FOOD, 
                    ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
                    (user_id, user_name, get_today_date_str(), "Dinner", final_rec, get_now_iso())))
                
                del USER_INPUT_STATE[user_id]
                ask_outing_question(line_bot_api, reply_token, final_rec)
                return

            # --- C. お腹記録 ---
            elif state.mode == InputMode.STOMACH:
                pass # 現状AI任せだが拡張用

            # 処理を行ったらreturn
            if user_id in USER_INPUT_STATE: del USER_INPUT_STATE[user_id]
            return

    # === 2. コマンド分岐 ===
    
    # --- 子供の体調記録 ---
    if msg.startswith("子供選択_"):
        child_name = msg.replace("子供選択_", "")
        actions = [(symptom, f"子供記録_{child_name}_{symptom}") for symptom in config.CHILD_SYMPTOMS]
        actions.append(("✨ みんな元気！", "子供記録_全員_元気"))
        qr = create_quick_reply(actions)
        send_reply_text(line_bot_api, reply_token, f"{child_name}ちゃんの様子はどうですか？", qr)
        return

    if msg.startswith("子供記録_"):
        handle_child_record(msg, user_id, user_name, reply_token, line_bot_api)
        return

    # --- 食事記録 ---
    if msg.startswith("食事カテゴリ_"):
        cat = msg.replace("食事カテゴリ_", "")
        menus = config.MENU_OPTIONS.get(cat, config.MENU_OPTIONS.get("その他", ["その他"]))
        
        actions = [(m, f"食事記録_{cat}_{m}") for m in menus]
        actions.append(("✏️ 手入力", f"食事手入力_{cat}"))
        
        qr = create_quick_reply(actions)
        send_reply_text(line_bot_api, reply_token, f"【{cat}】だね！ 具体的なメニューは？", qr)
        return

    if msg.startswith("食事手入力_"):
        cat = msg.replace("食事手入力_", "")
        USER_INPUT_STATE[user_id] = UserInputState(mode=InputMode.MEAL, category=cat)
        send_reply_text(line_bot_api, reply_token, f"わかった！ {cat}のメニューを教えてね📝")
        return

    if msg.startswith("食事記録_"):
        parts = msg.split("_", 2)
        if len(parts) >= 3:
            final_rec = f"{parts[1]}: {parts[2]}"
            sync_run(save_log_async(config.SQLITE_TABLE_FOOD,
                ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
                (user_id, user_name, get_today_date_str(), "Dinner", final_rec, get_now_iso())))
            ask_outing_question(line_bot_api, reply_token, final_rec)
        return
    
    if msg == "食事_スキップ":
        if user_id in USER_INPUT_STATE: del USER_INPUT_STATE[user_id]
        send_reply_text(line_bot_api, reply_token, "はーい、了解です✨ 今日はゆっくり休んでね。")
        return

    # --- 外出・面会 ---
    if msg.startswith("外出_"):
        val = msg.replace("外出_", "")
        sync_run(save_log_async(config.SQLITE_TABLE_DAILY, 
            ["user_id", "user_name", "date", "category", "value", "timestamp"],
            (user_id, user_name, get_today_date_str(), "外出", val, get_now_iso())))
        
        actions = [("はい", "面会_はい"), ("いいえ", "面会_いいえ")]
        qr = create_quick_reply(actions)
        send_reply_text(line_bot_api, reply_token, "誰かと会ったりした？", qr)
        return

    if msg.startswith("面会_"):
        val = msg.replace("面会_", "")
        sync_run(save_log_async(config.SQLITE_TABLE_DAILY,
            ["user_id", "user_name", "date", "category", "value", "timestamp"],
            (user_id, user_name, get_today_date_str(), "面会", val, get_now_iso())))
        send_reply_text(line_bot_api, reply_token, "教えてくれてありがとう！\n今日も一日お疲れ様でした🍵")
        return

    # --- お腹記録 ---
    if msg.startswith("お腹記録_"):
        handle_stomach_record(msg, user_id, user_name, reply_token, line_bot_api)
        return

    # === 3. AI自然言語処理 ===
    # おはようチェック
    if len(msg) <= config.MESSAGE_LENGTH_LIMIT:
        kw = next((k for k in config.OHAYO_KEYWORDS if k in msg.lower()), None)
        if kw:
            sync_run(save_log_async(config.SQLITE_TABLE_OHAYO, 
                ["user_id", "user_name", "message", "timestamp", "recognized_keyword"], 
                (user_id, user_name, msg, get_now_iso(), kw)))
            send_reply_text(line_bot_api, reply_token, f"{user_name}さん、おはようございます！☀️")
            return

    # AI呼び出し (同期的に実行)
    logger.info(f"🤖 AI解析へ: {msg}")
    ai_response = ai_logic.analyze_text_and_execute(msg, user_id, user_name)
    
    if ai_response:
        send_reply_text(line_bot_api, reply_token, ai_response)
        return

    # Fallback (AIも反応なしの場合)
    if any(w in msg for w in ["うんち", "排便", "トイレ", "お腹", "下痢", "便秘"]):
         # Discord通知のみ行う場合
         # sync_run(notification_service.send_push(...)) # 必要なら
         pass 

def ask_outing_question(api: MessagingApi, token: str, food_rec: str):
    actions = [("はい", "外出_はい"), ("いいえ", "外出_いいえ")]
    qr = create_quick_reply(actions)
    send_reply_text(api, token, f"「{food_rec}」を記録したよ📝\n\nあと、今日はお出かけした？", qr)

def handle_child_record(msg, user_id, user_name, reply_token, api: MessagingApi):
    try:
        parts = msg.split("_", 2)
        if len(parts) < 3: return
        target_child, condition = parts[1], parts[2]
        
        if target_child == "全員":
            for child in config.CHILDREN_NAMES:
                sync_run(save_log_async(config.SQLITE_TABLE_CHILD, ["user_id", "user_name", "child_name", "condition", "timestamp"], (user_id, user_name, child, "元気いっぱい", get_now_iso())))
            reply_text = "✨ よかった！みんな元気で何よりです。"
        else:
            sync_run(save_log_async(config.SQLITE_TABLE_CHILD, ["user_id", "user_name", "child_name", "condition", "timestamp"], (user_id, user_name, target_child, condition, get_now_iso())))
            
            if "元気" in condition: reply_text = f"✅ {target_child}ちゃん、元気で安心しました！"
            elif "熱" in condition: reply_text = f"😢 {target_child}ちゃん、お熱ですか...心配ですね。\n無理せず温かくして過ごしてくださいね。"
            elif "怪我" in condition: reply_text = f"🤕 {target_child}ちゃん、痛かったね💦"
            else: reply_text = f"📝 {target_child}ちゃん: {condition}\n記録しました。"

        send_reply_text(api, reply_token, reply_text)

    except Exception as e:
        logger.error(f"子供記録エラー: {e}")

def handle_stomach_record(msg, user_id, user_name, reply_token, api: MessagingApi):
    try:
        parts = msg.split("_", 2)
        if len(parts) < 3: return
        rec_type, condition = parts[1], parts[2]
        
        sync_run(save_log_async(config.SQLITE_TABLE_DEFECATION, 
            ["user_id", "user_name", "record_type", "condition", "timestamp"], 
            (user_id, user_name, rec_type, condition, get_now_iso())))
        
        msg_text = f"✅ {condition} を記録しました！"
        if "腹痛" in condition or "血便" in condition:
            msg_text += "\n無理せずお大事にしてください😢"
        
        send_reply_text(api, reply_token, msg_text)

    except Exception as e:
        logger.error(f"お腹記録エラー: {e}")