# HOME_SYSTEM/unified_server.py
from fastapi import FastAPI, Request, Header, HTTPException
from contextlib import asynccontextmanager
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage
import uvicorn
import time

# Season3 Modules
import config
import common
import switchbot_get_device_list as sb_tool
from handlers import line_logic

# ロガー
logger = common.setup_logging("server")

# クールタイム管理 (デバイスID: 最終通知時刻)
LAST_NOTIFY_TIME = {}
COOLDOWN_SECONDS = 300

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 System Season 3 Starting...")
    # デバイス名のキャッシュ更新
    sb_tool.fetch_device_name_cache()
    yield
    logger.info("🛑 System Shutdown.")

app = FastAPI(lifespan=lifespan)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)

@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)):
    """LINE Bot Webhook エンドポイント"""
    body = (await request.body()).decode('utf-8')
    try:
        handler.handle(body, x_line_signature)
    except InvalidSignatureError:
        logger.warning("Invalid Signature detected.")
        raise HTTPException(status_code=400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """メッセージ受信時の処理 (ロジックはhandlersへ委譲)"""
    try:
        line_logic.process_message(event, line_bot_api)
    except Exception as e:
        logger.error(f"メッセージ処理中にエラー発生: {e}")
        # ユーザーにはエラーを見せないが、裏でDiscordに飛ぶ

@app.post("/webhook/switchbot")
async def callback_switchbot(request: Request):
    """SwitchBot Webhook エンドポイント"""
    data = await request.json()
    ctx = data.get("context", {})
    mac = ctx.get("deviceMac")
    if not mac:
        return {"status": "ignored"}
    
    # デバイス名解決
    name = sb_tool.get_device_name_by_id(mac) or f"Unknown_{mac}"
    state = str(ctx.get("detectionState", "")).lower()
    
    # ログ記録
    common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (common.get_now_iso(), name, mac, "Webhook Device", state, ctx.get("brightness", "")))
    
    if state:
        logger.info(f"[SENSOR] 受信: {name} -> {state}")

    # 通知ロジック (クールタイム付き)
    if state in ["open", "detected"]:
        current_time = time.time()
        last_time = LAST_NOTIFY_TIME.get(mac, 0)
        
        if current_time - last_time > COOLDOWN_SECONDS:
            msg_text = f"🚨【見守り】\n{name} が反応しました: {state}"
            common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_text}], target="discord", channel="notify")
            
            LAST_NOTIFY_TIME[mac] = current_time
            logger.info(f"通知送信: {name}")
        else:
            logger.info(f"通知スキップ(クールタイム): {name}")

    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)