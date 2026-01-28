# MY_HOME_SYSTEM/unified_server.py
import os
import sys
import asyncio
import time
import datetime
import subprocess
import traceback
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, AsyncGenerator, List, Union

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

# プロジェクトルートの解決
PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

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

# === Logger Setup ===
logger = setup_logging("unified_server")

# === Global State (復元) ===
# センサー監視用の状態変数を維持
LAST_NOTIFY_TIME: Dict[str, float] = {}
IS_ACTIVE: Dict[str, bool] = {}
MOTION_TASKS: Dict[str, asyncio.Task] = {}
scheduler_process: Optional[subprocess.Popen] = None

# 定数
MOTION_TIMEOUT: int = 900       # 15分 (見守りタイマー)
CONTACT_COOLDOWN: int = 300     # 5分 (通知抑制)

# === Background Logic (復元 & 型定義) ===

async def send_inactive_notification(mac: str, name: str, location: str, timeout: int) -> None:
    """無反応検知通知 (復元: 動きがない場合に通知を送る)"""
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

async def _process_sensor_logic(mac: str, name: str, location: str, dev_type: str, state: str) -> None:
    """センサー検知ロジック (復元: Webhook受信時のメインロジック)"""
    msg: Optional[str] = None
    now = time.time()
    
    # Motion Sensor Logic
    if "Motion" in dev_type:
        if state == "detected":
            # 既存のタイマーがあればキャンセル（動きがあったため）
            if mac in MOTION_TASKS: 
                MOTION_TASKS[mac].cancel()
            
            # 非アクティブ状態からの復帰時に通知
            if not IS_ACTIVE.get(mac, False):
                msg = f"👀【{location}・見守り】\n{name} で動きがありました"
                IS_ACTIVE[mac] = True
            
            # 新たな「動きなし」監視タイマーをセット
            MOTION_TASKS[mac] = asyncio.create_task(
                send_inactive_notification(mac, name, location, MOTION_TIMEOUT)
            )
    
    # Contact Sensor Logic
    elif state in ["open", "timeoutnotclose"]:
        if now - LAST_NOTIFY_TIME.get(mac, 0.0) > CONTACT_COOLDOWN:
            msg = f"🚪【{location}・防犯】\n{name} が開きました" if state == "open" else f"⚠️【{location}・注意】\n{name} が開けっ放しです"
            LAST_NOTIFY_TIME[mac] = now
            
    if msg:
        # 非同期で通知送信
        await asyncio.to_thread(
            send_push, 
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            None, "discord", "notify"
        )

# === Scheduled Tasks ===

async def schedule_daily_backup() -> None:
    """毎日AM3:00にバックアップを実行"""
    target_time = datetime.time(hour=3, minute=0, second=0)
    logger.info(f"🕰️ Backup scheduler started (Target: {target_time})")
    
    while True:
        try:
            now = datetime.datetime.now()
            target = datetime.datetime.combine(now.date(), target_time)
            if now >= target:
                target += datetime.timedelta(days=1)
            
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            
            logger.info("📦 Starting periodic backup...")
            loop = asyncio.get_running_loop()
            
            success, res, size = await loop.run_in_executor(None, backup_database.perform_backup)
            
            if success:
                logger.info(f"✅ Backup successful: {size:.1f}MB")
            else:
                logger.error(f"❌ Backup failed: {res}")
                await asyncio.to_thread(
                    send_push,
                    config.LINE_USER_ID, 
                    [{"type": "text", "text": f"🚨 バックアップ失敗: {res}"}], 
                    None, "discord", "error"
                )
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Backup scheduler error: {e}")
            await asyncio.sleep(300)

async def schedule_device_refresh() -> None:
    """デバイスリスト定期更新 (Webhookの名前解決用)"""
    logger.info("🔄 Device list refresh scheduler started")
    while True:
        try:
            await asyncio.sleep(3600)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, sb_tool.fetch_device_name_cache)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Device refresh error: {e}")
            await asyncio.sleep(300)

# === Lifespan Manager (Startup/Shutdown) ===

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global scheduler_process
    logger.info("🚀 MY_HOME_SYSTEM & Family Quest Core Starting...")
    
    # 1. Init: Cache & Sounds & DB
    try:
        sb_tool.fetch_device_name_cache()
        sound_manager.check_and_restore_sounds()
        quest_router.seed_data()
    except Exception as e:
        logger.error(f"Startup init warning: {e}")

    # 2. Start Background Tasks
    task_backup = asyncio.create_task(schedule_daily_backup())
    task_refresh = asyncio.create_task(schedule_device_refresh())

    # 3. Start External Scheduler Process
    try:
        scheduler_path = os.path.join(PROJECT_ROOT, "scheduler.py")
        if os.path.exists(scheduler_path):
            scheduler_process = subprocess.Popen([sys.executable, scheduler_path])
            logger.info(f"📅 Scheduler subprocess started (PID: {scheduler_process.pid})")
    except Exception as e:
        logger.critical(f"Failed to start scheduler process: {e}")

    yield

    # --- Shutdown Sequence ---
    logger.info("🛑 Shutting down system...")
    if scheduler_process:
        scheduler_process.terminate()
    
    task_backup.cancel()
    task_refresh.cancel()
    
    # 実行中の見守りタスクをキャンセル
    for t in MOTION_TASKS.values():
        t.cancel()
        
    logger.info("👋 System Shutdown complete.")

