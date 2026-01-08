# HOME_SYSTEM/unified_server.py
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from fastapi.responses import JSONResponse
import uvicorn
import time
import datetime
import os
import asyncio
import logging
import sound_manager
import traceback

# Local Modules
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, PostbackEvent

import config
import common
from services import switchbot_service as sb_tool
from handlers import line_logic
from services import backup_service as backup_database
from routers import quest_router

# Logger Setup
logger = common.setup_logging("server")

# --- Global State Management ---
# 開閉センサーの連打防止用 (mac: timestamp)
LAST_NOTIFY_TIME: Dict[str, float] = {}
# 人感センサーの活動状態 (mac: bool)
IS_ACTIVE: Dict[str, bool] = {}
# 人感センサーの「動きなし監視タイマー」 (mac: asyncio.Task)
MOTION_TASKS: Dict[str, asyncio.Task] = {}

# Constants
CONTACT_COOLDOWN = 300   # 5分 (連打防止)
MOTION_TIMEOUT = 900     # 15分 (動きなし判定までの時間)


# --- Pydantic Models ---
class SwitchBotContext(BaseModel):
    deviceMac: str
    detectionState: str
    brightness: Optional[str] = None
    timeOfSample: Optional[int] = None

class SwitchBotWebhookBody(BaseModel):
    context: SwitchBotContext
    eventType: Optional[str] = None
    deviceType: Optional[str] = None


# --- Background Task: Scheduled Backup ---
async def schedule_daily_backup():
    """毎日AM3:00にバックアップを実行するループ"""
    target_time = datetime.time(hour=3, minute=0, second=0)
    logger.info(f"🕰️ バックアップスケジューラ起動 (Target: {target_time})")
    
    while True:
        now = datetime.datetime.now()
        target = datetime.datetime.combine(now.date(), target_time)
        
        if now >= target:
            # 既に過ぎている場合は翌日の同時刻
            target += datetime.timedelta(days=1)
        
        wait_seconds = (target - now).total_seconds()
        # logger.info(f"⏳ 次回バックアップまで待機: {wait_seconds / 3600:.1f}時間")
        
        # 1時間ごとのチェックで待機する実装に変更（長時間のsleepはキャンセル時に反応が悪いため）
        # ここでは単純化のためsleepを使用しますが、実運用ではループで細かく待つのがベター
        try:
            await asyncio.sleep(wait_seconds)
        except asyncio.CancelledError:
            break
        
        # Backup Execution
        logger.info("📦 定期バックアップを開始します...")
        loop = asyncio.get_running_loop()
        success, res, size = await loop.run_in_executor(None, backup_service.perform_backup)
        
        if success:
            logger.info("✅ バックアップ成功通知を送信")
            common.send_push(
                config.LINE_USER_ID, 
                [{"type": "text", "text": f"📦 [システム通知]\n定期バックアップが完了しました。\nサイズ: {size:.1f}MB"}], 
                target="discord", channel="notify"
            )
        else:
            logger.error(f"❌ バックアップ失敗通知: {res}")
            common.send_push(
                config.LINE_USER_ID, 
                [{"type": "text", "text": f"🚨 [システムエラー]\nバックアップに失敗しました。\n{res}"}], 
                target="discord", channel="error"
            )
            
        await asyncio.sleep(60)

# ▼▼▼ 追加: 定期デバイスリスト更新タスク ▼▼▼
async def schedule_device_refresh():
    """1時間に1回デバイスリストをSwitchBot APIから再取得してキャッシュを更新する"""
    logger.info("🔄 デバイスリスト自動更新スケジューラ起動 (Interval: 1h)")
    while True:
        try:
            await asyncio.sleep(3600) # 1時間待機
            logger.info("🔄 SwitchBotデバイスリストの定期更新を実行中...")
            loop = asyncio.get_running_loop()
            # ネットワークIOを含むためexecutorで実行
            await loop.run_in_executor(None, sb_tool.fetch_device_name_cache)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"デバイスリスト更新中にエラー: {e}")
            await asyncio.sleep(300) # エラー時は5分後再試行
