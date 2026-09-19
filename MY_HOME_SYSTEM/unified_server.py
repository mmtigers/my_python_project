# MY_HOME_SYSTEM/unified_server.py
import os
import sys
import asyncio
import datetime
import subprocess
import logging
import ipaddress
import re
import time

from typing import AsyncGenerator, Dict, List, Optional, Callable, Awaitable, Set

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
from services import sensor_service, camera_service
from services.dashboard_proxy_service import dashboard_proxy_service

# Routers
from routers import quest_router, webhook_router, system_router, camera_router, alexa_router, routine_router
# ダッシュボード(Streamlit)の中継。config.DASHBOARD_PROXY_ENABLED=false のときは
# include しないため、import だけしてルートは生やさない。
from routers import dashboard_router

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

# アクセスログ中のクエリパラメータ秘密情報(SwitchBot Webhook の ?token=... 等)をマスクする
# 正規表現。値は '&' / 空白 / 引用符 まで。
_QUERY_SECRET_RE = re.compile(r"(?i)((?:^|[?&])(?:token|secret|password|api_key|access_token)=)[^&\s\"']*")


def redact_query_secrets(text: str) -> str:
    """URL/パス文字列中の秘密系クエリパラメータの値を *** に置換する。"""
    return _QUERY_SECRET_RE.sub(r"\1***", text)


class SecretRedactionFilter(logging.Filter):
    """uvicorn のアクセスログ('%s - "%s %s HTTP/%s" %d')に含まれるクエリ文字列から
    共有シークレットをマスクするフィルター。

    SwitchBot Webhook は署名検証機構が無く、config.SWITCHBOT_WEBHOOK_TOKEN を
    ?token=... のクエリパラメータで受け取る設計(Issue #318)のため、uvicorn の
    アクセスログ(get_path_with_query_string でクエリ込みのパスを出力する)にそのまま
    残っていた。SilencePolicyFilter は POST を抑制しないため、SwitchBot のイベントごとに
    "POST /webhook/switchbot?token=<secret> 200" が home_system.log / journal に書かれ、
    log_analyzer や scripts/claude_investigate.sh がそれを Issue 本文へ貼り付ける経路も
    あった。ログレコードの args (パス位置)を書き換えることで、後段のどのハンドラ/
    フォーマッタでもマスク済みの値だけが出力されるようにする。
    """
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if isinstance(args, tuple):
                record.args = tuple(
                    redact_query_secrets(a) if isinstance(a, str) else a for a in args
                )
            elif isinstance(args, dict):
                record.args = {
                    k: (redact_query_secrets(v) if isinstance(v, str) else v) for k, v in args.items()
                }
            if isinstance(record.msg, str) and not args:
                record.msg = redact_query_secrets(record.msg)
        except Exception:
            # マスク処理の失敗でログ出力自体を止めない
            pass
        return True


# Global State
scheduler_process: Optional[subprocess.Popen] = None
camera_process: Optional[subprocess.Popen] = None

# --- 監視子プロセス(camera_monitor / scheduler_boot)の死活監視と自動再起動 ---
# Issue #646: 以前は lifespan 起動時に Popen するだけで、その後の死活を一切見ていなかった。
# scheduler_boot.py が落ちると配下の6監視タスクが静かに止まり、気づくのは毎時 cron の
# health_watch.py の通知後、復旧は人手だった。子プロセス名 -> 起動スクリプト(PROJECT_ROOT 相対)。
CHILD_SCRIPTS: Dict[str, str] = {
    "camera_monitor": "monitors/camera_monitor.py",
    "scheduler": "scheduler_boot.py",
}
# 死活確認の間隔(秒)
CHILD_MONITOR_INTERVAL_SEC: float = 30.0
# 1時間あたりの自動再起動の上限。これを超えたら(クラッシュループとみなして)その子プロセスの
# 自動再起動を止め、CRITICAL(Discord通知)を出して人の確認に委ねる。
CHILD_RESTART_MAX_PER_HOUR: int = 5
_CHILD_RESTART_WINDOW_SEC: float = 3600.0

# 子プロセス名ごとの再起動時刻(time.monotonic())の履歴と、上限到達で再起動を止めた子プロセス名
_child_restart_history: Dict[str, List[float]] = {}
_child_restart_disabled: Set[str] = set()


