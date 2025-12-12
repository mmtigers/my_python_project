# HOME_SYSTEM/unified_server.py
from fastapi import FastAPI, Request, Header, HTTPException
from contextlib import asynccontextmanager
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage
import uvicorn
import time
import config
import common
import switchbot_get_device_list as sb_tool
from handlers import line_logic

logger = common.setup_logging("server")

# 状態管理キャッシュ
LAST_NOTIFY_TIME = {} # 開閉センサーなどの連打防止用 (mac: time)
LAST_DEVICE_STATE = {} # 人感センサーの状態変化判定用 (mac: state)
COOLDOWN_SECONDS = 300

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 System Season 3 Starting...")
    sb_tool.fetch_device_name_cache()
    yield
    logger.info("🛑 System Shutdown.")

app = FastAPI(lifespan=lifespan)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)

@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)):
    body = (await request.body()).decode('utf-8')
    try: handler.handle(body, x_line_signature)
    except InvalidSignatureError:
        logger.warning("Invalid Signature detected.")
        raise HTTPException(status_code=400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try: line_logic.process_message(event, line_bot_api)
    except Exception as e: logger.error(f"メッセージ処理中にエラー発生: {e}")

@app.post("/webhook/switchbot")
async def callback_switchbot(request: Request):
    """SwitchBot Webhook エンドポイント"""
    data = await request.json()
    ctx = data.get("context", {})
    mac = ctx.get("deviceMac")
    if not mac: return {"status": "ignored"}
    
    # 1. デバイス情報の特定 (configから検索)
    device_conf = next((d for d in config.MONITOR_DEVICES if d["id"] == mac), None)
    
    # 名前と場所の解決
    if device_conf:
        name = device_conf.get("name") or sb_tool.get_device_name_by_id(mac) or f"Unknown_{mac}"
        location = device_conf.get("location", "場所不明")
        dev_type = device_conf.get("type", "Unknown")
    else:
        name = sb_tool.get_device_name_by_id(mac) or f"Unknown_{mac}"
        location = "未登録"
        dev_type = "Unknown"

    state = str(ctx.get("detectionState", "")).lower()
    
    # 2. DB記録 (全イベント保存)
    common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (common.get_now_iso(), name, mac, "Webhook Device", state, ctx.get("brightness", "")))
    
    if state:
        logger.info(f"[SENSOR] 受信: {name} ({location}) -> {state}")

    # 3. 通知ロジック
    msg_text = None
    
    # A. 人感センサー (Motion Sensor)
    # 要件: 動きなし(not_detected)⇔あり(detected) の変化時のみ通知
    if "Motion" in dev_type:
        last_state = LAST_DEVICE_STATE.get(mac)
        
        # 状態が変わった場合のみ通知 (初回は通知しない、またはdetectedなら通知するなど調整可。ここは変化重視)
        if state != last_state:
            # 状態更新
            LAST_DEVICE_STATE[mac] = state
            
            # 通知メッセージ作成
            if state == "detected":
                msg_text = f"👀【{location}・見守り】\n{name} で動きがありました"
            elif state == "not_detected":
                msg_text = f"💤【{location}・見守り】\n{name} の動きが止まりました"

    # B. 開閉センサー (Contact Sensor)
    # 要件: 開いた(open)時、または閉め忘れ(timeOutNotClose)時に通知 (連打防止あり)
    elif state in ["open", "timeoutnotclose"]:
        current_time = time.time()
        last_time = LAST_NOTIFY_TIME.get(mac, 0)
        
        if current_time - last_time > COOLDOWN_SECONDS:
            if state == "open":
                msg_text = f"🚪【{location}・防犯】\n{name} が開きました"
            else:
                msg_text = f"⚠️【{location}・注意】\n{name} が開けっ放しです"
            
            LAST_NOTIFY_TIME[mac] = current_time

    # 通知送信 (Discordの通知チャンネルへ)
    if msg_text:
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_text}], target="discord", channel="notify")
        logger.info(f"通知送信: {msg_text}")

    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)