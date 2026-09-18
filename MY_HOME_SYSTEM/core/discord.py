# MY_HOME_SYSTEM/core/discord.py
"""Discord Webhook への送信を1箇所に集約する低レベルユーティリティ(Issue #661)。

これまで Discord への POST は5系統に散っており、分割(2000字上限)・リトライ・
URL のマスクの有無が経路ごとにばらばらだった:

- `services/notification_service._send_discord_webhook`(分割・リトライ・マスクあり) → 移行済み
- `core/logger.DiscordErrorHandler._send_webhook`(単発 POST、リトライなし) → 移行済み
- `monitors/smart_timelapse_generator`(単発 POST、失敗を黙殺) → 移行済み
- `DDD/newface_monitor.DiscordNotifier`(embed ペイロード) → 移行済み
- `DDD/batch_download_discord._standalone_send_discord_webhook`
  → **意図的に未移行**。MY_HOME_SYSTEM が無い環境でも動くことが要件のフォールバックなので、
    このモジュールを import できない前提を残す必要がある。

ここには「1回の POST をどう投げるか」だけを置き、宛先の決定(チャンネル振り分け)や
LINE メッセージオブジェクトからのテキスト化といった上位の責務は
`services/notification_service` 側に残す。

送るものはテキスト(`content`)・添付(`files`)・埋め込み(`embeds`)の3種で、
呼び出し元の事情に応じて次の2つの受け口を用意してある(いずれも省略時は従来どおり):

- `session`: 呼び出し元が retry アダプタ付きの `requests.Session` を持っている場合に渡す。
  渡されたときはこのモジュール側のリトライを重ねない(二重リトライになるため)。
- `raise_for_status`: ステータスコードごとに分岐したい呼び出し元(例: `DDD/newface_monitor` の
  401/404 でのサーキットブレーカー開放)向けに、bool を返さず例外をそのまま伝播させる。

**このモジュールは `core.logger` を import しない。** `core.logger` 側がこれを使うため、
循環 import になるのに加え、Webhook 送信の失敗を同じロギング経路へ流すと
「エラーを通知しようとして失敗し、その失敗をまた通知しようとする」ループになりうる。
ログは標準ライブラリの `logging` を直接使い、ハンドラは呼び出し側の設定に委ねる。
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from typing import Any

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


def split_content(text: str, limit: int = CONTENT_CHUNK_SIZE) -> list[str]:
    """text を limit 文字以下のチャンクに分割する(できるだけ改行位置で切る)。空文字は1チャンク。"""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
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


def _rewind_files(files: dict | None) -> None:
    """添付ファイルの読み取り位置を先頭へ戻す(リトライ前に必ず呼ぶ)。

    `files` に渡すのは開いたままのファイルオブジェクトなので、1回目の POST で
    EOF まで読み切られている。巻き戻さずに再送すると**中身が空のまま**アップロード
    され、しかも Discord は 200/204 を返すため「成功したのに0バイトの動画が届く」
    という一番気づきにくい壊れ方をする(`monitors/smart_timelapse_generator` の
    動画アップロードがこの経路を通る)。seek できないストリームは黙って諦める。
    """
    for value in (files or {}).values():
        # requests の files は {"name": fileobj} と {"name": (filename, fileobj, type)} の両形式
        candidate = value[1] if isinstance(value, (tuple, list)) and len(value) >= 2 else value
        try:
            candidate.seek(0)
        except (AttributeError, OSError, ValueError):
            continue


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
        _rewind_files(kwargs.get("files"))
        res = requests.post(url, **kwargs)
    return res


def _build_payload(
    chunk: str,
    embeds: Sequence[dict] | None,
    username: str | None,
    *,
    first_chunk: bool,
) -> dict[str, Any]:
    """1回の POST に載せる JSON ボディを組み立てる。

    `content` キーは、テキストが空でも embed を伴わない限り常に入れる(embed 無しの
    従来の呼び出しが送っていたボディを1バイトも変えないため)。逆に embed だけを送る
    呼び出し(`DDD/newface_monitor` の個別キャスト通知)では `content` を入れない。
    embed は添付と同じく先頭チャンクにのみ付ける(分割時に同じ埋め込みが複数回届かないように)。
    """
    payload: dict[str, Any] = {}
    if chunk or embeds is None:
        payload["content"] = chunk
    if embeds is not None and first_chunk:
        payload["embeds"] = list(embeds)
    if username:
        payload["username"] = username
    return payload


def _post_once(url: str, session: Any | None, **kwargs):
    """1回分の POST。session が渡されていればそれを使い、こちら側のリトライは重ねない。

    session には呼び出し元が retry アダプタ(urllib3 の `Retry`)を載せている前提。
    ここでさらに `post_with_retry` を通すと二重リトライになり、429 のときの総待機時間が
    呼び出し元の設計値から外れてしまう。
    """
    if session is not None:
        return session.post(url, **kwargs)
    return post_with_retry(url, **kwargs)


def _send_chunks(
    url: str,
    content: str,
    files: dict | None,
    timeout: float,
    embeds: Sequence[dict] | None,
    username: str | None,
    session: Any | None,
    raise_for_status: bool,
) -> bool:
    for idx, chunk in enumerate(split_content(content)):
        first = idx == 0
        if files and first:
            data = {"content": chunk}
            if username:
                data["username"] = username
            res = _post_once(url, session, files=files, data=data, timeout=max(timeout, 60))
        else:
            res = _post_once(
                url,
                session,
                json=_build_payload(chunk, embeds, username, first_chunk=first),
                timeout=timeout,
            )
        if raise_for_status:
            # 4xx/5xx はここで requests.HTTPError になり、呼び出し元の分岐へ渡る。
            res.raise_for_status()
            continue
        if getattr(res, "status_code", None) not in SUCCESS_STATUS_CODES:
            logger.warning(
                "Discord API エラー: %s - %s",
                getattr(res, "status_code", "?"),
                redact_webhook_url(getattr(res, "text", "")),
            )
            return False
    return True


def post_webhook(
    url: str | None,
    content: str = "",
    files: dict | None = None,
    timeout: float = 10,
    *,
    embeds: Sequence[dict] | None = None,
    username: str | None = None,
    session: Any | None = None,
    raise_for_status: bool = False,
) -> bool:
    """1つの Webhook URL へ content(必要なら添付・embed つき)を送る。成功なら True。

    content が上限を超える場合は分割し、添付と embed は先頭チャンクにのみ付ける。
    URL 未設定なら何もせず False。例外・非成功ステータスは warning ログにして False を返す
    (通知の失敗で呼び出し元の本処理を止めない)。

    Args:
        embeds: Discord webhook API の `embeds` 配列。省略時はテキストのみ。
        username: webhook の表示名の上書き。省略時は webhook 既定の名前。
        session: 送信に使う `requests.Session`(retry アダプタ付きを想定)。
            渡した場合、このモジュール側のリトライは行わない(_post_once 参照)。
        raise_for_status: True にすると bool ではなく例外で失敗を伝える。
            ステータスコードごとに分岐したい呼び出し元専用で、`requests` の例外は
            握り潰さずそのまま伝播する(成功時は True を返す)。
    """
    if files and embeds:
        # #695レビュー指摘: files 送信は multipart になり、_send_chunks の
        # files分岐が組む data には embeds を含めない(Discordでembedを
        # multipartに乗せるには本来 payload_json フィールドが要るため、
        # 単純に data へ足しても正しく送れない)。呼び出し元の設定ミスとして
        # 無言でembedを握り潰す(#695で発見)のではなく、ここで即座に失敗させる。
        raise ValueError("post_webhook: files と embeds は同時に指定できません")
    if not url:
        return False
    args = (url, content, files, timeout, embeds, username, session, raise_for_status)
    if raise_for_status:
        return _send_chunks(*args)
    try:
        return _send_chunks(*args)
    except Exception as e:
        logger.warning("Discord送信失敗: %s: %s", type(e).__name__, redact_webhook_url(e))
        return False