# === FastAPI App ===
app = FastAPI(lifespan=lifespan, title="MY_HOME_SYSTEM Unified Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Exception Handlers (Fail-Safe) ===
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"❌ Unhandled Error at {request.url.path}: {exc}\n{traceback.format_exc()}")
    return JSONResponse(status_code=500, content={"status": "error", "message": "Internal Server Error"})

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.error(f"❌ Validation Error: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

# === LINE Bot Setup ===
line_handler: Optional[WebhookHandler] = None
line_bot_api: Optional[MessagingApi] = None

if config.LINE_CHANNEL_ACCESS_TOKEN and config.LINE_CHANNEL_SECRET:
    try:
        line_conf = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)
        line_bot_api = MessagingApi(ApiClient(line_conf))
        line_handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
        logger.info("✅ LINE Bot API v3 initialized")
    except Exception as e:
        logger.error(f"LINE initialization failed: {e}")

# === Routers (Existing APIs) ===
app.include_router(quest_router.router, prefix="/api/quest", tags=["Quest"])
app.include_router(bounty_router.router, prefix="/api/bounties", tags=["Bounties"])

# === Webhooks & System APIs ===

@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)) -> str:
    """LINE Bot Webhook"""
    if not line_handler:
        raise HTTPException(status_code=501, detail="LINE Bot not configured")
    
    body = (await request.body()).decode('utf-8')
    try:
        line_handler.handle(body, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    except Exception as e:
        logger.error(f"LINE callback error: {e}")
        # LINEサーバーにはOKを返し、再送ループを防ぐ
    return "OK"

@app.post("/webhook/switchbot")
async def switchbot_webhook(body: SwitchBotWebhookBody) -> Dict[str, str]:
    """SwitchBot Webhook受信・処理 (復元機能)"""
    ctx = body.context
    mac = ctx.deviceMac
    
    # デバイス情報の解決
    api_name = sb_tool.get_device_name_by_id(mac)
    # config.MONITOR_DEVICES は devices.json から読み込まれた最新データを使用
    device_conf = next((d for d in config.MONITOR_DEVICES if d.get("id") == mac), None)
    
    name = api_name or (device_conf.get("name") if device_conf else f"Unknown_{mac}")
    location = device_conf.get("location", "未登録") if device_conf else "場所不明"
    state = str(ctx.detectionState).lower()

    # 1. ログ保存 (旧テーブルへ - 互換性維持)
    await save_log_async("device_records", 
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (get_now_iso(), name, mac, "Webhook", state, ctx.brightness or "")
    )
    
    # 2. 新テーブル(daily_logs)への保存 (設計書準拠: イベントとして記録)
    #    重要な検知イベントのみを記録し、ログの肥大化を防ぐ
    if state in ["detected", "open", "timeoutnotclose"]:
        detail_msg = f"{name}: {state}"
        await save_log_async(config.SQLITE_TABLE_DAILY_LOGS,
            ["category", "detail", "timestamp"],
            ("Sensor", detail_msg, get_now_iso())
        )

    # 3. ロジック実行 (通知・見守りタイマー等)
    await _process_sensor_logic(mac, name, location, ctx.deviceType, state)
    
    return {"status": "success"}

@app.post("/api/system/backup")
async def manual_backup() -> Dict[str, Any]:
    """手動バックアップトリガー (復元)"""
    success, msg, size = backup_database.perform_backup()
    if not success: 
        raise HTTPException(status_code=500, detail=msg)
    return {"status": "success", "message": msg, "size_mb": size}

# === Event Handlers (LINE) ===
if line_handler:
    @line_handler.add(MessageEvent, message=TextMessageContent)
    def handle_message(event: MessageEvent) -> None:
        try:
            line_logic.handle_message(event, line_bot_api)
        except Exception as e:
            logger.error(f"LINE message handling error: {e}")

    @line_handler.add(PostbackEvent)
    def handle_postback(event: PostbackEvent) -> None:
        try:
            line_logic.handle_postback(event, line_bot_api)
        except Exception as e:
            logger.error(f"LINE postback handling error: {e}")

# === Static Files & SPA (設計書準拠) ===

# 1. 共通Assets
if os.path.exists(config.ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")
if os.path.exists(config.UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")

# 2. Family Quest (SPA) 配信
# 設計書ではルートパス配信が推奨されていますが、既存ブックマーク等の互換性のため
# "/quest_static" マウントと、ルートパスへのSPAフォールバックを両立させます。
if hasattr(config, "QUEST_DIST_DIR") and os.path.exists(config.QUEST_DIST_DIR):
    # 静的リソース用マウント
    app.mount("/quest_static", StaticFiles(directory=config.QUEST_DIST_DIR), name="quest_static")

    @app.get("/{full_path:path}")
    async def serve_family_quest(full_path: str) -> Union[FileResponse, Any]:
        # APIやWebhookなど、FastAPIが処理すべきパスは除外
        reserved_paths = ["api", "assets", "uploads", "callback", "webhook", "quest_static"]
        if any(full_path.startswith(p) for p in reserved_paths):
             raise HTTPException(status_code=404)
        
        # 上記以外はすべて index.html を返し、フロントエンド(React)側でルーティングさせる
        index_path = os.path.join(config.QUEST_DIST_DIR, "index.html")
        return FileResponse(index_path)

if __name__ == "__main__":
    import uvicorn
    # 設計書準拠: LAN内固定IPでの運用
    uvicorn.run(app, host="0.0.0.0", port=8000)