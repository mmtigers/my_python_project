# MY_HOME_SYSTEM/unified_server.py
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, AsyncGenerator, Any
import uvicorn
import time
import datetime
import os
import asyncio
import traceback
import logging

# --- LINE Bot SDK v3 Imports ---
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    PostbackEvent
)

# --- Project Modules ---
import config
import sound_manager
from core.logger import setup_logging
from core.utils import get_now_iso
from core.database import save_log_async
from services.notification_service import send_push
from services import switchbot_service as sb_tool
from handlers import line_logic
from services import backup_service as backup_database
from routers import quest_router, bounty_router
from models.switchbot import SwitchBotWebhookBody
from models.line import LineWebhookBody

# Logger Setup
logger: logging.Logger = setup_logging("server")

# --- Global State Management ---
LAST_NOTIFY_TIME: Dict[str, float] = {}
IS_ACTIVE: Dict[str, bool] = {}
MOTION_TASKS: Dict[str, asyncio.Task[None]] = {}

# Constants
CONTACT_COOLDOWN: int = 300
MOTION_TIMEOUT: int = 900

# --- Background Tasks ---

async def schedule_daily_backup() -> None:
    """毎日AM3:00にバックアップを実行するループ"""
    target_time = datetime.time(hour=3, minute=0, second=0)
    logger.info(f"🕰️ バックアップスケジューラ起動 (Target: {target_time})")
    
    while True:
        try:
            now = datetime.datetime.now()
            target = datetime.datetime.combine(now.date(), target_time)
            if now >= target:
                target += datetime.timedelta(days=1)
            
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            
            logger.info("📦 定期バックアップを開始します...")
            loop = asyncio.get_running_loop()
            success, res, size = await loop.run_in_executor(None, backup_database.perform_backup)
            
            if success:
                logger.info("✅ バックアップ成功通知を送信")
                send_push(
                    config.LINE_USER_ID, 
                    [{"type": "text", "text": f"📦 [システム通知]\n定期バックアップが完了しました。\nサイズ: {size:.1f}MB"}], 
                    target="discord", channel="notify"
                )
            else:
                logger.error(f"❌ バックアップ失敗通知: {res}")
                send_push(
                    config.LINE_USER_ID, 
                    [{"type": "text", "text": f"🚨 [システムエラー]\nバックアップに失敗しました。\n{res}"}], 
                    target="discord", channel="error"
                )
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Backup scheduler error: {e}")
            await asyncio.sleep(300)

async def schedule_device_refresh() -> None:
    """1時間に1回デバイスリストをSwitchBot APIから再取得してキャッシュを更新する"""
    logger.info("🔄 デバイスリスト自動更新スケジューラ起動 (Interval: 1h)")
    while True:
        try:
            await asyncio.sleep(3600)
            logger.info("🔄 SwitchBotデバイスリストの定期更新を実行中...")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, sb_tool.fetch_device_name_cache)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"デバイスリスト更新中にエラー: {e}")
            await asyncio.sleep(300)

async def send_inactive_notification(mac: str, name: str, location: str, timeout: int) -> None:
    """指定時間待機し、動きなしを通知する"""
    try:
        await asyncio.sleep(timeout)
        msg = f"💤【{location}・見守り】\n{name} の動きが止まりました（{int(timeout/60)}分経過）"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, 
            send_push, 
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            None, 
            "discord", 
            "notify"
        )
        logger.info(f"通知送信: {msg}")
        IS_ACTIVE[mac] = False
        if mac in MOTION_TASKS:
            del MOTION_TASKS[mac]
    except asyncio.CancelledError:
        logger.info(f"動きなしタイマーキャンセル: {name}")

# --- Lifecycle Manager ---

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("🚀 System Season 3 Starting...")
    logger.info(f"📂 Server is using DB at: {config.SQLITE_DB_PATH}")
    
    # 1. Update Cache (Initial)
    sb_tool.fetch_device_name_cache()
    
    # 2. Start Background Tasks
    task_backup = asyncio.create_task(schedule_daily_backup())
    task_refresh = asyncio.create_task(schedule_device_refresh())
    
    # 音声ファイルの整合性チェック
    sound_manager.check_and_restore_sounds()
    
    # 3. Seed DB
    try:
        quest_router.seed_data()
        logger.info("✅ Quest DB Seeded")
    except Exception as e:
        logger.error(f"Quest seed error: {e}")

    yield
    
    task_backup.cancel()
    task_refresh.cancel()
    logger.info("🛑 System Shutdown.")

# --- FastAPI App Definition ---
app = FastAPI(lifespan=lifespan)

# 全体エラーハンドリング
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb_str = traceback.format_exc()
    logger.error(f"❌ Unhandled Server Error at {request.url.path}\n{tb_str}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal Server Error", "detail": str(exc), "path": request.url.path}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    error_detail = exc.errors()
    logger.error(f"❌ Validation Error at {request.url.path}:\n{error_detail}")
    return JSONResponse(status_code=422, content={"detail": error_detail, "body": "Invalid data format"})

