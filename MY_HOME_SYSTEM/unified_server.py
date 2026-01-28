# MY_HOME_SYSTEM/unified_server.py
import os
import sys
import asyncio
import time
import datetime
import subprocess
import traceback
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, AsyncGenerator

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

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
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import sound_manager
from core.logger import setup_logging
from core.utils import get_now_iso
from core.database import save_log_async
from services.notification_service import send_push
from services import switchbot_service as sb_tool
from services import backup_service as backup_database
from handlers import line_logic
from routers import quest_router, bounty_router
from models.switchbot import SwitchBotWebhookBody
from models.line import LineWebhookBody

# === Logger Setup ===
logger = setup_logging("server")

# === Global State ===
LAST_NOTIFY_TIME: Dict[str, float] = {}
IS_ACTIVE: Dict[str, bool] = {}
MOTION_TASKS: Dict[str, asyncio.Task] = {}
scheduler_process: Optional[subprocess.Popen] = None

# === Constants ===
MOTION_TIMEOUT: int = 900       # 15分
CONTACT_COOLDOWN: int = 300     # 5分

# === Background Tasks (元の機能を維持) ===

async def schedule_daily_backup() -> None:
    """毎日AM3:00にバックアップを実行する"""
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
            
            # backup_service.py の戻り値 (success, msg, size) に対応
            success, res, size = await loop.run_in_executor(None, backup_database.perform_backup)
            
            if success:
                logger.info("✅ バックアップ成功通知を送信")
                await asyncio.to_thread(
                    send_push,
                    config.LINE_USER_ID, 
                    [{"type": "text", "text": f"📦 [システム通知]\n定期バックアップが完了しました。\nサイズ: {size:.1f}MB"}], 
                    None, "discord", "notify"
                )
            else:
                logger.error(f"❌ バックアップ失敗: {res}")
                await asyncio.to_thread(
                    send_push,
                    config.LINE_USER_ID, 
                    [{"type": "text", "text": f"🚨 [システムエラー]\nバックアップに失敗しました。\n{res}"}], 
                    None, "discord", "error"
                )
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Backup scheduler error: {e}")
            await asyncio.sleep(300)

async def schedule_device_refresh() -> None:
    """1時間に1回デバイスリストを更新 (Webhookの名前解決用)"""
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
    """無反応検知通知"""
    try:
        await asyncio.sleep(timeout)
        msg = f"💤【{location}・見守り】\n{name} の動きが止まりました（{int(timeout/60)}分経過）"
        
        await asyncio.to_thread(
            send_push,
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            None, "discord", "notify"
        )
        logger.info(f"通知送信: {msg}")
        IS_ACTIVE[mac] = False
        if mac in MOTION_TASKS:
            del MOTION_TASKS[mac]
            
    except asyncio.CancelledError:
        logger.debug(f"動きなしタイマーキャンセル: {name}")

# === Lifespan Manager ===

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global scheduler_process
    logger.info("🚀 System Season 3 Starting...")
    
    # 1. Initial Cache & Sounds
    try:
        sb_tool.fetch_device_name_cache()
        sound_manager.check_and_restore_sounds()
        # sound_manager.init_mixer() # 必要に応じてコメントアウト解除
    except Exception as e:
        logger.error(f"Init warning: {e}")

    # 2. Seed DB
    try:
        quest_router.seed_data()
        logger.info("✅ Quest DB Seeded")
    except Exception as e:
        logger.error(f"Quest seed error: {e}")

    # 3. Start Background Tasks (Backup & Cache Refresh)
    task_backup = asyncio.create_task(schedule_daily_backup())
    task_refresh = asyncio.create_task(schedule_device_refresh())

    # 4. Start Scheduler
    try:
        scheduler_script = os.path.join(os.path.dirname(__file__), "scheduler.py")
        if os.path.exists(scheduler_script):
            scheduler_process = subprocess.Popen([sys.executable, scheduler_script])
            logger.info(f"📅 Scheduler started (PID: {scheduler_process.pid})")
    except Exception as e:
        logger.critical(f"Failed to start scheduler: {e}")

    yield

    # --- Shutdown ---
    if scheduler_process:
        scheduler_process.terminate()
    
    task_backup.cancel()
    task_refresh.cancel()
    for t in MOTION_TASKS.values():
        t.cancel()
    logger.info("🛑 System Shutdown.")

