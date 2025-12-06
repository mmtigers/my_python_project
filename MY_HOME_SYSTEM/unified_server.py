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

def ask_outing_question(token, food_rec):
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": f"外出_{l}"}} for l in ["はい", "いいえ"]]
    common.send_reply(token, [{"type": "text", "text": f"「{food_rec}」を記録したよ📝\n\nあと、今日はお出かけした？", "quickReply": {"items": items}}])

def get_user_name(event):
    try:
        if isinstance(event.source, SourceGroup): return line_bot_api.get_group_member_profile(event.source.group_id, event.source.user_id).display_name
        elif isinstance(event.source, SourceUser): return line_bot_api.get_profile(event.source.user_id).display_name
    except: pass
    return "Unknown"

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
    
    common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (common.get_now_iso(), name, mac, "Webhook Device", state, ctx.get("brightness", "")))
    
    if state: logger.info(f"[SENSOR] 受信: {name} -> {state}")
    if state in ["open", "detected"]:
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"🚨【見守り】\n{name} が反応したよ！\n状態: {state}"}])
    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)