# ▲▲▲ 追加終了 ▲▲▲


# --- Async Notification Helper ---
async def send_inactive_notification(mac: str, name: str, location: str, timeout: int):
    """指定時間待機し、キャンセルされなければ「動きなし」を通知する"""
    try:
        await asyncio.sleep(timeout)
        
        msg = f"💤【{location}・見守り】\n{name} の動きが止まりました（{int(timeout/60)}分経過）"
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, 
            common.send_push, 
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            None, 
            "discord", 
            "notify"
        )
        
        logger.info(f"通知送信: {msg}")
        
        # State Reset
        IS_ACTIVE[mac] = False
        if mac in MOTION_TASKS:
            del MOTION_TASKS[mac]

    except asyncio.CancelledError:
        logger.info(f"動きなしタイマーキャンセル: {name} (活動継続)")


# --- Lifecycle Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server Startup & Shutdown Logic"""
    logger.info("🚀 System Season 3 Starting...")
    logger.info(f"📂 Server is using DB at: {config.SQLITE_DB_PATH}")
    
    # 1. Update Cache (Initial)
    sb_tool.fetch_device_name_cache()
    
    # 2. Start Background Tasks
    task_backup = asyncio.create_task(schedule_daily_backup())
    task_refresh = asyncio.create_task(schedule_device_refresh()) # ★追加
    
    # 音声ファイルの整合性チェック
    sound_manager.check_and_restore_sounds()
    
    # 3. Seed DB
    try:
        quest_router.seed_data()
        logger.info("✅ Quest DB Seeded")
    except Exception as e:
        logger.error(f"Quest seed error: {e}")

    yield
    
    # Shutdown logic
    task_backup.cancel()
    task_refresh.cancel() # ★追加
    logger.info("🛑 System Shutdown.")


