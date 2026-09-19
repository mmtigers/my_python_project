# MY_HOME_SYSTEM/routers/webhook_router.py
import asyncio
import hmac
import time
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Request, Header, HTTPException
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
async def callback_line(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: Optional[str] = Header(None),
) -> str:
    """LINE Bot Webhook"""
    if not line_handler.line_handler:
        raise HTTPException(status_code=501, detail="LINE Bot not configured")

    # L-L1 (#410): 署名ヘッダが無いと SDK 内部で AttributeError となり、以前は下の汎用
    # except に落ちて 200 "OK" を返していた(署名検証していないのに成功応答)。
    # 署名なしは不正リクエストとして 400 で即拒否する。
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature")

    # L-L1: 不正なバイト列は以前 try の外で UnicodeDecodeError → 500 になっていた。
    try:
        body = (await request.body()).decode('utf-8')
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Body is not valid UTF-8")

    # Issue #376: 以前は WebhookHandler.handle() が署名検証・パース・ディスパッチ
    # (AI呼び出し・DB書き込み・LINE返信を含む)を全てHTTPレスポンス送信前に完走させて
    # いたため、AI経路の遅延がそのまま reply token(約1分で失効)の失効リスクに直結して
    # いた。ここでは署名検証とイベントのパースのみを同期的に行い(HMAC計算とJSONパースの
    # みでネットワークI/Oを伴わないため軽量)、即座に200を返す。実処理
    # (handlers/line_handler.dispatch_events_async、イベント単位の例外隔離・再配信スキップ・
    # webhookEventIdベースの冪等化・AI呼び出し等はそちら側で行う)はBackgroundTasksで
    # レスポンス送信後に実行する。
    try:
        events = line_handler.line_handler.parser.parse(body, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    except Exception as e:
        # 署名は正しいが本文が不正(JSON decode失敗等)。LINE側のリトライ挙動に
        # 巻き込まれないよう、ログのみで200を返す(以前からの挙動を踏襲)。
        logger.error(f"LINE callback parse error: {e}")
        return "OK"

    # Issue #664: コルーチン(`dispatch_events_async`)を渡すことで、Starlette が
    # スレッドプールではなくこのサーバーのイベントループ上で await する。以前は同期版の
    # `dispatch_events` を渡しており、その内側で着信メッセージ1件ごとに `asyncio.run(...)`
    # が新しいイベントループを生成しては破棄していた(`services/ai_service` の
    # Gemini 非同期クライアントはモジュールレベルで1度だけ生成されるため、
    # 毎回別のループから使われる構成になっていた)。
    # ループを塞ぐ同期I/O(LINE Profile/reply API・Postback経路の line_logic)は
    # line_handler 側で `asyncio.to_thread` へ逃がしてある。
    background_tasks.add_task(line_handler.dispatch_events_async, events)
    return "OK"

# 対象とするセンサーのデバイスタイプ（温湿度計やプラグ等は除外）
# "Contact Sensor"/"Motion Sensor" はデバイス一覧API(GET /devices)の語彙で、
# SwitchBot公式Webhookの context.deviceType はこれとは異なる語彙
# ("WoContact"/"WoPresence"等)を使う。両方を許容することで、
# Webhook本来の形式が来ても「対象外デバイス」として黙って捨てられないようにする。
TARGET_DEVICE_TYPES = {
    "Contact Sensor", "Motion Sensor",  # デバイス一覧APIの語彙(後方互換)
    "WoContact", "WoPresence",  # 公式Webhookペイロードの語彙
}

# Issue #648: トークン未設定の拒否をERRORで通知するのはプロセス起動後の最初の1回だけにする。
_unconfigured_webhook_error_logged: bool = False


@router.post("/webhook/switchbot")
async def switchbot_webhook(body: SwitchBotWebhookBody, token: str = None):
    """SwitchBot Webhook受信・処理"""
    # SwitchBotにはLINEのような署名検証機構がないため、
    # クエリパラメータ ?token=... による簡易な共有シークレット検証を行う。
    # Issue #648: トークン未設定時は 503 で拒否する(フェイルクローズ)。このエンドポイントは
    # ip_restriction_middleware の対象外で、エッジの Cloudflare Access もバイパスする設計
    # (#321/#517)のため、トークンが唯一の防御になる。無検証で受け付けると第三者が任意の
    # deviceMac を POST して device_records/daily_logs への書き込み、LINE/Discord通知、
    # SwitchBot API 呼び出し(リトライ込み)を誘発できる。
    # 実機のトークン設定(#318)が済むまでの移行用に、明示的なオプトイン
    # (ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK=true)でのみ従来動作を残す。
    if not config.SWITCHBOT_WEBHOOK_TOKEN:
        if not getattr(config, "ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK", False):
            # ERROR は core/logger の DiscordErrorHandler 経由で通知が飛ぶ。拒否のたびに出すと、
            # 外部から誰でも通知を大量発火させられるため、プロセスごとに最初の1回だけにする。
            global _unconfigured_webhook_error_logged
            if not _unconfigured_webhook_error_logged:
                _unconfigured_webhook_error_logged = True
                logger.error(
                    "❌ SWITCHBOT_WEBHOOK_TOKEN が未設定のため /webhook/switchbot を拒否しました(503)。"
                    ".env に SWITCHBOT_WEBHOOK_TOKEN を設定し、SwitchBot 側の Webhook URL にも "
                    "?token=... を付けてください(移行中は ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK=true で従来動作)。"
                    " ※以降の同じ拒否は debug ログのみ。"
                )
            else:
                logger.debug("SWITCHBOT_WEBHOOK_TOKEN 未設定のため /webhook/switchbot を拒否(503)。")
            raise HTTPException(
                status_code=503,
                detail="SwitchBot webhook is not configured (SWITCHBOT_WEBHOOK_TOKEN is unset)",
            )
    else:
        # hmac.compare_digest は str 同士だと非ASCII文字を含む場合に TypeError を送出する
        # ("comparing strings with non-ASCII characters is not supported")。このエンドポイントは
        # 外部公開されているため、?token=%C3%A9 のような1リクエストで 500 + Discordエラー通知
        # (global_exception_handler 経由)を誰でも発生させられていた。bytes に揃えて比較する。
        if not token or not hmac.compare_digest(
            token.encode("utf-8"), config.SWITCHBOT_WEBHOOK_TOKEN.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="Invalid token")

    ctx = body.context
    mac = ctx.deviceMac
    
    # ctx.deviceType は Optional フィールドとして常に存在するため、getattr の
    # デフォルト値は「未定義時のみ」で「Noneの時」には効かない。公式形式
    # (context.deviceType)を優先しつつ、Noneの場合はトップレベルへフォールバックする。
    device_type = ctx.deviceType or getattr(body, "deviceType", None) or "Unknown"

    # ガード節 1: 対象外デバイス (Fail-Fast)
    if device_type not in TARGET_DEVICE_TYPES:
        logger.debug(f"Ignored webhook from unsupported device type: {device_type} (MAC: {mac})")
        return {"status": "ignored", "reason": "unsupported_device"}

    # WoContact(開閉センサー)は ctx.openState ("open"/"close"/"timeOutNotClose") が
    # 実際の開閉状態を表す。ctx.detectionState は同デバイス内蔵PIRのモーション検知結果
    # ("DETECTED"/"NOT_DETECTED")であり開閉状態ではないため、開閉判定には使わない
    # (SwitchBot公式Webhookドキュメント参照。Issue #251: 修正前はdetectionStateを
    # 開閉状態として扱っていたため、実機からのWebhookでは"open"/"timeoutnotclose"に
    # 一致せず、ドア開放時の防犯通知が発火しなかった)。openStateが未設定のペイロード
    # (過去互換)ではdetectionStateへフォールバックする。
    # WoPresence(人感センサー)はdetectionStateがそのままモーション検知状態を表すため、
    # 従来通りdetectionStateを用いる。
    if device_type in ("WoContact", "Contact Sensor") and ctx.openState is not None:
        state = str(ctx.openState).lower()
    else:
        state = str(ctx.detectionState).lower()
    current_time = time.time()

    # 🌟 追加: ガード節 2 - イベントの重複排除 (Fail-Fast)
    # 連続アクセスによるDBへの過剰書き込みをインメモリで防御
    if sensor_service.is_duplicate_webhook(mac, state, current_time):
        # ガイドライン(6.1)に基づき、ログのノイズ化を防ぐため DEBUG レベルで出力
        logger.debug(f"Duplicate webhook ignored for device: {mac}, state: {state}")
        return {"status": "ignored", "reason": "duplicate_event"}

    # --- これ以降は重複していない有効なイベントのみが通過する ---
    
    # デバイス情報の解決 (既存ロジック)
    # get_device_name_by_id はキャッシュ未取得時(プロセス起動後の最初のイベント)に
    # SwitchBot API へ同期HTTP(最大 10秒×4回 + バックオフ ≒ 47秒)を行う。async ハンドラ内で
    # 直接呼ぶとその間イベントループ全体(LINE callback・Alexa・/health・quest API)が停止する
    # ため、スレッドプールへ逃がす。
    api_name = await asyncio.to_thread(sb_tool.get_device_name_by_id, mac)
    device_conf = next((d for d in config.MONITOR_DEVICES if d.get("id") == mac), None)
    
    name = api_name or (device_conf.get("name") if device_conf else f"Unknown_{mac}")
    location = device_conf.get("location", "未登録") if device_conf else "場所不明"

    # 1. ログ保存 (互換性維持)
    # Issue #740 (AUDIT-011): save_log_async は Fail-Soft で False を返す(DBロックの
    # リトライ超過・ディスクフル・スキーマ不整合)。以前は戻り値を破棄して無条件に
    # 200 {"status": "success"} を返していたため、SwitchBot は「配信成功」と判断して
    # 再送せず、ドアの開閉・人感センサーの検知が恒久的に失われていた(防犯通知の原資
    # でもあるデータ)。これは #373 で LINE 経路にだけ適用された「戻り値を見る」修正の
    # 未展開分にあたる。503 を返して再送に委ねる。
    # 再送による二重記録は上の is_duplicate_webhook(インメモリ重複排除)が防ぐ。
    save_ok = await save_log_async("device_records",
        ["timestamp", "device_name", "device_id", "device_type", "contact_state", "brightness_state"],
        (get_now_iso(), name, mac, "Webhook", state, ctx.brightness or "")
    )
    if not save_ok:
        logger.error(
            f"SwitchBot Webhook のイベント保存に失敗しました (device={name}, mac={mac}, state={state})。"
            "503 を返して SwitchBot の再送に委ねます。"
        )
        raise HTTPException(
            status_code=503,
            detail="Failed to persist the sensor event; please retry",
        )

    # 2. 新テーブル(daily_logs)への保存
    # こちらは device_records の派生である補助的な記録(人が読む日次ログ)であり、
    # 失われても一次データは device_records に残る。ここで 503 を返すと保存済みの
    # device_records ごと再送させることになり、重複排除の窓を超えた再送では
    # 二重記録になりうるため、**失敗してもエラーログにとどめて通知処理を続行する**
    # (Issue #740: この判断は明示的なものであり、書き忘れではない)。
    if state in ["detected", "open", "timeoutnotclose"]:
        detail_msg = f"{name}: {state}"
        daily_ok = await save_log_async(config.SQLITE_TABLE_DAILY_LOGS,
            ["category", "detail", "timestamp"],
            ("Sensor", detail_msg, get_now_iso())
        )
        if not daily_ok:
            logger.error(
                f"daily_logs への記録に失敗しました (device={name}, state={state})。"
                "device_records は保存済みのため処理は続行します。"
            )

    # 3. センサーロジック (Service呼び出し)
    # device_type(61行目で context.deviceType/トップレベルから解決済み)を渡す。
    # 以前はここで未解決の body.deviceType(公式Webhook形式では常にNone)を渡していたため、
    # process_sensor_data のMotion判定に到達せず通知・無反応タイマーが発火しなかった(#94)。
    await sensor_service.process_sensor_data(mac, name, location, device_type, state)
    
    return {"status": "success"}