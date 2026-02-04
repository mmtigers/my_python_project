# MY_HOME_SYSTEM/unified_server.py
import os
import sys
import asyncio
import datetime
import subprocess
import signal
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

# プロジェクトルートの解決
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import config
from core.logger import setup_logging
from services import sensor_service

# Routers
# ▼▼▼ 修正: bounty_router も有効化 ▼▼▼
from routers import quest_router, webhook_router, system_router, bounty_router

# Handlers (初期化のため)
from handlers import line_handler

# Logger
logger = setup_logging("unified_server")

# Global State
scheduler_process: Optional[subprocess.Popen] = None

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """アプリケーションのライフサイクル管理"""
    # --- Startup ---
    logger.info("🚀 --- API Server Starting Up ---")
    
    # Schedulerの起動管理
    global scheduler_process
    try:
        scheduler_script = os.path.join(PROJECT_ROOT, "scheduler_boot.py")
        if os.path.exists(scheduler_script):
            scheduler_process = subprocess.Popen([sys.executable, scheduler_script])
            logger.info(f"✅ Scheduler started (PID: {scheduler_process.pid})")
        else:
            logger.warning("⚠️ scheduler_boot.py not found. Skipping scheduler start.")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

    yield

    # --- Shutdown ---
    logger.info("🛑 --- API Server Shutting Down ---")
    
    if scheduler_process:
        logger.info("Stopping scheduler...")
        scheduler_process.terminate()
        try:
            scheduler_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            scheduler_process.kill()
        logger.info("Scheduler stopped.")

    # Sensor Serviceのクリーンアップ
    sensor_service.cancel_all_tasks()
    logger.info("Bye!")

app = FastAPI(
    title="MY HOME SYSTEM API",
    version="2.0.0",
    description="Home Automation & Family Quest API",
    lifespan=lifespan
)

# CORS (ダッシュボード等からのアクセス許可)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Exception Handlers ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"🔥 Global Exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc)}
    )

# --- Router Registration ---
app.include_router(webhook_router.router)
app.include_router(quest_router.router, prefix="/api/quest", tags=["quest"])
app.include_router(system_router.router, prefix="/api/system", tags=["system"])
# ▼▼▼ 修正: Bounty Router (懸賞金) を登録 ▼▼▼
app.include_router(bounty_router.router, prefix="/api/bounty", tags=["bounty"])

# --- Static Files & SPA Serving ---
# 1. Assets (画像など)
assets_dir = os.path.join(PROJECT_ROOT, "assets")
if not os.path.exists(assets_dir):
    os.makedirs(assets_dir)
app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

# 2. Uploads
uploads_dir = os.path.join(PROJECT_ROOT, "uploads")
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# 3. Quest App (Frontend/SPA)
if hasattr(config, "QUEST_DIST_DIR") and os.path.exists(config.QUEST_DIST_DIR):
    app.mount("/quest_static", StaticFiles(directory=config.QUEST_DIST_DIR), name="quest_static")

    @app.get("/quest/{full_path:path}")
    async def serve_quest_spa(full_path: str):
        """React/VueなどのSPA用ルーティング (index.htmlへのフォールバック)"""
        file_path = os.path.join(config.QUEST_DIST_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        
        index_path = os.path.join(config.QUEST_DIST_DIR, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return JSONResponse(status_code=404, content={"error": "Quest App Not Found"})

    @app.get("/quest")
    async def serve_quest_root():
        """/quest アクセス時に index.html を返す"""
        index_path = os.path.join(config.QUEST_DIST_DIR, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return JSONResponse(status_code=404, content={"error": "Quest App Not Found"})

# --- Root Endpoints ---
@app.get("/")
async def root():
    return {
        "status": "ok", 
        "system": "MY_HOME_SYSTEM v2", 
        "time": datetime.datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}