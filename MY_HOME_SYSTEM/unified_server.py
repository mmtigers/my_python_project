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

import sqlite3

import jwt

import config
from core.cf_access import CloudflareAccessVerifier
from core.logger import setup_logging
from core.migrations import apply_pending_migrations
from services import sensor_service

# Routers
from routers import quest_router, webhook_router, system_router, camera_router

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

    if not config.SWITCHBOT_WEBHOOK_TOKEN:
        logger.warning("⚠️ SWITCHBOT_WEBHOOK_TOKEN is not set — SwitchBot webhook signature verification is DISABLED. Set the env var to enable it.")

    # スキーママイグレーションの適用 (migrations/ 配下、詳細は core/migrations.py 参照)
    try:
        migration_conn = sqlite3.connect(config.SQLITE_DB_PATH)
        try:
            apply_pending_migrations(migration_conn)
        finally:
            migration_conn.close()
    except Exception as e:
        logger.error(f"⚠️ Migration check failed (continuing startup): {e}")

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

    if camera_process:
        logger.info("Stopping camera monitor...")
        camera_process.terminate()
        try:
            camera_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            camera_process.kill()
        logger.info("Camera monitor stopped.")

    sensor_service.cancel_all_tasks()
    logger.info("Bye!")

app = FastAPI(
    title="MY HOME SYSTEM API",
    version="2.0.0",
    description="Home Automation & Family Quest API",
    lifespan=lifespan
)

# M-8-2: 許可オリジンのリストは config.CORS_ORIGINS に一本化した
# (以前はここに別のハードコードされたリストがあり、config.py側の設定や
# ALLOW_ALL_ORIGINS環境変数を変更してもCORS設定に反映されなかった)。
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

cf_access_verifier = CloudflareAccessVerifier(
    config.CF_ACCESS_TEAM_DOMAIN, config.CF_ACCESS_AUD
)


@app.middleware("http")
async def access_control_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """
    アクセス制御ミドルウェア。

    信頼判定には、クライアントが自由に詐称できるヘッダー(CF-Connecting-IP /
    X-Forwarded-For)ではなく、実際のTCP接続元 (request.client.host) を使う。

    1. Webhook例外パス (/webhook/switchbot, /callback/line) は通過させる。
       それぞれのハンドラが署名/トークン検証を行う(Cloudflare Access側でも
       これらのパスはBypassポリシーで素通しになっている前提)。
    2. 接続元がプライベート/ループバックで、かつCloudflare Tunnel経由の痕跡
       (cf-connecting-ipヘッダー)がなければ、LAN内・ローカルプロセスとして通過。
       ※Tunnel経由のリクエストは同居するcloudflaredからループバックで届くため、
         「ループバック=内部」とは判定できない。cloudflaredは必ず
         cf-connecting-ip を付与するので、その有無で区別する。
    3. それ以外(Tunnel経由の外部アクセス等)は Cf-Access-Jwt-Assertion の
       JWT検証を必須とする(fail-closed)。Cloudflare Accessのエッジ認証を通過した
       リクエストにはこのヘッダーが必ず付与される。検証失敗・欠如は403。
    """
    allowed_webhook_paths = {
        "/webhook/switchbot",
        "/callback/line"
    }

    if request.url.path in allowed_webhook_paths:
        return await call_next(request)

    peer_host = request.client.host if request.client else ""
    try:
        peer_ip = ipaddress.ip_address(peer_host)
        peer_is_internal = peer_ip.is_loopback or peer_ip.is_private
    except ValueError:
        # 接続元が解釈できない場合は内部扱いしない(fail-closed)
        peer_is_internal = False

    via_cloudflare_tunnel = "cf-connecting-ip" in request.headers

    if peer_is_internal and not via_cloudflare_tunnel:
        return await call_next(request)

    token = request.headers.get("cf-access-jwt-assertion")
    if not token or not cf_access_verifier.configured:
        logger.warning(
            f"🚫 Rejected external request without Cloudflare Access JWT - "
            f"peer: {peer_host}, cf-connecting-ip: {request.headers.get('cf-connecting-ip')}, "
            f"path: {request.url.path}"
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "Cloudflare Access authentication required"},
        )

    try:
        # JWKS取得(初回/キャッシュ切れ時)がブロッキングI/Oのためスレッドへ逃がす
        claims = await asyncio.to_thread(cf_access_verifier.verify, token)
    except jwt.PyJWTError as e:
        logger.warning(
            f"🚫 Rejected request with invalid Cloudflare Access JWT - "
            f"path: {request.url.path}, reason: {e}"
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "Invalid Cloudflare Access token"},
        )
    except Exception as e:
        # JWKS取得失敗などの検証基盤エラー。攻撃とは区別し503で返す(fail-closed)
        logger.error(f"🔥 Cloudflare Access JWT verification unavailable: {e}")
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable"},
        )

    # 後続処理やログで利用できるよう、エッジで認証されたユーザーを記録
    request.state.cf_access_email = claims.get("email")
    return await call_next(request)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"🔥 Global Exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"}
    )

# --- Router Registration ---
app.include_router(webhook_router.router)
app.include_router(quest_router.router, prefix="/api/quest", tags=["quest"])
app.include_router(system_router.router, prefix="/api/system", tags=["system"])
app.include_router(camera_router.router, prefix="/api/cameras", tags=["cameras"])

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
    # ★変更: /camera 配下のパスもSPAのルーティングに含める
    quest_dist_dir_real = os.path.realpath(quest_dist_dir)

    @app.get("/quest/{full_path:path}")
    @app.get("/camera/{full_path:path}")
    async def serve_quest_spa(full_path: str):
        target_file = os.path.realpath(os.path.join(quest_dist_dir, full_path))

        # ディレクトリトラバーサル対策: 解決後のパスが quest_dist_dir 配下であることを検証
        if os.path.commonpath([quest_dist_dir_real, target_file]) != quest_dist_dir_real:
            return JSONResponse(status_code=404, content={"error": "Not found"})

        # ファイル実体があればそれを返す (画像やJSなど)
        if os.path.isfile(target_file):
            return FileResponse(target_file)
        
        # なければSPAとして index.html を返す
        index_path = os.path.join(quest_dist_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return JSONResponse(status_code=404, content={"error": "index.html not found"})

    # ルートパス (/quest, /quest/, /camera, /camera/) をハンドリング
    # ★変更: /camera のルートアクセスを許可
    @app.get("/quest")
    @app.get("/quest/")
    @app.get("/camera")
    @app.get("/camera/")
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
    import logging
    
    # 【改修】Uvicornのデフォルトログ設定を取得し、アクセスログのレベルを WARNING に変更
    # これにより、正常なAPIポーリングやWebhook受信時の INFO ログスパムを抑止する
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["loggers"]["uvicorn.access"]["level"] = "WARNING"

    # 0.0.0.0 で起動することで外部（192.168.1.xxx等）からのアクセスを許可します
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=log_config)