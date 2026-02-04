# MY_HOME_SYSTEM/unified_server.py
import os
import sys
import asyncio
import datetime
import subprocess
import traceback
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

# プロジェクトルートの解決
PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import config
import sound_manager
from core.logger import setup_logging
from services import switchbot_service as sb_tool
from services import backup_service
from services import sensor_service
# 新しいRouterとHandlerをインポート
from routers import quest_router, bounty_router, webhook_router, system_router
from handlers import line_handler  # 初期化のためインポート

# === Logger Setup ===
logger = setup_logging("unified_server")

# === Global State ===
scheduler_process: Optional[subprocess.Popen] = None

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
            
            # Service層へ委譲
            success, res, size = await loop.run_in_executor(None, backup_service.perform_backup)
            
            if success:
                logger.info(f"✅ Backup successful: {size:.1f}MB")
            else:
                logger.error(f"❌ Backup failed: {res}")
            
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
    
    # センサー監視タスクのクリーンアップ (Serviceへ委譲)
    sensor_service.cancel_all_tasks()
        
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

# === Exception Handlers ===
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"❌ Unhandled Error at {request.url.path}: {exc}\n{traceback.format_exc()}")
    return JSONResponse(status_code=500, content={"status": "error", "message": "Internal Server Error"})

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.error(f"❌ Validation Error: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

# === Routers ===
# 既存のAPI
app.include_router(quest_router.router, prefix="/api/quest", tags=["Quest"])
app.include_router(bounty_router.router, prefix="/api/bounties", tags=["Bounties"])

# 新規追加・分離したAPI
app.include_router(webhook_router.router, tags=["Webhooks"]) # /callback/line, /webhook/switchbot
app.include_router(system_router.router, prefix="/api/system", tags=["System"]) # /backup

# === Static Files & SPA ===

# 1. 共通Assets
if os.path.exists(config.ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")
if os.path.exists(config.UPLOAD_DIR):
    app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")

# 2. Family Quest (SPA)
if hasattr(config, "QUEST_DIST_DIR") and os.path.exists(config.QUEST_DIST_DIR):
    @app.get("/quest/{file_path:path}")
    async def serve_quest_app(file_path: str):
        target_path = os.path.normpath(os.path.join(config.QUEST_DIST_DIR, file_path))
        if not target_path.startswith(str(os.path.abspath(config.QUEST_DIST_DIR))):
             raise HTTPException(status_code=403, detail="Access denied")
        if os.path.isfile(target_path):
            return FileResponse(target_path)
        index_path = os.path.join(config.QUEST_DIST_DIR, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return JSONResponse(status_code=404, content={"error": "SPA index.html not found"})

    @app.get("/quest")
    async def serve_quest_root():
        index_path = os.path.join(config.QUEST_DIST_DIR, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return JSONResponse(status_code=404, content={"error": "SPA not found"})

# 3. Fallback (SPA Routing Support)
@app.get("/{full_path:path}")
async def serve_root_fallback(full_path: str):
    reserved_paths = ["api", "assets", "uploads", "callback", "webhook", "quest"]
    if any(full_path.startswith(p) for p in reserved_paths):
         raise HTTPException(status_code=404)
    if hasattr(config, "QUEST_DIST_DIR") and os.path.exists(config.QUEST_DIST_DIR):
        return FileResponse(os.path.join(config.QUEST_DIST_DIR, "index.html"))
    raise HTTPException(status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)