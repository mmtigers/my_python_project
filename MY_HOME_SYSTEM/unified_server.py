# HOME_SYSTEM/unified_server.py
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import time
import datetime
import asyncio
import config
import common
import switchbot_get_device_list as sb_tool
from handlers import line_logic
import backup_database

logger = common.setup_logging("server")

# 状態管理
LAST_NOTIFY_TIME = {} # 開閉センサーの連打防止用 (mac: timestamp)
IS_ACTIVE = {}        # 人感センサーの活動状態 (mac: bool)
MOTION_TASKS = {}     # 人感センサーの「動きなし監視タイマー」 (mac: asyncio.Task)

# 定数設定
CONTACT_COOLDOWN = 300   # 開閉センサー: 5分 (連打防止)
MOTION_TIMEOUT = 900     # 人感センサー: 15分 (動きなし判定までの時間)


# --- バックグラウンドタスク: 定期バックアップ ---
async def schedule_daily_backup():
    """毎日AM3:00にバックアップを実行するループ"""
    logger.info("🕰️ バックアップスケジューラ起動 (Target: 03:00)")
    while True:
        now = datetime.datetime.now()
        # 次の3時を計算
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        
        wait_seconds = (target - now).total_seconds()
        logger.info(f"⏳ 次回バックアップまで待機: {int(wait_seconds/3600)}時間{int((wait_seconds%3600)/60)}分")
        
        # 待機
        await asyncio.sleep(wait_seconds)
        
        # 実行
        logger.info("📦 定期バックアップを開始します...")
        # ファイル操作などの重い処理はExecutorで実行してサーバーを止めない
        loop = asyncio.get_running_loop()
        success, res, size = await loop.run_in_executor(None, backup_database.perform_backup)
        
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
            
        # 連続実行防止のため少し待つ
        await asyncio.sleep(60)

# --- ライフサイクル (起動時・終了時) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 System Season 3 Starting...")
    
    # 1. キャッシュ更新
    sb_tool.fetch_device_name_cache()
    
    # 2. バックアップタスクを開始
    asyncio.create_task(schedule_daily_backup())
    
    yield
    logger.info("🛑 System Shutdown.")


# ★★★ アプリケーションの作成 (これより下にルートを追加すること) ★★★
app = FastAPI(lifespan=lifespan)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)


# --- Quest API用モデル ---
class QuestAction(BaseModel):
    user_id: str
    task_id: int
    points: int
    completed: bool  # True:完了, False:キャンセル

class RewardRedemption(BaseModel):
    user_id: str
    cost: int
    reward_title: str

# --- Quest API エンドポイント ---

@app.post("/api/quest/complete")
async def complete_quest(action: QuestAction):
    # 旧エンドポイント互換性のため残す場合はこれを使いますが、
    # 下の /api/quest/action の方が高機能です
    msg = f"🌟 Family Quest\n{action.user_id} がタスク完了！\n+{action.points}ポイント GET!"
    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="line")
    return {"status": "ok", "new_points": 100}

@app.get("/api/quest/data")
async def get_quest_data():
    """初期表示用：全データ（ユーザー、タスク、今日の進捗）を取得"""
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    
    with common.get_db_cursor() as cur:
        # 1. ユーザーと現在のポイント
        cur.execute("SELECT id, name, current_points FROM quest_users")
        users = {row[0]: {"points": row[2], "name": row[1]} for row in cur.fetchall()}
        
        # 2. タスク定義
        cur.execute("SELECT id, target_user_id, title, icon_name, points FROM quest_tasks")
        tasks = []
        for row in cur.fetchall():
            tasks.append({
                "id": row[0],
                "userId": row[1],
                "title": row[2],
                "icon": row[3], # アイコン名はフロントで解決
                "points": row[4],
                "isCompleted": False # デフォルト
            })
            
        # 3. 今日の完了状況を上書き
        cur.execute("SELECT task_id FROM quest_status WHERE date = ? AND is_completed = 1", (today,))
        completed_ids = set(row[0] for row in cur.fetchall())
        
        for task in tasks:
            if task["id"] in completed_ids:
                task["isCompleted"] = True
                
    return {"users": users, "tasks": tasks}

@app.post("/api/quest/action")
async def update_quest_status(action: QuestAction):
    """タスクの完了・キャンセル切り替え"""
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    logger.info(f"Quest Action: {action}")

    with common.get_db_cursor(commit=True) as cur:
        # 1. ステータス更新 (Upsert的な処理)
        cur.execute("""
            INSERT INTO quest_status (task_id, date, is_completed, completed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_id, date) DO UPDATE SET
            is_completed = excluded.is_completed,
            completed_at = excluded.completed_at
        """, (action.task_id, today, 1 if action.completed else 0, datetime.datetime.now()))

        # 2. ポイント増減
        delta = action.points if action.completed else -action.points
        cur.execute("UPDATE quest_users SET current_points = MAX(0, current_points + ?) WHERE id = ?", (delta, action.user_id))
        
        # 3. 最新ポイント取得
        cur.execute("SELECT current_points, name FROM quest_users WHERE id = ?", (action.user_id,))
        res = cur.fetchone()
        new_points = res[0]
        user_name = res[1]

    # 4. 通知 (完了時のみ)
    if action.completed:
        msg = f"🎉【Family Quest】\n{user_name}が「タスク」を達成しました！\n💰 +{action.points} pt (現在: {new_points} pt)"
        # Discord通知に変更 (LINE不調のため)
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel="notify")

    return {"status": "success", "newPoints": new_points}

