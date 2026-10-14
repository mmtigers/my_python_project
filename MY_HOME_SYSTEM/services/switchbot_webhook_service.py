# MY_HOME_SYSTEM/services/switchbot_webhook_service.py
"""SwitchBot Webhook の受信前処理(共有シークレット検証・イベントの抽出)。

Issue #954: 以前は `routers/webhook_router.py` の `switchbot_webhook`(135行)に
これらの判定が直接書かれていた。ルーターは「リクエストのパース・検証のみ行い、ロジックは
services/ へ委譲する」設計方針(CLAUDE.md)のため、I/O を伴わない判定をここへ移した。
挙動は変えていない。DB保存・SwitchBot API 呼び出し・センサーロジックの呼び出しは、
従来どおりルーター側(`save_log_async` / `sb_tool` / `sensor_service` を差し替える
既存テストの前提)に残している。
"""
import hmac
from typing import NamedTuple, Optional

from fastapi import HTTPException

import config
from core.logger import setup_logging
from models.switchbot import SwitchBotWebhookBody

logger = setup_logging("switchbot_webhook_service")

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


class WebhookEvent(NamedTuple):
    """受け付けたセンサーイベント(対象デバイスのものだけ)。"""

    mac: str
    device_type: str
    state: str  # 小文字化済み("open" / "timeoutnotclose" / "detected" 等)


def verify_token(token: Optional[str]) -> None:
    """共有シークレットを検証する。不正なら HTTPException(503 未設定 / 401 不一致)を送出する。"""
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


def extract_event(body: SwitchBotWebhookBody) -> Optional[WebhookEvent]:
    """ペイロードから対象デバイスのイベントを取り出す。対象外のデバイスタイプなら None。"""
    ctx = body.context
    mac = ctx.deviceMac

    # ctx.deviceType は Optional フィールドとして常に存在するため、getattr の
    # デフォルト値は「未定義時のみ」で「Noneの時」には効かない。公式形式
    # (context.deviceType)を優先しつつ、Noneの場合はトップレベルへフォールバックする。
    device_type = ctx.deviceType or getattr(body, "deviceType", None) or "Unknown"

    # 対象外デバイス (Fail-Fast)
    if device_type not in TARGET_DEVICE_TYPES:
        logger.debug(f"Ignored webhook from unsupported device type: {device_type} (MAC: {mac})")
        return None

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

    return WebhookEvent(mac=mac, device_type=device_type, state=state)
