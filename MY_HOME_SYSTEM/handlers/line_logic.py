# HOME_SYSTEM/handlers/line_logic.py
import common
import config
from linebot.models import MessageEvent, TextMessage, PostbackEvent
from urllib.parse import parse_qsl
import handlers.ai_logic as ai_logic

# ユーザーの状態管理
USER_INPUT_STATE = {}

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

def handle_postback(event, line_bot_api):
    """
    ボタン押下(Postback)時の処理
    """
    user_id = event.source.user_id
    reply_token = event.reply_token
    user_name = get_user_name(event, line_bot_api)
    
    # data="action=child_check&child=智矢&status=genki" を辞書化
    data = dict(parse_qsl(event.postback.data))
    action = data.get("action")
    target_name = data.get("child")

    if action == "child_check":
        child_name = data.get("child")
        status = data.get("status")
        
        # ステータス定義
        status_info = {
            "genki": ("😊 元気いっぱい", "記録しました！今日も一日楽しく過ごせますように✨"),
            "fever": ("🤒 お熱がある", "心配ですね😢 無理せず温かくして休んでください。"),
            "cold": ("🤧 鼻水・咳", "風邪気味かな？早めに休ませてあげてくださいね。"),
            "other": ("✏️ その他", None) # 手入力へ
        }
        
        condition_text, reply_msg = status_info.get(status, ("その他", None))

        if status == "other":
            # 手入力モードへ移行
            USER_INPUT_STATE[user_id] = f"子供記録_{child_name}"
            common.send_reply(reply_token, [{
                "type": "text",
                "text": f"了解です。{child_name}ちゃんの詳しい様子をメッセージで教えてください📝"
            }])
        else:
            # 即時記録
            common.save_log_generic(config.SQLITE_TABLE_CHILD,
                ["user_id", "user_name", "child_name", "condition", "timestamp"],
                (user_id, user_name, child_name, condition_text, common.get_now_iso()))
            
            # 完了メッセージ（残数付き）
            quota_text = get_quota_text()
            full_msg = f"✅ {child_name}: {condition_text}\n{reply_msg}{quota_text}"
            common.send_reply(reply_token, [{"type": "text", "text": full_msg}])
        
    # ▼ 修正: インデントを戻して if と同じレベルにする
    elif action == "get_history":
        # 直近5件を取得
        history_text = f"📊 【{target_name}】の最近の記録\n"
        
        with common.get_db_cursor() as cur:
            # child_health_recordsから該当者のデータを新しい順に5件取得
            cur.execute(f"""
                SELECT timestamp, condition 
                FROM {config.SQLITE_TABLE_CHILD} 
                WHERE child_name = ? 
                ORDER BY id DESC LIMIT 5
            """, (target_name,))
            rows = cur.fetchall()
        
        if not rows:
            history_text += "\nまだ記録がありません。"
        else:
            for row in rows:
                # 日付整形
                try:
                    dt = datetime.datetime.fromisoformat(row["timestamp"])
                    date_str = dt.strftime("%m/%d %H:%M")
                except:
                    date_str = "??/??"
                
                history_text += f"\n・{date_str}: {row['condition']}"

        quota_text = get_quota_text()
        common.send_reply(reply_token, [{"type": "text", "text": history_text + quota_text}])
    
    else:
        common.logger.info(f"Unknown postback action: {action}")

def process_message(event, line_bot_api):
    """メッセージ処理（既存ロジック改修）"""
    msg = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token
    user_name = get_user_name(event, line_bot_api)

    # === 1. 手入力モード処理 (修正版) ===
    if user_id in USER_INPUT_STATE:
        category = USER_INPUT_STATE[user_id]
        
        # キャンセル処理
        if msg.startswith(("キャンセル", "戻る", "やめる")):
            del USER_INPUT_STATE[user_id]
            common.send_reply(reply_token, [{"type": "text", "text": "入力をキャンセルしました。"}])
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