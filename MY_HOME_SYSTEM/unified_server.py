# HOME_SYSTEM/unified_server.py
from fastapi import FastAPI, Request, Header, HTTPException
from contextlib import asynccontextmanager
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, SourceGroup, SourceUser
import uvicorn
import config
import switchbot_get_device_list as sb_tool
import common # ★共通ライブラリを使用

# === 状態管理 & 定義 ===
USER_INPUT_STATE = {}
MENU_OPTIONS = config.MENU_OPTIONS

# === ライフサイクル ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[INFO] サーバー起動: デバイスリスト取得中...")
    sb_tool.fetch_device_name_cache()
    yield
    print("[INFO] サーバー終了")

app = FastAPI(lifespan=lifespan)

# SDK (署名検証 & プロフィール取得用)
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)

# === Webhook エンドポイント ===
@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)):
    body = (await request.body()).decode('utf-8')
    try: 
        handler.handle(body, x_line_signature)
    except InvalidSignatureError: 
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token
    
    # 1. スキップ処理
    if msg == "食事_スキップ":
        if user_id in USER_INPUT_STATE: del USER_INPUT_STATE[user_id]
        common.send_line_reply(reply_token, [{"type": "text", "text": "👌 記録をスキップしました。"}])
        return

    # 2. 手入力モード判定
    if user_id in USER_INPUT_STATE:
        if msg.startswith("食事"): # ボタン操作が割り込んだ場合
            del USER_INPUT_STATE[user_id]
        else:
            category = USER_INPUT_STATE[user_id]
            if len(msg) > 50:
                common.send_line_reply(reply_token, [{"type": "text", "text": "⚠️ 50文字以内で入力してください。"}])
                return
            
            user_name = get_user_name_from_event(event)
            final_record = f"{category}: {msg} (手入力)"
            
            if save_food_log(user_id, user_name, final_record):
                del USER_INPUT_STATE[user_id]
                reply_text = f"✅ {common.get_display_date()}の夕食\n「{final_record}」\nを記録しました！"
                common.send_line_reply(reply_token, [{"type": "text", "text": reply_text}])
                print(f"[FOOD] 手入力記録: {user_name} -> {final_record}")
            else:
                common.send_line_reply(reply_token, [{"type": "text", "text": "❌ エラー: 記録失敗"}])
            return

    # 3. カテゴリ選択ボタン ("食事カテゴリ_自炊")
    if msg.startswith("食事カテゴリ_"):
        selected_cat = msg.replace("食事カテゴリ_", "")
        menus = MENU_OPTIONS.get(selected_cat, MENU_OPTIONS["その他"])
        
        items = []
        for menu in menus:
            items.append({
                "type": "action",
                "action": {
                    "type": "message", "label": menu[:20], 
                    "text": f"食事記録_{selected_cat}_{menu}"
                }
            })
        # 手入力ボタン
        items.append({
            "type": "action",
            "action": {
                "type": "message", "label": "✏️ その他(手入力)", 
                "text": f"食事手入力_{selected_cat}"
            }
        })

        reply_obj = {
            "type": "text",
            "text": f"【{selected_cat}】ですね。メニューを選んでください。",
            "quickReply": { "items": items }
        }
        common.send_line_reply(reply_token, [reply_obj])
        return

    # 4. 手入力リクエスト ("食事手入力_自炊")
    if msg.startswith("食事手入力_"):
        category = msg.replace("食事手入力_", "")
        USER_INPUT_STATE[user_id] = category
        common.send_line_reply(reply_token, [{"type": "text", "text": f"📝 【{category}】のメニュー名を入力してください。"}])
        return

    # 5. 記録確定 ("食事記録_自炊_カレー")
    if msg.startswith("食事記録_"):
        try:
            parts = msg.split("_", 2) 
            if len(parts) >= 3:
                final_record = f"{parts[1]}: {parts[2]}"
                user_name = get_user_name_from_event(event)
                if save_food_log(user_id, user_name, final_record):
                    common.send_line_reply(reply_token, [{"type": "text", "text": f"✅ 記録しました: {final_record}"}])
                    print(f"[FOOD] ボタン記録: {user_name} -> {final_record}")
        except Exception as e:
            print(f"[ERROR] 解析失敗: {e}")
        return

    # 6. おはよう記録
    if len(msg) > config.MESSAGE_LENGTH_LIMIT: return
    keyword = next((k for k in config.OHAYO_KEYWORDS if k in msg.lower()), None)
    if keyword:
        user_name = get_user_name_from_event(event)
        cols = ["user_id", "user_name", "message", "timestamp", "recognized_keyword"]
        vals = (event.source.user_id, user_name, msg, common.get_now_iso(), keyword)
        common.save_log_generic(config.SQLITE_TABLE_OHAYO, cols, vals)
        print(f"[OHAYO] 記録: {user_name} -> {msg}")

# --- ヘルパー関数 ---
def get_user_name_from_event(event):
    try:
        if isinstance(event.source, SourceGroup):
            return line_bot_api.get_group_member_profile(event.source.group_id, event.source.user_id).display_name
        elif isinstance(event.source, SourceUser):
            return line_bot_api.get_profile(event.source.user_id).display_name
    except: pass
    return "Unknown"

def save_food_log(user_id, user_name, record_content):
    cols = ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"]
    vals = (user_id, user_name, common.get_today_date_str(), "Dinner", record_content, common.get_now_iso())
    return common.save_log_generic(config.SQLITE_TABLE_FOOD, cols, vals)

# --- SwitchBot Webhook ---
@app.post("/webhook/switchbot")
async def callback_switchbot(request: Request):
    data = await request.json()
    ctx = data.get("context", {})
    mac = ctx.get("deviceMac")
    if not mac: return {"status": "ignored"}
    
    name = sb_tool.get_device_name_by_id(mac) or f"Unknown_{mac}"
    detection_state = str(ctx.get("detectionState", "")).lower()
    brightness = ctx.get("brightness", "")
    
    # 記録
    cols = ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"]
    vals = (common.get_now_iso(), name, mac, "Webhook Device", detection_state, brightness)
    common.save_log_generic(config.SQLITE_TABLE_SENSOR, cols, vals)
    
    if detection_state: print(f"[SENSOR] 受信: {name} -> {detection_state}")
    
    # 通知
    if detection_state in ["open", "detected"]:
        msg = {"type": "text", "text": f"🚨【見守り通知】\n{name} が反応しました。\n状態: {detection_state}"}
        common.send_line_push(config.LINE_USER_ID, [msg])
        
    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)