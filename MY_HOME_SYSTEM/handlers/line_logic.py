# HOME_SYSTEM/handlers/line_logic.py
import common
import config
from linebot.models import MessageEvent, TextMessage, PostbackEvent
from urllib.parse import parse_qsl
import handlers.ai_logic as ai_logic
import datetime

# ユーザーの状態管理
USER_INPUT_STATE = {}
TARGET_MEMBERS = config.FAMILY_SETTINGS["members"]

def get_user_name(event, line_bot_api) -> str:
    """プロファイル取得（変更なし）"""
    try:
        if event.source.type == "group":
            return line_bot_api.get_group_member_profile(event.source.group_id, event.source.user_id).display_name
        elif event.source.type == "user":
            return line_bot_api.get_profile(event.source.user_id).display_name
    except Exception:
        pass
    return "家族のみんな"

def create_quick_reply(items_data: list) -> dict:
    """QuickReply生成（変更なし）"""
    items = []
    for label, text in items_data:
        items.append({
            "type": "action",
            "action": {"type": "message", "label": label[:20], "text": text}
        })
    return {"items": items}

def get_quota_text():
    """今月のメッセージ残数を取得してテキスト化"""
    try:
        quota = common.get_line_message_quota()
        if quota and quota.get('remain') is not None:
            return f"\n(今月の残り: {quota['remain']}通)"
    except:
        pass
    return ""

# ▼▼▼ 追加: 入力用カルーセルを作成する関数 ▼▼▼
def create_health_carousel_flex():
    """詳細入力用カルーセルを作成"""
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
                    {"type": "separator", "margin": "md"},
                    {"type": "button", "style": "link", "height": "sm", "margin": "md",
                     "action": {"type": "postback", "label": "📊 今日の記録確認", "data": "action=check_status"}}
                ]
            }
        }
        bubbles.append(bubble)

    return {"type": "flex", "altText": "体調入力パネル", "contents": {"type": "carousel", "contents": bubbles}}

# ▼▼▼ 追加: 今日の記録サマリを取得する関数 ▼▼▼
def get_daily_health_summary():
    """今日の記録サマリを取得"""
    today_str = common.get_today_date_str() # YYYY-MM-DD
    summary_lines = []
    
    with common.get_db_cursor() as cur:
        for name in TARGET_MEMBERS:
            
            # 今日の最新の記録を取得
            cur.execute(f"""
                SELECT condition, timestamp FROM {config.SQLITE_TABLE_CHILD}
                WHERE child_name = ? AND timestamp LIKE ?
                ORDER BY id DESC LIMIT 1
            """, (name, f"{today_str}%"))
            row = cur.fetchone()
            
            if row:
                # 時刻抽出
                try:
                    time_str = datetime.datetime.fromisoformat(row["timestamp"]).strftime("%H:%M")
                except:
                    time_str = "??:??"
                status = row["condition"]
                # 絵文字装飾
                icon = "✅" if "元気" in status else "⚠️"
                summary_lines.append(f"{icon} {name}: {status} ({time_str})")
            else:
                summary_lines.append(f"❓ {name}: (未記録)")
    
    return "\n".join(summary_lines)

