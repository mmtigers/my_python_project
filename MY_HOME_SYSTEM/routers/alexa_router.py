# MY_HOME_SYSTEM/routers/alexa_router.py
"""
Alexaカスタムスキル「ファミクエ」のWebサービスエンドポイント。

Alexa Developer Console側のスキルエンドポイントに、このunified_serverの
公開URL + `/webhook/alexa` を設定することで、AWS Lambda等を用意せずに
既存インフラ(LINE Webhookと同じ公開HTTPS)上でスキルを動かす。

リクエストごとに:
    1. core.alexa_verifier で Signature / SignatureCertChainUrl ヘッダとタイムスタンプを検証
    2. handlers.alexa_handler.skill (ask-sdk-core) にディスパッチ
    3. 生成された ResponseEnvelope をJSONとして返す
"""
import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Request, HTTPException

import config
from core.logger import setup_logging
from core.alexa_verifier import verify_signature, verify_timestamp, AlexaVerificationError
from handlers.alexa_handler import skill

logger = setup_logging("alexa_router")
router = APIRouter()


def _application_id(payload: Any) -> Optional[str]:
    """要求の applicationId(context.System.application → session.application の順)。"""
    if not isinstance(payload, dict):
        return None
    for path in (("context", "System", "application"), ("session", "application")):
        node: Any = payload
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and node.get("applicationId"):
            return str(node["applicationId"])
    return None


def _require_skill_configured() -> None:
    """Issue #1149: ALEXA_SKILL_ID が未設定なら 503 で拒否する(明示的なオプトインを除く)。"""
    if config.ALEXA_SKILL_ID or config.ALEXA_ALLOW_ANY_SKILL:
        return
    logger.warning("ALEXA_SKILL_ID が未設定のため /webhook/alexa を拒否しました(503)。")
    raise HTTPException(status_code=503, detail="Alexa skill is not configured (ALEXA_SKILL_ID is unset)")


@router.post("/webhook/alexa")
async def alexa_webhook(request: Request):
    # Issue #1149: 設定が無ければ、署名検証(証明書の取得を伴う)の前に拒否する
    _require_skill_configured()
    raw_body = await request.body()
    signature = request.headers.get("Signature", "")
    cert_chain_url = request.headers.get("SignatureCertChainUrl", "")

    try:
        # #230: verify_signatureは証明書キャッシュミス時にrequests.get()で証明書
        # チェーンを同期取得する。unified_serverはworkers指定なしの単一プロセス・
        # 単一イベントループ構成のため、awaitせず直接呼ぶとこのI/O待ちの間、
        # 同時間帯のSwitchBot/LINE Webhook・Family Quest APIの処理まで巻き込んで
        # ブロックしてしまう。LINE経路(webhook_router.py)と同様にスレッドへ退避する。
        await asyncio.to_thread(verify_signature, raw_body, signature, cert_chain_url)
    except AlexaVerificationError as e:
        logger.warning(f"Alexa request signature verification failed: {e}")
        raise HTTPException(status_code=400, detail="Signature verification failed")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    request_timestamp = (payload.get("request") or {}).get("timestamp", "")
    try:
        verify_timestamp(request_timestamp)
    except AlexaVerificationError as e:
        logger.warning(f"Alexa request timestamp verification failed: {e}")
        raise HTTPException(status_code=400, detail="Timestamp verification failed")

    # Issue #1149: 正しく署名されていても、別のスキルからの要求は拒否する。ask-sdk-core の
    # skill_id 検証(handlers/alexa_handler.py)は起動時の設定しか見ず、不一致を例外(=500)で返すため、
    # ここで先に 403 にする。
    if config.ALEXA_SKILL_ID and _application_id(payload) != config.ALEXA_SKILL_ID:
        logger.warning(f"Alexa request from an unexpected skill was rejected: {str(_application_id(payload))[:100]!r}")
        raise HTTPException(status_code=403, detail="Unexpected skill")

    try:
        request_envelope = skill.serializer.deserialize(
            payload=raw_body.decode("utf-8"),
            obj_type="ask_sdk_model.request_envelope.RequestEnvelope",
        )
        # #230: skill.invoke()はhandlers/alexa_handler.pyのハンドラ経由で
        # quest_service(SQLite同期アクセス)を呼び出す。同じ理由でスレッドへ退避する。
        response_envelope = await asyncio.to_thread(
            skill.invoke, request_envelope=request_envelope, context=None
        )
        return skill.serializer.serialize(response_envelope)
    except Exception as e:
        logger.error(f"Alexa skill invocation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Skill invocation failed")
