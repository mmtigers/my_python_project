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

# --- 変更: ログサイレンスポリシーの実装 (Silence Policy 6.1準拠) ---
class SilencePolicyFilter(logging.Filter):
    """
    特定の頻繁なエンドポイント（ポーリング、ヘルスチェック、静的ファイル）に対する
    正常なGETリクエスト(HTTP 200/304)のアクセスログを抑制するフィルター。
    重要な状態変化(POST/PUT/DELETE)やエラーはそのまま出力する。
    """
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            
            # GETリクエスト以外(POST, PUT, DELETE等)はフィルタリングせず出力
            if "GET " not in msg:
                return True
                
            # 正常系 (200 OK) または キャッシュ (304 Not Modified) 以外はエラー/警告として出力
            if " 200 " not in msg and " 304 " not in msg:
                return True

            # ログ出力を抑制するパスやキーワードのリスト
            silenced_keywords = [
                # ポーリング/定常アクセス
                "/api/quest/inventory/admin/pending",
                "/api/bounty/list",
                "/api/quest/data",
                # ヘルスチェック
                "GET /health ",
                "GET / HTTP",
                # 静的アセット配下
                "/assets/",
                "/uploads/",
                "/quest_static/",
                # 静的ファイルの拡張子
                ".png", ".jpg", ".jpeg", ".gif", ".ico",
                ".css", ".js", ".json", ".woff", ".woff2"
            ]

            # メッセージ内に抑制対象のキーワードが含まれていればログ出力をスキップ (False)
            if any(keyword in msg for keyword in silenced_keywords):
                return False

        except Exception:
            # フィルタ処理中の予期せぬエラーでアプリケーションを止めないための安全策
            pass
            
        return True # 上記のどれにも引っかからなければ出力 (True)

# Global State
scheduler_process: Optional[subprocess.Popen] = None
camera_process = None

async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """アプリケーションのライフサイクル管理"""
    
    # UvicornのアクセスロガーにSilence Policyを適用
    logging.getLogger("uvicorn.access").addFilter(SilencePolicyFilter())
    
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