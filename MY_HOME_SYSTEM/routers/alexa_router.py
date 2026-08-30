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
from fastapi import APIRouter, Request, HTTPException

from core.logger import setup_logging
from core.alexa_verifier import verify_signature, verify_timestamp, AlexaVerificationError
from handlers.alexa_handler import skill

logger = setup_logging("alexa_router")
router = APIRouter()


@router.post("/webhook/alexa")
async def alexa_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("Signature", "")
    cert_chain_url = request.headers.get("SignatureCertChainUrl", "")

    try:
        verify_signature(raw_body, signature, cert_chain_url)
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

    try:
        request_envelope = skill.serializer.deserialize(
            payload=raw_body.decode("utf-8"),
            obj_type="ask_sdk_model.request_envelope.RequestEnvelope",
        )
        response_envelope = skill.invoke(request_envelope=request_envelope, context=None)
        return skill.serializer.serialize(response_envelope)
    except Exception as e:
        logger.error(f"Alexa skill invocation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Skill invocation failed")