def handle_postback(event, line_bot_api):
    """Postback処理"""
    try:
        user_id = event.source.user_id
        reply_token = event.reply_token
        user_name = get_user_name(event, line_bot_api)
        
        data = dict(parse_qsl(event.postback.data))
        action = data.get("action")
        target_name = data.get("child")
        
        quota_text = get_quota_text()

        # === 1. 全員元気 (一括) ===
        if action == "all_genki":
            timestamp = common.get_now_iso()
            for name in TARGET_MEMBERS:
                common.save_log_generic(config.SQLITE_TABLE_CHILD,
                    ["user_id", "user_name", "child_name", "condition", "timestamp"],
                    (user_id, user_name, name, "😊 元気いっぱい", timestamp))
            
            reply_msg = f"✅ 全員の「元気」を記録しました！\n今日も一日頑張りましょう✨\n\n[詳細確認]ボタンで修正できます。{quota_text}"
            
            # 確認ボタン付きのメッセージを返す
            buttons = {
                "type": "template",
                "altText": "記録完了",
                "template": {
                    "type": "buttons",
                    "text": reply_msg[:160], # Text limit precaution
                    "actions": [{"type": "postback", "label": "📊 記録を確認・修正", "data": "action=check_status"}]
                }
            }
            common.send_reply(reply_token, [buttons])

        # === 2. 詳細入力パネル表示 ===
        elif action == "show_health_input":
            flex_msg = create_health_carousel_flex()
            common.send_reply(reply_token, [{"type": "text", "text": "気になる方の体調を入力してください👇"}, flex_msg])

        # === 3. 個別記録 ===
        elif action == "child_check":
            status = data.get("status")
            status_map = {
                "genki": "😊 元気いっぱい",
                "fever": "🤒 お熱がある",
                "cold": "🤧 鼻水・咳・他",
                "other": "✏️ その他"
            }
            condition_text = status_map.get(status, "その他")
            
            if status == "other":
                USER_INPUT_STATE[user_id] = f"子供記録_{target_name}"
                common.send_reply(reply_token, [{"type": "text", "text": f"了解です。{target_name}の様子をメッセージで送ってください📝"}])
            else:
                common.save_log_generic(config.SQLITE_TABLE_CHILD,
                    ["user_id", "user_name", "child_name", "condition", "timestamp"],
                    (user_id, user_name, target_name, condition_text, common.get_now_iso()))
                
                # 記録後のフィードバック（サマリ確認へ誘導）
                reply_text = f"📝 {target_name}: {condition_text}\n記録しました。"
                # サマリボタンを付ける
                buttons = {
                    "type": "template",
                    "altText": "記録完了",
                    "template": {
                        "type": "buttons",
                        "text": reply_text,
                        "actions": [{"type": "postback", "label": "📊 今日の記録確認", "data": "action=check_status"}]
                    }
                }
                common.send_reply(reply_token, [buttons])

        # === 4. 記録確認 & 修正 ===
        elif action == "check_status":
            summary = get_daily_health_summary()
            today_disp = datetime.datetime.now().strftime("%m/%d")
            
            # Flex Messageでサマリを表示
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
                        # ▼▼▼ 修正箇所: label は action の中に入れます ▼▼▼
                        {
                            "type": "button", 
                            "style": "secondary", 
                            # "label": "..." ← ここにあったのが間違い
                            "action": {
                                "type": "postback", 
                                "label": "✏️ 修正する (入力パネル)", # ここが正解
                                "data": "action=show_health_input"
                            }
                        }
                        # ▲▲▲▲▲▲
                    ]
                }
            }
            common.send_reply(reply_token, [{"type": "flex", "altText": "記録サマリ", "contents": flex_content}])

        else:
            common.logger.info(f"Unknown action: {action}")

    except Exception as e:
        common.logger.error(f"Handle Postback Error: {e}")
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"エラー: {e}"}], target="discord", channel="error")

