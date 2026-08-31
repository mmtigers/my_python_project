# MY_HOME_SYSTEM/tests/test_alexa_router.py
"""
routers/alexa_router.py (/webhook/alexa) の結線テスト。

署名/タイムスタンプ検証そのものは test_alexa_verifier.py でカバー済みのため、
ここでは「検証結果に応じて正しくディスパッチ/エラー応答するか」を確認する。
"""
import asyncio
import json
import os
import sys
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from core.alexa_verifier import AlexaVerificationError


def _launch_request_body(supports_apl: bool = True):
    interfaces = {"Alexa.Presentation.APL": {}} if supports_apl else {}
    return {
        "version": "1.0",
        "session": {
            "new": True, "sessionId": "amzn1.echo-api.session.x",
            "application": {"applicationId": "amzn1.ask.skill.test"},
            "user": {"userId": "amzn1.ask.account.x"},
        },
        "context": {
            "System": {
                "application": {"applicationId": "amzn1.ask.skill.test"},
                "user": {"userId": "amzn1.ask.account.x"},
                "device": {"deviceId": "dev1", "supportedInterfaces": interfaces},
                "apiEndpoint": "https://api.amazonalexa.com",
            }
        },
        "request": {
            "type": "LaunchRequest", "requestId": "amzn1.echo-api.request.x",
            "timestamp": "2026-08-29T00:00:00Z", "locale": "ja-JP",
        },
    }


def _seed_one_user():
    with common.get_db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, avatar, role)
            VALUES ('dad', 'パパ', 'warrior', 3, 40, 120, '🦸', 'role_adult')
        """)


HEADERS = {"Signature": "sig", "SignatureCertChainUrl": "https://s3.amazonaws.com/echo.api/cert.pem"}


def test_launch_request_returns_apl_directive(api_client):
    _seed_one_user()

    with patch("routers.alexa_router.verify_signature", return_value=None), \
         patch("routers.alexa_router.verify_timestamp", return_value=None):
        res = api_client.post(
            "/webhook/alexa",
            content=json.dumps(_launch_request_body(supports_apl=True)).encode("utf-8"),
            headers=HEADERS,
        )

    assert res.status_code == 200
    body = res.json()
    assert body["response"]["directives"][0]["type"] == "Alexa.Presentation.APL.RenderDocument"
    assert "ファミリークエストを開きます" in body["response"]["outputSpeech"]["ssml"]


def test_launch_request_without_apl_support_speaks_only(api_client):
    _seed_one_user()

    with patch("routers.alexa_router.verify_signature", return_value=None), \
         patch("routers.alexa_router.verify_timestamp", return_value=None):
        res = api_client.post(
            "/webhook/alexa",
            content=json.dumps(_launch_request_body(supports_apl=False)).encode("utf-8"),
            headers=HEADERS,
        )

    assert res.status_code == 200
    body = res.json()
    assert "directives" not in body["response"]


def test_rejects_when_signature_verification_fails(api_client):
    with patch("routers.alexa_router.verify_signature", side_effect=AlexaVerificationError("bad sig")):
        res = api_client.post(
            "/webhook/alexa",
            content=json.dumps(_launch_request_body()).encode("utf-8"),
            headers=HEADERS,
        )

    assert res.status_code == 400


def test_rejects_when_timestamp_verification_fails(api_client):
    with patch("routers.alexa_router.verify_signature", return_value=None), \
         patch("routers.alexa_router.verify_timestamp", side_effect=AlexaVerificationError("stale")):
        res = api_client.post(
            "/webhook/alexa",
            content=json.dumps(_launch_request_body()).encode("utf-8"),
            headers=HEADERS,
        )

    assert res.status_code == 400


def test_rejects_missing_signature_headers(api_client):
    res = api_client.post(
        "/webhook/alexa",
        content=json.dumps(_launch_request_body()).encode("utf-8"),
    )

    assert res.status_code == 400


def test_verify_signature_and_skill_invoke_are_offloaded_to_a_thread(api_client, monkeypatch):
    """Issue #230の回帰テスト: verify_signature(証明書キャッシュミス時に同期
    requests.get())とskill.invoke(quest_serviceのSQLite同期アクセスを含む)は、
    unified_serverが単一プロセス・単一イベントループ構成であるにもかかわらず
    直接awaitされていなかったため、同時間帯のSwitchBot/LINE Webhook・
    Family Quest APIの処理をブロックしうる不具合があった。LINE経路
    (webhook_router.py)と同様にasyncio.to_threadでスレッドへ退避されている
    ことを確認する。"""
    _seed_one_user()

    real_to_thread = asyncio.to_thread
    offloaded_funcs = []

    async def _spy_to_thread(func, *args, **kwargs):
        offloaded_funcs.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("routers.alexa_router.asyncio.to_thread", _spy_to_thread)

    with patch("routers.alexa_router.verify_signature", return_value=None) as mock_verify, \
         patch("routers.alexa_router.verify_timestamp", return_value=None):
        res = api_client.post(
            "/webhook/alexa",
            content=json.dumps(_launch_request_body()).encode("utf-8"),
            headers=HEADERS,
        )

    assert res.status_code == 200
    assert mock_verify in offloaded_funcs, (
        "verify_signature がスレッドへ退避(asyncio.to_thread)されていない"
    )
    from handlers.alexa_handler import skill
    assert skill.invoke in offloaded_funcs, (
        "skill.invoke がスレッドへ退避(asyncio.to_thread)されていない"
    )