# --- FastAPI App Definition ---
app = FastAPI(lifespan=lifespan)
# ▼▼▼ 追加: 全体エラーハンドリング (500エラーの見える化) ▼▼▼
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    想定外のエラーが発生した場合に、ログにスタックトレースを出力し、
    フロントエンドにJSON形式でエラー内容を返却する
    """
    # エラーの詳細（スタックトレース）を取得
    tb_str = traceback.format_exc()
    
    # ログに詳細を出力 (これが原因特定に不可欠)
    logger.error(f"❌ Unhandled Server Error at {request.url.path}\n{tb_str}")
    
    # クライアントへのレスポンス (500 Internal Server Error)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "Internal Server Error",
            "detail": str(exc),  # 開発用: エラーメッセージそのものを返す
            "path": request.url.path
        }
    )

handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)

# Router
app.include_router(quest_router.router, prefix="/api/quest", tags=["Quest"])

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,  # configから読み込み
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Endpoints: LINE ---
@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)):
    body = (await request.body()).decode('utf-8')
    
    # イベントループの取得
    loop = asyncio.get_running_loop()
    
    try: 
        # handler.handle をスレッドプールで実行し、完了を待機
        await loop.run_in_executor(None, lambda: handler.handle(body, x_line_signature))
    except InvalidSignatureError:
        logger.warning("Invalid Signature detected.")
        raise HTTPException(status_code=400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try: 
        line_logic.process_message(event, line_bot_api)
    except Exception as e: 
        logger.error(f"メッセージ処理中にエラー発生: {e}")

@handler.add(PostbackEvent)
def handle_postback_event(event):
    from handlers import line_logic
    line_logic.handle_postback(event, line_bot_api)


# --- Endpoints: SwitchBot ---
@app.post("/webhook/switchbot")
async def callback_switchbot(body: SwitchBotWebhookBody):
    """SwitchBot Webhook Endpoint with Pydantic Validation"""
    ctx = body.context
    mac = ctx.deviceMac
    
    # 1. Identify Device
    device_conf = next((d for d in config.MONITOR_DEVICES if d["id"] == mac), None)
    
    # ▼▼▼ 修正: 名前解決の優先順位変更 (API > Config > Unknown) ▼▼▼
    api_name = sb_tool.get_device_name_by_id(mac)
    config_name = device_conf.get("name") if device_conf else None
    name = api_name or config_name or f"Unknown_{mac}"
    # ▲▲▲ 修正終了 ▲▲▲

    if device_conf:
        location = device_conf.get("location", "場所不明")
        dev_type = device_conf.get("type", "Unknown")
    else:
        location = "未登録"
        dev_type = "Unknown"

    state = str(ctx.detectionState).lower()
    
    # 2. Logging to DB
    try:
        # ★非同期ラッパーを await で呼ぶように変更
        await common.save_log_async(config.SQLITE_TABLE_SENSOR, 
            ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
            (common.get_now_iso(), name, mac, "Webhook Device", state, ctx.brightness or ""))
    except Exception as e:
        logger.error(f"Failed to save log: {e}")
    
    if state:
        logger.info(f"[SENSOR] 受信: {name} ({location}) -> {state}")

    # 3. Notification Logic
    await _process_sensor_logic(mac, name, location, dev_type, state)

    return {"status": "success"}


async def _process_sensor_logic(mac: str, name: str, location: str, dev_type: str, state: str):
    """Separate logic for Motion vs Contact sensors"""
    msg_text = None
    current_time = time.time()
    
    # A. Motion Sensor
    if "Motion" in dev_type:
        if state == "detected":
            # 動きあり: 既存タイマーキャンセル & 通知(初回のみ)
            if mac in MOTION_TASKS:
                MOTION_TASKS[mac].cancel()
                del MOTION_TASKS[mac]
            
            if not IS_ACTIVE.get(mac, False):
                msg_text = f"👀【{location}・見守り】\n{name} で動きがありました"
                IS_ACTIVE[mac] = True 
        
        elif state == "not_detected":
            # 動きなし: 監視タイマー開始
            if IS_ACTIVE.get(mac, False):
                if mac in MOTION_TASKS:
                    MOTION_TASKS[mac].cancel()
                
                task = asyncio.create_task(send_inactive_notification(mac, name, location, MOTION_TIMEOUT))
                MOTION_TASKS[mac] = task

    # B. Contact Sensor
    elif state in ["open", "timeoutnotclose"]:
        last_time = LAST_NOTIFY_TIME.get(mac, 0)
        # Cooldown check
        if current_time - last_time > CONTACT_COOLDOWN:
            if state == "open":
                msg_text = f"🚪【{location}・防犯】\n{name} が開きました"
            else:
                msg_text = f"⚠️【{location}・注意】\n{name} が開けっ放しです"
            
            LAST_NOTIFY_TIME[mac] = current_time

    # Send Notification if needed
    if msg_text:
        # run_in_executor is preferred for blocking I/O like requests
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            common.send_push,
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg_text}], 
            None,
            "discord", 
            "notify"
        )
        logger.info(f"通知送信: {msg_text}")


# --- Static Files ---
if hasattr(config, "ASSETS_DIR") and os.path.exists(config.ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")

# ★追加: アップロード画像を /uploads/xxx.jpg でアクセス可能にする
if hasattr(config, "UPLOAD_DIR"):
    app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")
    # logger.info(f"✅ Uploads mounted from {config.UPLOAD_DIR}")

if os.path.exists(config.QUEST_DIST_DIR):
    app.mount("/quest", StaticFiles(directory=config.QUEST_DIST_DIR, html=True), name="quest")
    # logger.info(f"✅ Family Quest mounted from {config.QUEST_DIST_DIR}")
else:
    logger.warning(f"⚠️ Family Quest dist not found at {config.QUEST_DIST_DIR}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)