def process_message(event, line_bot_api):
    """メッセージ処理（既存ロジック改修）"""
    msg = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token
    user_name = get_user_name(event, line_bot_api)

    # === 1. 手入力モード処理 (修正版) ===
    if user_id in USER_INPUT_STATE:
        category = USER_INPUT_STATE[user_id]
        if msg.startswith(("キャンセル", "戻る")):
            del USER_INPUT_STATE[user_id]
            common.send_reply(reply_token, [{"type": "text", "text": "キャンセルしました。"}])
            return

        if category.startswith("子供記録_"):
            target_child = category.replace("子供記録_", "")
            common.save_log_generic(config.SQLITE_TABLE_CHILD,
                ["user_id", "user_name", "child_name", "condition", "timestamp"],
                (user_id, user_name, target_child, msg, common.get_now_iso()))
            del USER_INPUT_STATE[user_id]
            
            # 手入力完了後もサマリ確認ボタンを出す
            buttons = {
                "type": "template", "altText": "記録完了",
                "template": {
                    "type": "buttons", "text": f"📝 {target_child}: {msg}\n詳細を記録しました。",
                    "actions": [{"type": "postback", "label": "📊 記録を確認", "data": "action=check_status"}]
                }
            }
            common.send_reply(reply_token, [buttons])
            return

        # ▼▼▼ 追加: 子供記録の手入力処理 ▼▼▼
        if category.startswith("子供記録_"):
            target_child = category.replace("子供記録_", "")
            
            # DB保存
            common.save_log_generic(config.SQLITE_TABLE_CHILD,
                ["user_id", "user_name", "child_name", "condition", "timestamp"],
                (user_id, user_name, target_child, msg, common.get_now_iso()))
            
            del USER_INPUT_STATE[user_id]
            
            # 完了通知
            quota_text = get_quota_text()
            common.send_reply(reply_token, [{
                "type": "text", 
                "text": f"詳しくありがとうございます！\n📝 {target_child}: {msg}\n記録しました。お大事にしてくださいね。{quota_text}"
            }])
            return
        # ▲▲▲ ここまで ▲▲▲

        # 既存: 食事記録の手入力処理
        if category.startswith("食事") or category in ["自炊", "外食", "その他"]: # カテゴリ名の揺らぎに対応
            if len(msg) > 50:
                common.send_reply(reply_token, [{"type": "text", "text": "長すぎるよ💦 50文字以内でお願い！"}])
                return

            final_rec = f"{category}: {msg} (手入力)"
            
            common.save_log_generic(config.SQLITE_TABLE_FOOD, 
                ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
                (user_id, user_name, common.get_today_date_str(), "Dinner", final_rec, common.get_now_iso()))
            
            del USER_INPUT_STATE[user_id]
            
            # 次の質問へ
            ask_outing_question(reply_token, final_rec)
            return
            
        # 該当しないカテゴリがStateに残っていた場合の安全策
        del USER_INPUT_STATE[user_id]

    # === 2. コマンド分岐 ===
    
    # --- 子供の体調記録 ---
    if msg.startswith("子供選択_"):
        child_name = msg.replace("子供選択_", "")
        actions = [(symptom, f"子供記録_{child_name}_{symptom}") for symptom in config.CHILD_SYMPTOMS]
        actions.append(("✨ みんな元気！", "子供記録_全員_元気"))
        
        reply_msg = {
            "type": "text",
            "text": f"{child_name}ちゃんの様子はどうですか？",
            "quickReply": create_quick_reply(actions)
        }
        common.send_reply(reply_token, [reply_msg])
        return

    if msg.startswith("子供記録_"):
        handle_child_record(msg, user_id, user_name, reply_token)
        return

    # --- 食事記録 ---
    if msg.startswith("食事カテゴリ_"):
        cat = msg.replace("食事カテゴリ_", "")
        menus = config.MENU_OPTIONS.get(cat, config.MENU_OPTIONS["その他"])
        
        actions = [(m, f"食事記録_{cat}_{m}") for m in menus]
        actions.append(("✏️ 手入力", f"食事手入力_{cat}"))
        
        reply_msg = {
            "type": "text", 
            "text": f"【{cat}】だね！ 美味しそう✨\n具体的なメニューはどれ？", 
            "quickReply": create_quick_reply(actions)
        }
        common.send_reply(reply_token, [reply_msg])
        return

    if msg.startswith("食事手入力_"):
        cat = msg.replace("食事手入力_", "")
        USER_INPUT_STATE[user_id] = cat
        common.send_reply(reply_token, [{"type": "text", "text": f"わかった！ {cat}のメニューを教えてね📝"}])
        return

    if msg.startswith("食事記録_"):
        parts = msg.split("_", 2)
        if len(parts) >= 3:
            final_rec = f"{parts[1]}: {parts[2]}"
            common.save_log_generic(config.SQLITE_TABLE_FOOD,
                ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
                (user_id, user_name, common.get_today_date_str(), "Dinner", final_rec, common.get_now_iso()))
            ask_outing_question(reply_token, final_rec)
        return
    
    if msg == "食事_スキップ":
        if user_id in USER_INPUT_STATE: del USER_INPUT_STATE[user_id]
        common.send_reply(reply_token, [{"type": "text", "text": "はーい、了解です✨ 今日はゆっくり休んでね。"}])
        return

    # --- 外出・面会 ---
    if msg.startswith("外出_"):
        val = msg.replace("外出_", "")
        common.save_log_generic(config.SQLITE_TABLE_DAILY, 
            ["user_id", "user_name", "date", "category", "value", "timestamp"],
            (user_id, user_name, common.get_today_date_str(), "外出", val, common.get_now_iso()))
        
        actions = [("はい", "面会_はい"), ("いいえ", "面会_いいえ")]
        common.send_reply(reply_token, [{"type": "text", "text": "誰かと会ったりした？", "quickReply": create_quick_reply(actions)}])
        return

    if msg.startswith("面会_"):
        val = msg.replace("面会_", "")
        common.save_log_generic(config.SQLITE_TABLE_DAILY,
            ["user_id", "user_name", "date", "category", "value", "timestamp"],
            (user_id, user_name, common.get_today_date_str(), "面会", val, common.get_now_iso()))
        common.send_reply(reply_token, [{"type": "text", "text": "教えてくれてありがとう！\n今日も一日お疲れ様でした🍵 ゆっくり休んでね。"}])
        return

    # --- お腹記録 ---
    if msg.startswith("お腹記録_"):
        handle_stomach_record(msg, user_id, user_name, reply_token)
        return
    
    # トリガーワード検知 (お腹系)
    # ↓ この既存ロジックは AIの方が賢いので削除またはコメントアウトしても良いですが、
    #   念のため残しておき、AIが処理しなかった場合のバックアップにすることも可能です。
    #   今回は「AIに任せる」ため、ここに来る前にAI処理を挟みます。

    # === 3. AI自然言語処理 (ここを追加！) ===
    # 既存のコマンドに当てはまらなかった場合、Geminiに解析させる
    
    # 短すぎる挨拶などはOHAYOロジックに任せるため、ある程度の長さか、特定キーワードがある場合
    # または「AIにお任せ」スタイルなら、すべてのメッセージを投げても良いですが、
    # APIコストとレスポンス速度を考慮し、「コマンド以外」かつ「挨拶以外」で回すのが賢明です。
    
    # 先に「おはよう」チェックを行う (既存ロジック)
    if len(msg) <= config.MESSAGE_LENGTH_LIMIT:
        kw = next((k for k in config.OHAYO_KEYWORDS if k in msg.lower()), None)
        if kw:
            common.save_log_generic(config.SQLITE_TABLE_OHAYO, 
                ["user_id", "user_name", "message", "timestamp", "recognized_keyword"], 
                (user_id, user_name, msg, common.get_now_iso(), kw))
            common.logger.info(f"[OHAYO] {user_name} -> {msg}")
            # おはようの場合はここで終了（AIには投げない）
            return

    # ここでAI呼び出し！
    common.logger.info(f"🤖 AI解析へ: {msg}")
    ai_response = ai_logic.analyze_text_and_execute(msg, user_id, user_name)
    
    if ai_response:
        # AIが何かを処理した、または雑談を返した場合はそれを返信
        common.send_reply(reply_token, [{"type": "text", "text": ai_response}])
        return

    # AIも反応しなかった場合（エラーや該当なし）、従来のお腹トリガーなどへ
    if any(w in msg for w in ["うんち", "排便", "トイレ", "お腹", "下痢", "便秘"]):
         common.send_push(config.LINE_USER_ID, [
             {"type": "text", "text": "🚽 [Discord通知]\nお腹の調子はどうですか？\n記録なら「うんち出た」のように教えてね。"}
         ], target="discord")
         return 