# === FastAPI App Definition ===
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=getattr(config, "CORS_ORIGINS", ["*"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Global Error Handlers ===
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"❌ Unhandled Error at {request.url.path}: {exc}\n{traceback.format_exc()}")
    return JSONResponse(status_code=500, content={"status": "error", "message": "Internal Server Error", "detail": str(exc)})

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.error(f"❌ Validation Error: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

# === LINE Bot Setup ===
line_bot_api: Optional[MessagingApi] = None
line_handler: Optional[WebhookHandler] = None

if config.LINE_CHANNEL_ACCESS_TOKEN and config.LINE_CHANNEL_SECRET:
    try:
        conf = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)
        line_bot_api = MessagingApi(ApiClient(conf))
        line_handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
        logger.info("✅ LINE Bot v3 Initialized")
    except Exception as e:
        logger.error(f"LINE Init Failed: {e}")

# === Routes (元のパスを維持) ===
app.include_router(quest_router.router, prefix="/api/quest", tags=["Quest"])
app.include_router(bounty_router.router, prefix="/api/bounties", tags=["Bounties"])

@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)) -> str:
    body = (await request.body()).decode('utf-8')
    try:
        line_handler.handle(body, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    except Exception:
        pass # エラーでもLINE側にはOKを返して再送を防ぐ
    return "OK"

if line_handler:
    @line_handler.add(MessageEvent, message=TextMessageContent)
    def handle_message(event: MessageEvent) -> None:
        try:
            line_logic.handle_message(event, line_bot_api)
        except Exception as e:
            logger.error(f"LINE Message Error: {e}")

    @line_handler.add(PostbackEvent)
    def handle_postback(event: PostbackEvent) -> None:
        try:
            line_logic.handle_postback(event, line_bot_api)
        except Exception as e:
            logger.error(f"LINE Postback Error: {e}")

@app.post("/webhook/switchbot")
async def switchbot_webhook(body: SwitchBotWebhookBody) -> Dict[str, str]:
    ctx = body.context
    mac = ctx.deviceMac
    
    api_name = sb_tool.get_device_name_by_id(mac)
    device_conf = next((d for d in getattr(config, "MONITOR_DEVICES", []) if d["id"] == mac), None)
    
    name = api_name or (device_conf.get("name") if device_conf else f"Unknown_{mac}")
    location = device_conf.get("location", "未登録") if device_conf else "場所不明"
    state = str(ctx.detectionState).lower()

    await save_log_async(config.SQLITE_TABLE_SENSOR, 
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (get_now_iso(), name, mac, "Webhook", state, ctx.brightness or "")
    )
    
    await _process_sensor_logic(mac, name, location, ctx.deviceType, state)
    return {"status": "success"}

async def _process_sensor_logic(mac: str, name: str, location: str, dev_type: str, state: str) -> None:
    msg: Optional[str] = None
    now = time.time()
    
    if "Motion" in dev_type:
        if state == "detected":
            if mac in MOTION_TASKS: MOTION_TASKS[mac].cancel()
            if not IS_ACTIVE.get(mac, False):
                msg = f"👀【{location}・見守り】\n{name} で動きがありました"
                IS_ACTIVE[mac] = True
            MOTION_TASKS[mac] = asyncio.create_task(send_inactive_notification(mac, name, location, MOTION_TIMEOUT))
    
    elif state in ["open", "timeoutnotclose"]:
        if now - LAST_NOTIFY_TIME.get(mac, 0.0) > CONTACT_COOLDOWN:
            msg = f"🚪【{location}・防犯】\n{name} が開きました" if state == "open" else f"⚠️【{location}・注意】\n{name} が開けっ放しです"
            LAST_NOTIFY_TIME[mac] = now
            
    if msg:
        await asyncio.to_thread(send_push, config.LINE_USER_ID, [{"type": "text", "text": msg}], None, "discord", "notify")

@app.post("/api/system/backup")
async def manual_backup() -> Dict[str, Any]:
    # backup_service.py の新しい戻り値 (3つ) に対応
    success, msg, size = backup_database.perform_backup()
    if not success: raise HTTPException(500, msg)
    return {"status": "success", "message": msg, "size_mb": size}

# === Static Files (元のマウントパスを完全維持) ===
if hasattr(config, "ASSETS_DIR") and os.path.exists(config.ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")

if hasattr(config, "UPLOAD_DIR") and os.path.exists(config.UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")

if hasattr(config, "QUEST_DIST_DIR") and os.path.exists(config.QUEST_DIST_DIR):
    app.mount("/quest", StaticFiles(directory=config.QUEST_DIST_DIR, html=True), name="quest")

# SPA Fallback
frontend_dist = os.getenv("FRONTEND_DIST_DIR", os.path.join(os.path.dirname(__file__), "dist"))
if os.path.exists(frontend_dist):
    app.mount("/spa_assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="spa_assets")
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if any(full_path.startswith(p) for p in ["api", "assets", "uploads", "quest", "spa_assets", "callback", "webhook"]):
             raise HTTPException(status_code=404)
        return FileResponse(os.path.join(frontend_dist, "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)