def _get_child_process(name: str) -> Optional[subprocess.Popen]:
    return camera_process if name == "camera_monitor" else scheduler_process


def _set_child_process(name: str, proc: Optional[subprocess.Popen]) -> None:
    global camera_process, scheduler_process
    if name == "camera_monitor":
        camera_process = proc
    else:
        scheduler_process = proc


def _spawn_child_process(name: str) -> Optional[subprocess.Popen]:
    """監視子プロセスを起動する。起動失敗は logger.error にとどめ None を返す(#360 と同じ保護)。"""
    script_path = os.path.join(PROJECT_ROOT, CHILD_SCRIPTS[name])
    if not os.path.exists(script_path):
        logger.warning(f"⚠️ {CHILD_SCRIPTS[name]} not found. Skipping {name} start.")
        return None
    try:
        proc = subprocess.Popen([sys.executable, script_path])
        logger.info(f"✅ {name} started (PID: {proc.pid})")
        return proc
    except Exception as e:
        logger.error(f"Failed to start {name}: {e}")
        return None


def restart_dead_children(now: Optional[float] = None) -> List[str]:
    """終了している監視子プロセスを再起動し、再起動した子プロセス名の一覧を返す。

    テスト容易性のため同期関数にしている(定期実行は `_supervise_child_processes`)。
    直近1時間の再起動回数が `CHILD_RESTART_MAX_PER_HOUR` に達した子プロセスは
    クラッシュループとみなして自動再起動を止め、CRITICAL を1回だけ出す。
    """
    now = time.monotonic() if now is None else now
    restarted: List[str] = []
    for name in CHILD_SCRIPTS:
        proc = _get_child_process(name)
        if proc is None or name in _child_restart_disabled:
            continue
        returncode = proc.poll()
        if returncode is None:
            continue  # 生存中

        history = [t for t in _child_restart_history.get(name, []) if now - t < _CHILD_RESTART_WINDOW_SEC]
        if len(history) >= CHILD_RESTART_MAX_PER_HOUR:
            _child_restart_disabled.add(name)
            _child_restart_history[name] = history
            logger.critical(
                f"🚨 {name} が直近1時間に{CHILD_RESTART_MAX_PER_HOUR}回以上終了したため自動再起動を停止します"
                f"(最終 exit code {returncode})。logs/ を確認し、原因を直してからサーバーを再起動してください。"
            )
            continue

        logger.error(f"⚠️ {name} が予期せず終了しました(exit code {returncode})。再起動します。")
        new_proc = _spawn_child_process(name)
        if new_proc is None:
            # 起動自体に失敗した場合も再起動試行として数え、無限に Popen を繰り返さない
            history.append(now)
            _child_restart_history[name] = history
            continue
        history.append(now)
        _child_restart_history[name] = history
        _set_child_process(name, new_proc)
        restarted.append(name)
    return restarted


async def _supervise_child_processes(interval_sec: float = CHILD_MONITOR_INTERVAL_SEC) -> None:
    """lifespan 内で動かす死活監視ループ。シャットダウン時は cancel される。"""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            restart_dead_children()
        except Exception as e:
            # 監視ループ自身は止めない
            logger.error(f"Child process supervision failed: {e}")


