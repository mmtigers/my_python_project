# MY_HOME_SYSTEM/unified_server.py
import os
import sys
import asyncio
import datetime
import subprocess
import signal
import logging
from contextlib import asynccontextmanager
import ipaddress

from typing import AsyncGenerator, Optional, Callable, Awaitable

from fastapi import FastAPI, Request, HTTPException, Response
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

@app.middleware("http")
async def ip_restriction_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """
    リクエスト元のIPアドレスを検証し、許可されたネットワークからのアクセスのみを後続へ渡すミドルウェア。
    Cloudflare等のリバースプロキシ環境に対応し、CF-Connecting-IP または X-Forwarded-For ヘッダーから
    実クライアントIPを取得して判定する。

    例外として、外部からのWebhook受信が必要な以下のパスは全IPからアクセスを許可する:
    - /webhook/switchbot
    - /callback/line

    許可ネットワーク:
    - プライベートIP (192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12)
    - ローカルホスト (127.0.0.1, ::1)
    """
    allowed_webhook_paths = {
        "/webhook/switchbot",
        "/callback/line"
    }

    # 1. 例外パスの判定（Webhook関連は無条件で許可）
    if request.url.path in allowed_webhook_paths:
        return await call_next(request)

    # 2. クライアントIPの取得 (リバースプロキシ対応)
    # Cloudflareの独自ヘッダーを最優先、次に一般的な X-Forwarded-For を確認
    client_ip: str | None = request.headers.get("cf-connecting-ip")
    
    if not client_ip:
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            # X-Forwarded-Forはカンマ区切りで複数IPが入る場合があるため、先頭（元のクライアント）を取得
            client_ip = x_forwarded_for.split(",")[0].strip()
        else:
            # ヘッダーがない場合は直接の接続元IPを取得
            client_ip = request.client.host if request.client else "0.0.0.0"

    try:
        # 文字列として取得したIPを判定用のオブジェクトに変換
        ip_obj = ipaddress.ip_address(client_ip)
        
        # 3. IPアドレスがローカルホスト(is_loopback)またはプライベートIP(is_private)か検証
        if ip_obj.is_loopback or ip_obj.is_private:
            return await call_next(request)
            
    except ValueError:
        # 不正な形式のIPアドレス文字列が渡された場合のフェイルセーフ（意図せぬクラッシュ防止）
        pass

    # 4. 許可条件を満たさない場合はログを記録し、403 Forbidden を返す (printは不使用)
    logger.warning(f"Blocked unauthorized external access - IP: {client_ip}, Path: {request.url.path}")
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden: Access denied."}
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