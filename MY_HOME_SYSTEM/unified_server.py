# MY_HOME_SYSTEM/unified_server.py
import os
import sys
import asyncio
import datetime
import subprocess
import signal
import logging
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
from routers import quest_router, webhook_router, system_router, bounty_router

# Handlers
from handlers import line_handler

# Logger
logger = setup_logging("unified_server")

# --- 追加: ログサイレンスポリシーの実装 ---
class PollingEndpointFilter(logging.Filter):
    """
    特定のポーリングエンドポイントに対する正常なGETリクエスト(200 OK)のログ出力を抑制するフィルター。
    基本設計書 運用・保守設計「DEBUG: 正常なポーリングは運用時に出力しない」に準拠。
    """
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            # 正常なGETリクエスト（HTTP 200）のみを対象とする
            if "GET" in msg and " 200 " in msg:
                # 抑制対象のエンドポイントリスト（部分一致）
                if ("/api/quest/inventory/admin/pending" in msg or
                    "/api/bounties/list" in msg or
                    "/api/quest/data" in msg):
                    return False # ログ出力をスキップ
        except Exception:
            pass
        return True # 上記以外（エラーやPOSTなど）は通常通り出力

# Global State
scheduler_process: Optional[subprocess.Popen] = None
camera_process = None

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """アプリケーションのライフサイクル管理"""
    logging.getLogger("uvicorn.access").addFilter(PollingEndpointFilter())
    logger.info("🚀 --- API Server Starting Up ---")

    global camera_process
    camera_script = os.path.join(PROJECT_ROOT, "monitors/camera_monitor.py")
    camera_process = subprocess.Popen([sys.executable, camera_script])
    
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

    logger.info("🛑 --- API Server Shutting Down ---")
    
    if scheduler_process:
        logger.info("Stopping scheduler...")
        scheduler_process.terminate()
        try:
            scheduler_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            scheduler_process.kill()
        logger.info("Scheduler stopped.")

    sensor_service.cancel_all_tasks()
    logger.info("Bye!")

app = FastAPI(
    title="MY HOME SYSTEM API",
    version="2.0.0",
    description="Home Automation & Family Quest API",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(bounty_router.router, prefix="/api/bounty", tags=["bounty"])

# --- Static Files & SPA Serving ---

# 1. Assets
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
# 安全に設定を取得し、ログを出力してデバッグしやすくする
quest_dist_dir = getattr(config, "QUEST_DIST_DIR", None)

if quest_dist_dir and os.path.exists(quest_dist_dir):
    logger.info(f"📂 Quest App Configured: {quest_dist_dir}")
    
    # 静的ファイル (JS/CSSなど) の配信
    app.mount("/quest_static", StaticFiles(directory=quest_dist_dir), name="quest_static")

    # SPA用ルーティング (ファイルが存在すればそれを、なければindex.htmlを返す)
    @app.get("/quest/{full_path:path}")
    async def serve_quest_spa(full_path: str):
        target_file = os.path.join(quest_dist_dir, full_path)
        
        # ファイル実体があればそれを返す (画像やJSなど)
        if os.path.isfile(target_file):
            return FileResponse(target_file)
        
        # なければSPAとして index.html を返す
        index_path = os.path.join(quest_dist_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return JSONResponse(status_code=404, content={"error": "index.html not found"})

    # ルートパス (/quest と /quest/) の両方をハンドリング
    @app.get("/quest")
    @app.get("/quest/")
    async def serve_quest_root():
        index_path = os.path.join(quest_dist_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return JSONResponse(status_code=404, content={"error": "index.html not found"})

else:
    # 設定がない、またはディレクトリが存在しない場合の警告
    logger.warning(f"⚠️ Quest App Directory NOT FOUND or NOT SET. Config value: {quest_dist_dir}")

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

if __name__ == "__main__":
    import uvicorn
    # 0.0.0.0 で起動することで外部（192.168.1.xxx）からのアクセスを許可します
    uvicorn.run(app, host="0.0.0.0", port=8000)