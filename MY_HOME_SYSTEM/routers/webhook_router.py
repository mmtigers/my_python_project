# MY_HOME_SYSTEM/routers/webhook_router.py
import asyncio
from fastapi import APIRouter, Request, Header, HTTPException
from linebot.v3.exceptions import InvalidSignatureError

import config
from core.logger import setup_logging
from core.database import save_log_async
from core.utils import get_now_iso
from services import sensor_service, switchbot_service as sb_tool
from handlers import line_handler
from models.switchbot import SwitchBotWebhookBody

logger = setup_logging("webhook_router")
router = APIRouter()

@router.post("/callback/line")
async def callback_line(request: Request, x_line_signature: str = Header(None)) -> str:
    """LINE Bot Webhook"""
    if not line_handler.line_handler:
        raise HTTPException(status_code=501, detail="LINE Bot not configured")
    
    body = (await request.body()).decode('utf-8')
    try:
        # 同期ハンドラをスレッドで実行
        await asyncio.to_thread(line_handler.line_handler.handle, body, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    except Exception as e:
        logger.error(f"LINE callback error: {e}")
    return "OK"

# 対象とするセンサーのデバイスタイプ（温湿度計やプラグ等は除外）
TARGET_DEVICE_TYPES = ["Contact Sensor", "Motion Sensor"]

@router.post("/webhook/switchbot")
async def switchbot_webhook(body: SwitchBotWebhookBody):
    """SwitchBot Webhook受信・処理"""
    ctx = body.context
    mac = ctx.deviceMac
    
    # 🌟 追加: デバイスタイプの取得（フォールバック付き）
    device_type = getattr(ctx, "deviceType", getattr(body, "deviceType", "Unknown"))

    # 🌟 追加: ガード節 (Fail-Fast)
    # 対象外のデバイスからのWebhookはDBアクセスを行う前に即座に破棄し、I/Oを保護する
    if device_type not in TARGET_DEVICE_TYPES:
        # ログレベル設計に準拠: 不要な通知やエラーは出さず、DEBUGレベルで記録
        logger.debug(f"Ignored webhook from unsupported device type: {device_type} (MAC: {mac})")
        return {"status": "ignored", "reason": "unsupported_device"}

    # デバイス情報の解決 (既存ロジック)
    api_name = sb_tool.get_device_name_by_id(mac)
    device_conf = next((d for d in config.MONITOR_DEVICES if d.get("id") == mac), None)
    
    name = api_name or (device_conf.get("name") if device_conf else f"Unknown_{mac}")
    location = device_conf.get("location", "未登録") if device_conf else "場所不明"
    state = str(ctx.detectionState).lower()

    # 1. ログ保存 (互換性維持)
    await save_log_async("device_records", 
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (get_now_iso(), name, mac, "Webhook", state, ctx.brightness or "")
    )
    
    # 2. 新テーブル(daily_logs)への保存
    if state in ["detected", "open", "timeoutnotclose"]:
        detail_msg = f"{name}: {state}"
        await save_log_async(config.SQLITE_TABLE_DAILY_LOGS,
            ["category", "detail", "timestamp"],
            ("Sensor", detail_msg, get_now_iso())
        )

    # 3. センサーロジック (Service呼び出し)
    await sensor_service.process_sensor_data(mac, name, location, body.deviceType, state)
    
    return {"status": "success"}