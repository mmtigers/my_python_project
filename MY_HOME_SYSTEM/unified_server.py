# HOME_SYSTEM/unified_server.py
from fastapi import FastAPI, Request, Header, HTTPException
from contextlib import asynccontextmanager
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, SourceGroup, SourceUser
import uvicorn
import config
import common
import switchbot_get_device_list as sb_tool

logger = common.setup_logging("server")
USER_INPUT_STATE = {}

# クールタイム管理用 (デバイスID: 最終通知時刻)
LAST_NOTIFY_TIME = {}
COOLDOWN_SECONDS = 300  # 5分間は連続通知しない

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("システム起動！準備運動中...")
    sb_tool.fetch_device_name_cache()
    yield
    logger.info("システム終了。お疲れ様でした🍵")

app = FastAPI(lifespan=lifespan)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)

@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)):
    body = (await request.body()).decode('utf-8')
    try: handler.handle(body, x_line_signature)
    except InvalidSignatureError: raise HTTPException(status_code=400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token
    
    # 1. スキップ
    if msg == "食事_スキップ":
        if user_id in USER_INPUT_STATE: del USER_INPUT_STATE[user_id]
        common.send_reply(reply_token, [{"type": "text", "text": "はーい、了解です✨ 今日はゆっくり休んでね。"}])
        return

    # 2. 手入力モード
    if user_id in USER_INPUT_STATE:
        if msg.startswith(("食事", "外出", "面会")):
            del USER_INPUT_STATE[user_id]
        else:
            category = USER_INPUT_STATE[user_id]
            if len(msg) > 50:
                common.send_reply(reply_token, [{"type": "text", "text": "ごめんね、もう少し短く教えてくれる？💦 (50文字以内)"}])
                return
            
            user_name = get_user_name(event)
            final_rec = f"{category}: {msg} (手入力)"
            
            if save_food_log(user_id, user_name, final_rec):
                del USER_INPUT_STATE[user_id]
                ask_outing_question(reply_token, final_rec)
            else:
                common.send_reply(reply_token, [{"type": "text", "text": "あら、記録に失敗しちゃったみたい😢 もう一度試してみて？"}])
            return

    # 1. 子供選択時 ("子供選択_智矢")
    if msg.startswith("子供選択_"):
        child_name = msg.replace("子供選択_", "")
        
        # 症状ボタンを表示
        items = []
        for symptom in config.CHILD_SYMPTOMS:
            label = symptom[:20] 
            # タップで記録: "子供記録_智矢_お熱がある"
            items.append({
                "type": "action", 
                "action": {"type": "message", "label": label, "text": f"子供記録_{child_name}_{symptom}"}
            })
            
        common.send_reply(reply_token, [{
            "type": "text", 
            "text": f"{child_name}ちゃんの様子はどうですか？", 
            "quickReply": {"items": items}
        }])
        return

    # 2. 記録実行 ("子供記録_智矢_お熱がある" or "子供記録_全員_元気")
    if msg.startswith("子供記録_"):
        try:
            parts = msg.split("_", 2) # 子供記録, 名前, 状態
            if len(parts) < 3: return
            
            target_child = parts[1]
            condition = parts[2]
            user_name = get_user_name(event)
            
            # 全員元気の場合
            if target_child == "全員":
                for child in config.CHILDREN_NAMES:
                    save_child_log(user_id, user_name, child, "元気いっぱい")
                reply_msg = "✨ よかった！みんな元気で何よりです。\n今日も一日頑張りましょう！"
                
            else:
                # 個別記録
                save_child_log(user_id, user_name, target_child, condition)
                
                # 症状に応じた優しい返信
                if "元気" in condition:
                    reply_msg = f"✅ {target_child}ちゃん、元気で安心しました！"
                elif "熱" in condition:
                    reply_msg = f"😢 {target_child}ちゃん、お熱ですか...心配ですね。\n無理せず温かくして過ごしてくださいね。"
                    # 念のためDiscordにも通知
                    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"🚨【体調不良】{target_child}: {condition}"}], target="discord")
                elif "鼻水" in condition or "咳" in condition:
                    reply_msg = f"🤧 {target_child}ちゃん、お大事に。\n酷くならないといいですね🍀"
                elif "怪我" in condition:
                     reply_msg = f"🤕 {target_child}ちゃん、痛かったね💦\n早く治りますように。"
                else:
                    reply_msg = f"📝 {target_child}ちゃん: {condition}\n記録しました。様子を見てあげてくださいね。"

            common.send_reply(reply_token, [{"type": "text", "text": reply_msg}])
            
        except Exception as e:
            logger.error(f"子供記録エラー: {e}")
        return


    # 3. 食事カテゴリ
    if msg.startswith("食事カテゴリ_"):
        cat = msg.replace("食事カテゴリ_", "")
        menus = config.MENU_OPTIONS.get(cat, config.MENU_OPTIONS["その他"])
        items = [{"type": "action", "action": {"type": "message", "label": m[:20], "text": f"食事記録_{cat}_{m}"}} for m in menus]
        items.append({"type": "action", "action": {"type": "message", "label": "✏️ 手入力", "text": f"食事手入力_{cat}"}})
        
        common.send_reply(reply_token, [{"type": "text", "text": f"【{cat}】だね！ 美味しそう✨\n具体的なメニューはどれ？", "quickReply": {"items": items}}])
        return

    # 4. 手入力要求
    if msg.startswith("食事手入力_"):
        cat = msg.replace("食事手入力_", "")
        USER_INPUT_STATE[user_id] = cat
        common.send_reply(reply_token, [{"type": "text", "text": f"わかった！ {cat}のメニューを教えてね📝"}])
        return

    # 5. 食事記録確定
    if msg.startswith("食事記録_"):
        try:
            parts = msg.split("_", 2)
            if len(parts) >= 3:
                final_rec = f"{parts[1]}: {parts[2]}"
                if save_food_log(user_id, get_user_name(event), final_rec):
                    ask_outing_question(reply_token, final_rec)
        except: pass
        return

    # 6. 外出・面会
    if msg.startswith("外出_"):
        save_daily_log(user_id, get_user_name(event), "外出", msg.replace("外出_", ""))
        items = [{"type": "action", "action": {"type": "message", "label": l, "text": f"面会_{l}"}} for l in ["はい", "いいえ"]]
        common.send_reply(reply_token, [{"type": "text", "text": "誰かと会ったりした？", "quickReply": {"items": items}}])
        return

    if msg.startswith("面会_"):
        save_daily_log(user_id, get_user_name(event), "面会", msg.replace("面会_", ""))
        common.send_reply(reply_token, [{"type": "text", "text": "教えてくれてありがとう！\n今日も一日お疲れ様でした🍵 ゆっくり休んでね。"}])
        return

    # 7. おはよう
    if len(msg) <= config.MESSAGE_LENGTH_LIMIT:
        kw = next((k for k in config.OHAYO_KEYWORDS if k in msg.lower()), None)
        if kw:
            user = get_user_name(event)
            common.save_log_generic(config.SQLITE_TABLE_OHAYO, ["user_id", "user_name", "message", "timestamp", "recognized_keyword"], (user_id, user, msg, common.get_now_iso(), kw))
            logger.info(f"[OHAYO] {user} -> {msg}")
    
    # A. 排便・お腹記録のトリガー
    if any(w in msg for w in ["うんち", "ウンチ", "排便", "トイレ", "便", "お腹", "下痢", "便秘"]):
        if not msg.startswith("お腹記録_"):
            # Discordに通知テスト (ボタンは出ないのでテキストで案内)
            text_msg = "🚽 [Discord通知テスト]\nお腹の調子はどうですか？\n\n記録するにはLINEで以下のように送ってください：\n「お腹記録_排便_バナナ」\n「お腹記録_症状_腹痛あり」"
            
            # target="discord" を指定して送信
            common.send_push(config.LINE_USER_ID, [{"type": "text", "text": text_msg}], target="discord")
            return

    # B. 記録実行
    if msg.startswith("お腹記録_"):
        try:
            parts = msg.split("_", 2)
            if len(parts) < 3: return
            
            rec_type = parts[1]
            condition = parts[2]
            user_name = get_user_name(event)

            # DB保存
            cols = ["user_id", "user_name", "record_type", "condition", "timestamp"]
            vals = (user_id, user_name, rec_type, condition, common.get_now_iso())
            
            if common.save_log_generic(config.SQLITE_TABLE_DEFECATION, cols, vals):
                # Discordに成功通知
                if "血便" in condition or "腹痛" in condition:
                    reply_text = f"✅ [Discord通知]\n{condition} を記録しました。\n無理せずお大事にしてください😢"
                else:
                    reply_text = f"✅ [Discord通知]\n{condition} を記録しました！"
                
                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": reply_text}], target="discord")
            else:
                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": "❌ 記録に失敗しました"}], target="discord")
                
        except Exception as e:
            logger.error(f"お腹記録エラー: {e}")
        return