# --- LINE Bot Setup ---
line_bot_api: Optional[MessagingApi] = None
handler: Optional[WebhookHandler] = None

if config.LINE_CHANNEL_ACCESS_TOKEN and config.LINE_CHANNEL_SECRET:
    try:
        configuration = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)
        api_client = ApiClient(configuration)
        line_bot_api = MessagingApi(api_client)
        handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
        logger.info("✅ LINE Bot v3 Initialized")
    except Exception as e:
        logger.error(f"❌ LINE Bot Init Failed: {e}")
else:
    logger.warning("⚠️ LINE Config missing")

# --- Routers ---
app.include_router(quest_router.router, prefix="/api/quest", tags=["Quest"])
app.include_router(bounty_router.router, prefix="/api/bounties", tags=["Bounties"])

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Endpoints: LINE ---
@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)) -> str:
    raw_body = (await request.body()).decode('utf-8')
    try:
        json_body = await request.json()
        LineWebhookBody(**json_body)
    except Exception as e:
        logger.warning(f"不正なLINE Webhook形式: {e}")

    loop = asyncio.get_running_loop()
    try:
        if handler:
            await loop.run_in_executor(None, lambda: handler.handle(raw_body, x_line_signature))
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    return "OK"

if handler:
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle_message(event: MessageEvent) -> None:
        try:
            line_logic.handle_message(event, line_bot_api)
        except Exception as e:
            logger.error(f"LINE Message Error: {e}")
            traceback.print_exc()

    @handler.add(PostbackEvent)
    def handle_postback(event: PostbackEvent) -> None:
        try:
            logger.info(f"📩 Postback Received: {event.postback.data}")
            line_logic.handle_postback(event, line_bot_api)
        except Exception as e:
            logger.error(f"LINE Postback Error: {e}")
            traceback.print_exc()

# --- Endpoints: SwitchBot ---
@app.post("/webhook/switchbot")
async def callback_switchbot(body: SwitchBotWebhookBody) -> Dict[str, str]:
    ctx = body.context
    mac = ctx.deviceMac
    device_conf = next((d for d in config.MONITOR_DEVICES if d["id"] == mac), None)
    
    api_name = sb_tool.get_device_name_by_id(mac)
    config_name = device_conf.get("name") if device_conf else None
    name = api_name or config_name or f"Unknown_{mac}"
    location = device_conf.get("location", "場所不明") if device_conf else "未登録"
    dev_type = device_conf.get("type", "Unknown") if device_conf else "Unknown"
    state = str(ctx.detectionState).lower()
    
    try:
        await save_log_async(config.SQLITE_TABLE_SENSOR, 
            ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
            (get_now_iso(), name, mac, "Webhook Device", state, ctx.brightness or ""))
    except Exception as e:
        logger.error(f"Failed to save log: {e}")
    
    if state:
        logger.info(f"[SENSOR] 受信: {name} ({location}) -> {state}")
    
    await _process_sensor_logic(mac, name, location, dev_type, state)
    return {"status": "success"}

async def _process_sensor_logic(mac: str, name: str, location: str, dev_type: str, state: str) -> None:
    msg_text: Optional[str] = None
    current_time = time.time()
    
    if "Motion" in dev_type:
        if state == "detected":
            if mac in MOTION_TASKS:
                MOTION_TASKS[mac].cancel()
                del MOTION_TASKS[mac]
            if not IS_ACTIVE.get(mac, False):
                msg_text = f"👀【{location}・見守り】\n{name} で動きがありました"
                IS_ACTIVE[mac] = True 
        elif state == "not_detected":
            if IS_ACTIVE.get(mac, False):
                if mac in MOTION_TASKS:
                    MOTION_TASKS[mac].cancel()
                task = asyncio.create_task(send_inactive_notification(mac, name, location, MOTION_TIMEOUT))
                MOTION_TASKS[mac] = task
    elif state in ["open", "timeoutnotclose"]:
        last_time = LAST_NOTIFY_TIME.get(mac, 0.0)
        if current_time - last_time > CONTACT_COOLDOWN:
            if state == "open":
                msg_text = f"🚪【{location}・防犯】\n{name} が開きました"
            else:
                msg_text = f"⚠️【{location}・注意】\n{name} が開けっ放しです"
            LAST_NOTIFY_TIME[mac] = current_time

    if msg_text:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            send_push,
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg_text}], 
            None,
            "discord", 
            "notify"
        )
        logger.info(f"通知送信: {msg_text}")

# --- Static Files ---
if hasattr(config, "ASSETS_DIR") and os.path.exists(config.ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")

if hasattr(config, "UPLOAD_DIR"):
    app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")

if os.path.exists(config.QUEST_DIST_DIR):
    app.mount("/quest", StaticFiles(directory=config.QUEST_DIST_DIR, html=True), name="quest")
else:
    logger.warning(f"⚠️ Family Quest dist not found at {config.QUEST_DIST_DIR}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)