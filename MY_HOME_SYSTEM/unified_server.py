# HOME_SYSTEM/unified_server.py
from fastapi import FastAPI, Request, Header, HTTPException
from contextlib import asynccontextmanager
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, SourceGroup, SourceUser, TextSendMessage
import sqlite3
import datetime
import pytz
import uvicorn
import config
import switchbot_get_device_list as sb_tool

# === 1. 起動・終了時の処理 ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[INFO] サーバー起動処理を開始します...")
    sb_tool.fetch_device_name_cache()
    yield
    print("[INFO] サーバーを終了します...")

app = FastAPI(lifespan=lifespan)

# LINE Bot設定
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)

# ==========================================
# 2. LINE Bot Webhook ("おはよ" 記録)
# ==========================================
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
    msg = event.message.text
    if len(msg) > config.MESSAGE_LENGTH_LIMIT: return
    
    keyword = next((k for k in config.OHAYO_KEYWORDS if k in msg.lower()), None)
    if not keyword: return

    user_name = "Unknown"
    try:
        if isinstance(event.source, SourceGroup):
            user_name = line_bot_api.get_group_member_profile(event.source.group_id, event.source.user_id).display_name
        elif isinstance(event.source, SourceUser):
            user_name = line_bot_api.get_profile(event.source.user_id).display_name
    except: pass

    save_log(config.SQLITE_TABLE_OHAYO, 
             (event.source.user_id, user_name, msg, get_now(), keyword),
             "user_id, user_name, message, timestamp, recognized_keyword")
    print(f"[OHAYO] 記録: {user_name}「{msg}」")


# ==========================================
# 3. SwitchBot Webhook (見守りログ ＆ 通知)
# ==========================================
@app.post("/webhook/switchbot")
async def callback_switchbot(request: Request):
    data = await request.json()
    ctx = data.get("context", {})
    mac = ctx.get("deviceMac")
    
    if not mac: return {"status": "ignored"}
    
    # 名前解決
    name = sb_tool.get_device_name_by_id(mac) or f"Unknown_{mac}"
    
    # 状態の取得 (open/close/detected等)
    detection_state = str(ctx.get("detectionState", "")).lower()
    brightness = ctx.get("brightness", "")
    
    # DB保存
    save_log(config.SQLITE_TABLE_SENSOR,
             (get_now(), name, mac, "Webhook Device", detection_state, brightness),
             "timestamp, device_name, device_id, device_type, contact_state, brightness_state")
    
    print(f"[SENSOR] 受信: {name} -> {detection_state}")

    # ★★★ ここに追加: LINE通知機能 ★★★
    # 「ドアが開いた(open)」または「動きを検知した(detected)」場合に通知
    if detection_state == "open" or detection_state == "detected":
        send_line_alert(name, detection_state)

    return {"status": "success"}


# --- 共通関数 ---
def send_line_alert(device_name, state):
    """LINE通知を送る関数"""
    try:
        # メッセージの作成
        message = f"🚨【見守り通知】\n{device_name} が反応しました。\n状態: {state}"
        
        # config.LINE_USER_ID 宛に送信
        line_bot_api.push_message(config.LINE_USER_ID, TextSendMessage(text=message))
        print(f"[LINE] 通知送信完了: {device_name}")
    except Exception as e:
        print(f"[ERROR] LINE通知失敗: {e}")
        # 通知エラーでもサーバーは落とさない

def get_now():
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def save_log(table, values, columns):
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH)
        placeholders = ", ".join(["?"] * len(values))
        conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()
    except Exception as e: 
        print(f"[ERROR] DB Save: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)