async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """アプリケーションのライフサイクル管理"""
    
    # UvicornのアクセスロガーにSilence Policyと秘密情報マスクを適用
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(SecretRedactionFilter())
    access_logger.addFilter(SilencePolicyFilter())
    
    logger.info("🚀 --- API Server Starting Up ---")

    if not config.SWITCHBOT_WEBHOOK_TOKEN:
        # Issue #648: 未設定時は /webhook/switchbot を 503 で拒否する(フェイルクローズ)。
        # 移行用オプトインが立っている場合だけ従来どおり無検証で受け付ける。
        if getattr(config, "ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK", False):
            logger.warning("⚠️ SWITCHBOT_WEBHOOK_TOKEN is not set and ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK=true — /webhook/switchbot accepts UNAUTHENTICATED requests. Set the token and remove the opt-in.")
        else:
            logger.warning("⚠️ SWITCHBOT_WEBHOOK_TOKEN is not set — /webhook/switchbot will reject all requests with 503. Set the env var (and add ?token=... to the SwitchBot webhook URL) to enable it.")

    # NAS依存パス(ASSETS_DIR等)のプリウォーム。Issue #330 PR-Bでconfigのimport時
    # NAS検証は遅延化されたため、サーバー起動時はここで明示的に解決しておく
    # (遅延化前と同じく、起動時点でNAS障害のフォールバック判定が済む)。
    try:
        config.prewarm_nas_paths()
    except Exception as e:
        logger.error(f"⚠️ NAS path prewarm failed (continuing startup): {e}")

    # スキーママイグレーションの適用 (migrations/ 配下、詳細は core/migrations.py 参照)
    # #383: 以前は接続に timeout 未指定(既定5秒)で、前世代の監視プロセスが書き込み中だと
    # "database is locked" で失敗しやすく、しかも失敗を logger.error で握りつぶして
    # 子プロセスを起動しサービスを継続していた(スキーマ未適用のまま全APIが500)。
    # timeout を延ばし、失敗時は CRITICAL(Discord通知)を出して監視子プロセスを起動しない。
    migration_ok = True
    try:
        migration_conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=30.0)
        try:
            apply_pending_migrations(migration_conn)
        finally:
            migration_conn.close()
    except Exception as e:
        migration_ok = False
        logger.critical(
            f"🚨 Migration failed at startup: {e}. "
            "Monitor subprocesses will NOT be started; fix the DB/schema and restart."
        )
    app.state.migration_ok = migration_ok

    supervisor_task: Optional[asyncio.Task] = None
    if migration_ok:
        # #360: camera_monitor の起動も scheduler と同様に保護する(以前は失敗すると
        # lifespan の例外でサーバー全体が起動しなかった)。起動失敗は _spawn_child_process が
        # logger.error にとどめ、一方の失敗がもう一方や lifespan 自体に影響しない。
        for child_name in CHILD_SCRIPTS:
            _set_child_process(child_name, _spawn_child_process(child_name))

        # Issue #646: 起動後の死活監視。終了していれば再起動する(上限超過でクラッシュループ扱い)。
        supervisor_task = asyncio.create_task(_supervise_child_processes())

    yield

    logger.info("🛑 --- API Server Shutting Down ---")

    # 子プロセスを止める前に監視ループを止める(止めた子を再起動しないように)
    if supervisor_task is not None:
        supervisor_task.cancel()
        try:
            await supervisor_task
        except (asyncio.CancelledError, Exception):
            pass

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

    # #360: ライブ配信/VOD生成の ffmpeg も止める(孤児化による HLS 二重書き込み防止)
    try:
        camera_service.stop_all_processes()
    except Exception as e:
        logger.error(f"Failed to stop ffmpeg processes: {e}")

    sensor_service.cancel_all_tasks()

    # ダッシュボード中継用の httpx コネクションプールを閉じる
    # (未クローズのまま落とすと "Unclosed client session" の警告が出る)。
    try:
        await dashboard_proxy_service.aclose()
    except Exception as e:
        logger.warning(f"ダッシュボード中継クライアントのクローズに失敗しました: {e}")

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
# #411 S-L7: allow_origins=["*"](ALLOW_ALL_ORIGINS=true時)と allow_credentials=True の
# 組合せは、Starletteの CORSMiddleware がワイルドカード指定でも認証情報(Cookie等)を
# 伴うリクエストに対してリクエスト元Originをそのままエコーバックしてしまい、実質的に
# 任意オリジンへ資格情報付きアクセスを許可する状態になる。ワイルカード指定時は
# allow_credentials を False にする。
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=config.CORS_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """
    Issue #665: 全レスポンスに最小限のセキュリティヘッダーを付与するミドルウェア。

    付与するヘッダー:
    - `X-Content-Type-Options: nosniff` — Content-Type を無視したMIMEスニッフィングを禁止する。
      `/quest`・`/camera` 配下は family-quest のビルド成果物を、`/uploads` は
      ユーザーがアップロードしたアバター画像をそのまま配信するため、拡張子と実体が
      食い違うファイルをブラウザが実行可能なリソースとして解釈しないようにする。
    - `X-Frame-Options` — 既定 `SAMEORIGIN`(`config.SECURITY_HEADER_X_FRAME_OPTIONS`)。
      `DENY` を既定にすると、Echo Show 等からの同一オリジンiframe埋め込みまで
      壊れうるため既定では同一オリジンを許す。空文字にすると付与しない。
    - `Referrer-Policy: strict-origin-when-cross-origin` — 外部への遷移時にパスを送らない。

    既に値が設定されているヘッダーは上書きしない。エッジ(Cloudflare)側で
    同じヘッダーを付与している構成では `SECURITY_HEADERS_ENABLED=false` で
    オリジン側の付与そのものを止められる(値が二重になるのを避けるため)。

    CSP は family-quest が Vite ビルドのインラインスタイル等を含むため、
    ここでは意図的に付与しない(付けるならフロント側の実測とセットで行う)。
    """
    response = await call_next(request)

    if not config.SECURITY_HEADERS_ENABLED:
        return response

    headers: Dict[str, str] = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }
    if config.SECURITY_HEADER_X_FRAME_OPTIONS:
        headers["X-Frame-Options"] = config.SECURITY_HEADER_X_FRAME_OPTIONS

    for name, value in headers.items():
        if name not in response.headers:
            response.headers[name] = value

    return response