def ask_outing_question(token, food_rec):
    actions = [("はい", "外出_はい"), ("いいえ", "外出_いいえ")]
    common.send_reply(token, [{
        "type": "text", 
        "text": f"「{food_rec}」を記録したよ📝\n\nあと、今日はお出かけした？", 
        "quickReply": create_quick_reply(actions)
    }])

def handle_child_record(msg, user_id, user_name, reply_token):
    try:
        parts = msg.split("_", 2)
        if len(parts) < 3: return
        target_child, condition = parts[1], parts[2]
        
        # 保存
        if target_child == "全員":
            for child in config.CHILDREN_NAMES:
                common.save_log_generic(config.SQLITE_TABLE_CHILD, ["user_id", "user_name", "child_name", "condition", "timestamp"], (user_id, user_name, child, "元気いっぱい", common.get_now_iso()))
            reply_text = "✨ よかった！みんな元気で何よりです。\n今日も一日頑張りましょう！"
        else:
            common.save_log_generic(config.SQLITE_TABLE_CHILD, ["user_id", "user_name", "child_name", "condition", "timestamp"], (user_id, user_name, target_child, condition, common.get_now_iso()))
            
            # 応答生成
            if "元気" in condition: reply_text = f"✅ {target_child}ちゃん、元気で安心しました！"
            elif "熱" in condition: reply_text = f"😢 {target_child}ちゃん、お熱ですか...心配ですね。\n無理せず温かくして過ごしてくださいね。"
            elif "怪我" in condition: reply_text = f"🤕 {target_child}ちゃん、痛かったね💦\n早く治りますように。"
            else: reply_text = f"📝 {target_child}ちゃん: {condition}\n記録しました。様子を見てあげてくださいね。"

            # 重篤な場合はDiscordにも通知
            if "熱" in condition or "怪我" in condition:
                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"🚨【体調不良】{target_child}: {condition}"}], target="discord", channel="notify")

        common.send_reply(reply_token, [{"type": "text", "text": reply_text}])

    except Exception as e:
        common.logger.error(f"子供記録エラー: {e}")

def handle_stomach_record(msg, user_id, user_name, reply_token):
    try:
        parts = msg.split("_", 2)
        if len(parts) < 3: return
        rec_type, condition = parts[1], parts[2]
        
        common.save_log_generic(config.SQLITE_TABLE_DEFECATION, 
            ["user_id", "user_name", "record_type", "condition", "timestamp"], 
            (user_id, user_name, rec_type, condition, common.get_now_iso()))
        
        # Discordへ通知
        msg_text = f"✅ [Discord通知]\n{condition} を記録しました！"
        if "腹痛" in condition or "血便" in condition:
            msg_text += "\n無理せずお大事にしてください😢"
        
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_text}], target="discord")

    except Exception as e:
        common.logger.error(f"お腹記録エラー: {e}")