@app.post("/api/quest/redeem")
async def redeem_reward(req: RewardRedemption):
    """ごほうび交換"""
    with common.get_db_cursor(commit=True) as cur:
        # ポイント消費
        cur.execute("UPDATE quest_users SET current_points = current_points - ? WHERE id = ?", (req.cost, req.user_id))
        
        cur.execute("SELECT current_points, name FROM quest_users WHERE id = ?", (req.user_id,))
        res = cur.fetchone()
        new_points = res[0]
        user_name = res[1]

    # 通知
    msg = f"🎁【ごほうび交換】\n{user_name}が「{req.reward_title}」を交換しました！\n消費: {req.cost} pt (残り: {new_points} pt)\nパパママ、確認お願いします！"
    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="line")

    return {"status": "success", "newPoints": new_points}


# --- 非同期通知ヘルパー ---
async def send_inactive_notification(mac, name, location, timeout):
    """指定時間待機し、キャンセルされなければ「動きなし」を通知する"""
    try:
        # 指定時間待つ (この間に detected が来ればキャンセルされる)
        await asyncio.sleep(timeout)
        
        # 時間経過後、通知を実行
        msg = f"💤【{location}・見守り】\n{name} の動きが止まりました（{int(timeout/60)}分経過）"
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, 
            common.send_push, 
            config.LINE_USER_ID, 
            [{"type": "text", "text": msg}], 
            None, # image_data
            "discord", 
            "notify"
        )
        
        logger.info(f"通知送信: {msg}")
        
        # 状態リセット
        IS_ACTIVE[mac] = False
        if mac in MOTION_TASKS:
            del MOTION_TASKS[mac]

    except asyncio.CancelledError:
        # キャンセルされた＝動きがあったので何もしない
        logger.info(f"動きなしタイマーキャンセル: {name} (活動継続)")

# --- LINE / SwitchBot エンドポイント ---

@app.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)):
    body = (await request.body()).decode('utf-8')
    try: handler.handle(body, x_line_signature)
    except InvalidSignatureError:
        logger.warning("Invalid Signature detected.")
        raise HTTPException(status_code=400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try: line_logic.process_message(event, line_bot_api)
    except Exception as e: logger.error(f"メッセージ処理中にエラー発生: {e}")

@app.post("/webhook/switchbot")
async def callback_switchbot(request: Request):
    """SwitchBot Webhook エンドポイント"""
    data = await request.json()
    ctx = data.get("context", {})
    mac = ctx.get("deviceMac")
    if not mac: return {"status": "ignored"}
    
    # 1. デバイス情報の特定
    device_conf = next((d for d in config.MONITOR_DEVICES if d["id"] == mac), None)
    
    if device_conf:
        name = device_conf.get("name") or sb_tool.get_device_name_by_id(mac) or f"Unknown_{mac}"
        location = device_conf.get("location", "場所不明")
        dev_type = device_conf.get("type", "Unknown")
    else:
        name = sb_tool.get_device_name_by_id(mac) or f"Unknown_{mac}"
        location = "未登録"
        dev_type = "Unknown"

    state = str(ctx.get("detectionState", "")).lower()
    
    # 2. DB記録 (全イベント保存)
    common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (common.get_now_iso(), name, mac, "Webhook Device", state, ctx.get("brightness", "")))
    
    if state:
        logger.info(f"[SENSOR] 受信: {name} ({location}) -> {state}")

    # 3. 通知ロジック
    msg_text = None
    current_time = time.time()
    
    # A. 人感センサー (Motion Sensor) - 新ロジック
    if "Motion" in dev_type:
        # --- 動きあり (DETECTED) ---
        if state == "detected":
            if mac in MOTION_TASKS:
                MOTION_TASKS[mac].cancel()
                del MOTION_TASKS[mac]
            
            if not IS_ACTIVE.get(mac, False):
                msg_text = f"👀【{location}・見守り】\n{name} で動きがありました"
                IS_ACTIVE[mac] = True 
        
        # --- 動きなし (NOT_DETECTED) ---
        elif state == "not_detected":
            if IS_ACTIVE.get(mac, False):
                if mac in MOTION_TASKS:
                    MOTION_TASKS[mac].cancel()
                
                task = asyncio.create_task(send_inactive_notification(mac, name, location, MOTION_TIMEOUT))
                MOTION_TASKS[mac] = task

    # B. 開閉センサー (Contact Sensor)
    elif state in ["open", "timeoutnotclose"]:
        last_time = LAST_NOTIFY_TIME.get(mac, 0)
        if current_time - last_time > CONTACT_COOLDOWN:
            if state == "open":
                msg_text = f"🚪【{location}・防犯】\n{name} が開きました"
            else:
                msg_text = f"⚠️【{location}・注意】\n{name} が開けっ放しです"
            
            LAST_NOTIFY_TIME[mac] = current_time

    if msg_text:
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_text}], target="discord", channel="notify")
        logger.info(f"通知送信: {msg_text}")

    return {"status": "success"}


# ▼▼▼ 静的ファイルのマウント (必ず最後に追加) ▼▼▼

# NASアセット
if hasattr(config, "ASSETS_DIR"):
    app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")

# Family Quest アプリ
# ディレクトリが存在するか確認してからマウントする安全策
import os
QUEST_DIST_DIR = "/home/masahiro/develop/family-quest/dist"
if os.path.exists(QUEST_DIST_DIR):
    app.mount("/quest", StaticFiles(directory=QUEST_DIST_DIR, html=True), name="quest")
    logger.info("✅ Family Quest mounted.")
else:
    logger.warning(f"⚠️ Family Quest dist not found at {QUEST_DIST_DIR}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)