@app.middleware("http")
async def ip_restriction_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """
    リクエスト元のIPアドレスを検証し、許可されたネットワークからのアクセスのみを後続へ渡すミドルウェア。
    Cloudflare等のリバースプロキシ環境に対応し、CF-Connecting-IP または X-Forwarded-For ヘッダーから
    実クライアントIPを取得して判定する。

    例外として、外部からのWebhook受信が必要な以下のパスは全IPからアクセスを許可する:
    - /webhook/switchbot
    - /callback/line
    - /webhook/alexa

    このリストは「オリジンが外部から到達可能でなければ機能しないパス」の一覧でもある。
    本ミドルウェア自体は遮断を行わないため、リストに載せることの実際の効果は
    「クライアントIPの解決と外部アクセスのINFOログ出力をスキップする」ことだけだが、
    Cloudflare Access側でこれらのパスをバイパス対象に設定し忘れるとWebhookが
    サーバーまで届かなくなる(Issue #517で /webhook/switchbot・/callback/line が
    実際にブロックされていた)。エッジ側の設定を点検する際の参照元として、
    外部Webhookのパスを追加したらここにも必ず追記すること。

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
        "/callback/line",
        # Alexaカスタムスキル「ファミクエ」のエンドポイント(routers/alexa_router.py)。
        # 上2つと同じく Alexa クラウドから外部到達する必要があるが、長らくこのリストから
        # 漏れていた(docstringが列挙する「外部Webhook」の意図と非対称だった)。
        # 署名・タイムスタンプ検証は core/alexa_verifier.py が別途行う。
        "/webhook/alexa",
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
app.include_router(routine_router.router, prefix="/api/routine", tags=["routine"])

# ダッシュボード(Streamlit・8501番)の中継。
# 8501番は認証を持たないため localhost 束縛のままにし、外部からの到達は
# 既に Cloudflare Access で保護されている本サーバー経由に一本化する
# (詳細は services/dashboard_proxy_service.py のモジュールdocstring)。
# ★このパスを Cloudflare Access のバイパス対象に設定してはならない。
if config.DASHBOARD_PROXY_ENABLED:
    app.include_router(dashboard_router.router, tags=["dashboard"])
    logger.info(f"📊 Dashboard Proxy: {config.DASHBOARD_BASE_PATH} -> {config.DASHBOARD_INTERNAL_URL}")

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
async def health_check(request: Request) -> JSONResponse:
    """liveness ではなく readiness を返すヘルスチェック。

    Issue #735 (AUDIT-005): 以前は無条件に {"status": "healthy"} を返していた。
    lifespan のマイグレーションが失敗すると migration_ok=False になり、監視子プロセスを
    起動せず全APIがスキーマ未適用のDBに当たって500を返す状態になるが、プロセス自体は
    生き続けるため systemd(Type=simple)・server_watchdog(systemctl + pgrep)・
    health_watch(check_service_active)のいずれも「正常」と報告していた。
    起動時のCRITICAL通知1通だけが唯一のシグナルで、Webhook障害時には失われる。
    「プロセスは生きているが機能していない」を外形から見分けられるよう 503 を返す。
    """
    migration_ok = getattr(request.app.state, "migration_ok", True)
    if not migration_ok:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "reason": "migration_failed"},
        )
    return JSONResponse(status_code=200, content={"status": "healthy"})

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