def ask_outing_question(token, food_rec):
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": f"外出_{l}"}} for l in ["はい", "いいえ"]]
    common.send_reply(token, [{"type": "text", "text": f"「{food_rec}」を記録したよ📝\n\nあと、今日はお出かけした？", "quickReply": {"items": items}}])

def get_user_name(event):
    try:
        if isinstance(event.source, SourceGroup): return line_bot_api.get_group_member_profile(event.source.group_id, event.source.user_id).display_name
        elif isinstance(event.source, SourceUser): return line_bot_api.get_profile(event.source.user_id).display_name
    except: pass
    return "Unknown"

def save_child_log(uid, uname, child, cond):
    cols = ["user_id", "user_name", "child_name", "condition", "timestamp"]
    vals = (uid, uname, child, cond, common.get_now_iso())
    return common.save_log_generic(config.SQLITE_TABLE_CHILD, cols, vals)

def save_food_log(uid, uname, content):
    return common.save_log_generic(config.SQLITE_TABLE_FOOD, ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"], (uid, uname, common.get_today_date_str(), "Dinner", content, common.get_now_iso()))

def save_daily_log(uid, uname, cat, val):
    return common.save_log_generic(config.SQLITE_TABLE_DAILY, ["user_id", "user_name", "date", "category", "value", "timestamp"], (uid, uname, common.get_today_date_str(), cat, val, common.get_now_iso()))

@app.post("/webhook/switchbot")
async def callback_switchbot(request: Request):
    data = await request.json()
    ctx = data.get("context", {})
    mac = ctx.get("deviceMac")
    if not mac: return {"status": "ignored"}
    name = sb_tool.get_device_name_by_id(mac) or f"Unknown_{mac}"
    state = str(ctx.get("detectionState", "")).lower()
    
    # DBには必ず記録する (データの粒度を保つため)
    common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (common.get_now_iso(), name, mac, "Webhook Device", state, ctx.get("brightness", "")))
    
    if state: logger.info(f"[SENSOR] 受信: {name} -> {state}")

    # 通知判定 (クールタイム導入)
    if state in ["open", "detected"]:
        current_time = time.time()
        last_time = LAST_NOTIFY_TIME.get(mac, 0)
        
        # 前回の通知から5分以上経過している場合のみ送信
        if current_time - last_time > COOLDOWN_SECONDS:
            common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"🚨【見守り】\n{name} が反応しました: {state}"}], target="discord")
            # 時刻を更新
            LAST_NOTIFY_TIME[mac] = current_time
            logger.info(f"通知送信: {name}")
        else:
            logger.info(f"通知スキップ(クールタイム中): {name}")

    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)