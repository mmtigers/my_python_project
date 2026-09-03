# MY_HOME_SYSTEM/unified_server.py
import os
import sys
import datetime
import subprocess
import logging
import ipaddress

from typing import AsyncGenerator, Optional, Callable, Awaitable

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

# プロジェクトルートの解決
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import sqlite3

import config
from core.logger import setup_logging
from core.migrations import apply_pending_migrations
from services import sensor_service

# Routers
from routers import quest_router, webhook_router, system_router, camera_router, alexa_router

# Handlers

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
            # #177: uvicornのアクセスログフォーマット('%s - "%s %s HTTP/%s" %d'、
            # h11_impl.py/httptools_impl.py)ではステータスコードがメッセージ末尾に
            # 前方スペースのみで出力され、後方にはスペースが付かない
            # (例: '127.0.0.1 - "GET /path HTTP/1.1" 200')。" 200 "/" 304 " という
            # 前後スペース付きの部分文字列判定では決して一致せず、抑制が常に無効化
            # されていた。末尾の空白を除去したうえで、末尾一致(endswith)で判定する。
            stripped_msg = msg.rstrip()
            if not stripped_msg.endswith(" 200") and not stripped_msg.endswith(" 304"):
                return True

            # ログ出力を抑制するパスやキーワードのリスト
            silenced_keywords = [
                # ポーリング/定常アクセス
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

    # NAS依存パス(ASSETS_DIR等)のプリウォーム。Issue #330 PR-Bでconfigのimport時
    # NAS検証は遅延化されたため、サーバー起動時はここで明示的に解決しておく
    # (遅延化前と同じく、起動時点でNAS障害のフォールバック判定が済む)。
    try:
        config.prewarm_nas_paths()
    except Exception as e:
        logger.error(f"⚠️ NAS path prewarm failed (continuing startup): {e}")

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

    非プライベートIPからのアクセスはブロックせずログ記録のみ行い通過させる（Issue #321・
    2026-09-03決定）。アプリ層では`Cf-Access-Jwt-Assertion`の署名/aud検証を意図的に
    実装せず、外部アクセス制御はインフラ側のCloudflare Access（Zero Trust）に委譲する設計を
    正式なものとしている。この設計は、オリジンへの直接到達がCloudflareのIPレンジ経由に
    限定されていること（ルーター/FW側の設定）を前提とする。詳細は
    `docs/reports/CODE_REVIEW_REPORT_ALL.md`のCritical#2/#8を参照。
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
        ip_obj = ipaddress.ip_address(client_ip)
        
        if ip_obj.is_loopback or ip_obj.is_private:
            return await call_next(request)
            
    except ValueError:
        pass

    # Cloudflare Access (Zero Trust) を導入しているため、IPベースの遮断は行わず、
    # 認証はCloudflareのエッジネットワークに委譲する。
    # Issue #321(2026-09-03決定・案B): `Cf-Access-Jwt-Assertion`ヘッダーの検証は
    # PR #80で一度実装されたが2026-08-28の障害でrevertされ、その後の判断として
    # 「再実装しない」ことを正式設計として確定した(defense in depthより、過去の
    # 障害実績の再導入を避けることを優先)。オリジンへの直接到達をCloudflareの
    # IPレンジ経由に限定するインフラ側設定が、この設計の前提条件となる。
    
    # #182: setup_logging()(core/logger.py)はロガーレベルをINFO固定にしており、
    # DEBUGレベルのオーバーライド手段が存在しないため、logger.debug()での出力は
    # 常に抑制され「外部アクセスの記録」が事実上機能していなかった。本ミドルウェアの
    # docstring・CLAUDE.mdはいずれも「非プライベートネットワークからのリクエストを
    # ログに記録する」ことを意図した挙動として明記しており、ポーリング等の定常ノイズを
    # 意図的にDEBUGへ降格するSilence Policy(#177のuvicornアクセスログ抑制とは別経路)
    # とは性質が異なるため、実際に記録されるようINFOレベルに変更する。
    logger.info(f"Allowed external access via Cloudflare - IP: {client_ip}, Path: {request.url.path}")
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
app.include_router(alexa_router.router, tags=["alexa"])

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

def _run_uvicorn_server() -> None:
    """本番起動経路のエントリポイント(`python unified_server.py`)。

    #229: 以前はここで"uvicorn.access"ロガー自体のレベルをWARNINGに固定していた。
    uvicornのアクセスログは常にlogger.info()(レベル20)で出力されるため、
    ロガーのレベルチェックの時点でログレコードが作られず、lifespan()内で
    登録しているSilencePolicyFilter(GETの200/304ポーリングのみを選別して抑制し、
    POST・エラーは残す設計)が一度も呼び出されなかった。結果、POST等の状態変更
    リクエストやエラーレスポンスを含め、アクセスログが本番起動経路で一切残らない
    状態になっていた。デフォルトのlog_config(uvicorn.access=INFO)をそのまま使い、
    レコード生成自体は妨げず、SilencePolicyFilterに選別を委ねる。
    """
    import uvicorn

    # 0.0.0.0 で起動することで外部（192.168.1.xxx等）からのアクセスを許可します
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    _run_uvicorn_server()