# MY_HOME_SYSTEM/core/discord.py
"""Discord Webhook への送信を1箇所に集約する低レベルユーティリティ(Issue #661)。

これまで Discord への POST は5系統に散っており、分割(2000字上限)・リトライ・
URL のマスクの有無が経路ごとにばらばらだった:

- `services/notification_service._send_discord_webhook`(分割・リトライ・マスクあり)
- `core/logger.DiscordErrorHandler._send_webhook`(単発 POST、リトライなし)
- `monitors/smart_timelapse_generator`(単発 POST、失敗を黙殺)
- `DDD/batch_download_discord._standalone_send_discord_webhook`
- `DDD/newface_monitor.DiscordNotifier`

ここには「1回の POST をどう投げるか」だけを置き、宛先の決定(チャンネル振り分け)や
LINE メッセージオブジェクトからのテキスト化といった上位の責務は
`services/notification_service` 側に残す。

**このモジュールは `core.logger` を import しない。** `core.logger` 側がこれを使うため、
循環 import になるのに加え、Webhook 送信の失敗を同じロギング経路へ流すと
「エラーを通知しようとして失敗し、その失敗をまた通知しようとする」ループになりうる。
ログは標準ライブラリの `logging` を直接使い、ハンドラは呼び出し側の設定に委ねる。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, List, Optional

import requests

logger = logging.getLogger("core.discord")

# Discord の content 上限(2000)に対する安全側のチャンクサイズ
CONTENT_CHUNK_SIZE = 1900
# 429/5xx 時のリトライ回数(初回を除く)と、Retry-After が無い/異常な場合の上限待機秒数
RETRY_ATTEMPTS = 1
RETRY_MAX_WAIT_SECONDS = 5.0
# 成功とみなすステータスコード(Discord は 204 No Content を返すことがある)
SUCCESS_STATUS_CODES = (200, 204)

# テストから差し替えられるようにモジュール属性にしておく
_retry_sleep = time.sleep

# https://discord.com/api/webhooks/<id>/<token> の token 部分。
# 表記は既存の core/logger・DDD/file_utils の実装に合わせる(ログの見た目を変えないため)。
_WEBHOOK_URL_RE = re.compile(r"(/api/webhooks/\d+)/[A-Za-z0-9_\-]+")


def redact_webhook_url(value: Any) -> str:
    """Discord Webhook URL のトークン部分をマスクした文字列を返す(ログ・例外メッセージ用)。"""
    return _WEBHOOK_URL_RE.sub(r"\1/<redacted>", str(value))


def split_content(text: str, limit: int = CONTENT_CHUNK_SIZE) -> List[str]:
    """text を limit 文字以下のチャンクに分割する(できるだけ改行位置で切る)。空文字は1チャンク。"""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        chunks.append(rest)
    return chunks


def post_with_retry(url: str, **kwargs):
    """`requests.post` を呼び、429/5xx なら Retry-After(または短い固定待機)の後に限定回数リトライする。

    戻り値はレスポンスオブジェクトそのもの(呼び出し側がステータスを見る)。
    例外はそのまま伝播させる。
    """
    res = requests.post(url, **kwargs)
    for _ in range(RETRY_ATTEMPTS):
        status = getattr(res, "status_code", None)
        if status != 429 and not (isinstance(status, int) and status >= 500):
            break
        wait = 1.0
        headers = getattr(res, "headers", None) or {}
        retry_after = headers.get("Retry-After") or headers.get("X-RateLimit-Reset-After")
        try:
            if retry_after is not None:
                wait = float(retry_after)
        except (TypeError, ValueError):
            wait = 1.0
        wait = max(0.0, min(wait, RETRY_MAX_WAIT_SECONDS))
        logger.warning("Discord API %s — %.1fs 後にリトライします", status, wait)
        _retry_sleep(wait)
        res = requests.post(url, **kwargs)
    return res


def post_webhook(
    url: Optional[str],
    content: str = "",
    files: Optional[dict] = None,
    timeout: float = 10,
) -> bool:
    """1つの Webhook URL へ content(必要なら添付つき)を送る。成功なら True。

    content が上限を超える場合は分割し、添付は先頭チャンクにのみ付ける。URL 未設定なら
    何もせず False。例外・非成功ステータスは warning ログにして False を返す
    (通知の失敗で呼び出し元の本処理を止めない)。
    """
    if not url:
        return False
    try:
        for idx, chunk in enumerate(split_content(content)):
            if files and idx == 0:
                res = post_with_retry(url, files=files, data={"content": chunk}, timeout=max(timeout, 60))
            else:
                res = post_with_retry(url, json={"content": chunk}, timeout=timeout)
            if getattr(res, "status_code", None) not in SUCCESS_STATUS_CODES:
                logger.warning(
                    "Discord API エラー: %s - %s",
                    getattr(res, "status_code", "?"),
                    redact_webhook_url(getattr(res, "text", "")),
                )
                return False
        return True
    except Exception as e:
        logger.warning("Discord送信失敗: %s: %s", type(e).__name__, redact_webhook_url(e))
        return False
