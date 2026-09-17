# MY_HOME_SYSTEM/services/dashboard_proxy_service.py
"""
Streamlitダッシュボード(dashboard.py)を `unified_server.py` 経由で配信するための
リバースプロキシ処理。

背景:
    ダッシュボードは認証機構を持たず、家族の健康記録・防犯ログの閲覧と
    `sudo systemctl restart` ボタンを備えるため、`home_dashboard.service` /
    `start_all.sh` は 127.0.0.1:8501 にのみバインドしている
    (`docs/reports/CODE_REVIEW_REPORT_ALL.md` の Critical 指摘への対応)。
    このままではスマートフォンから一切到達できないため、既にエッジの
    Cloudflare Access で保護されている `unified_server.py`(8000番)の
    `config.DASHBOARD_BASE_PATH` 配下へ中継する。8501番は localhost 束縛のまま
    変えないので、「LANに入れれば無認証で見えてしまう」状態には戻らない。

    アプリ層で `Cf-Access-Jwt-Assertion` を検証しない設計(Issue #321・2026-09-03決定)は
    他のパスと共通で、このパスも同じ前提(オリジンへの直接到達がCloudflareの
    IPレンジ経由に限定されていること)に依存する。**Cloudflare Access側で
    このパスをバイパス対象にしてはならない**。

実装上の注意:
    - StreamlitはHTTPだけでなく WebSocket(`_stcore/stream`)でブラウザと双方向通信
      するため、HTTPの中継だけでは画面が永久に "Connecting..." のままになる。
      HTTP・WebSocketの両方を中継する。
    - Streamlit側は `--server.baseUrlPath` を `config.DASHBOARD_BASE_PATH` と
      揃えて起動する必要がある(揃っていないと静的アセットのURLがずれて404になる)。
      起動引数は `deploy/systemd/home_dashboard.service` と `start_all.sh` にある。
"""
import asyncio
import logging
from typing import Dict, Iterable, Optional, Tuple

import httpx
import websockets
from websockets import exceptions as ws_exceptions
from fastapi import Request, WebSocket
from fastapi.responses import PlainTextResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect

import config

logger = logging.getLogger(__name__)

# RFC 7230 のホップバイホップヘッダー。中継先へそのまま渡すと接続の意味が壊れるため落とす。
# `host` は中継先のホストへ差し替えるので同様に除外する。
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)

# WebSocketのハンドシェイク固有ヘッダー。`websockets` クライアントが自前で生成するため、
# ブラウザから来た値をそのまま転送すると重複して接続が失敗する。
_WEBSOCKET_HANDSHAKE_HEADERS = frozenset(
    {
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
    }
)

# レスポンス側で落とすヘッダー。ボディは `aiter_raw()` で無加工のまま流すが、
# 長さ・チャンク制御は Starlette 側が改めて決めるため中継先の値を持ち越さない。
_EXCLUDED_RESPONSE_HEADERS = _HOP_BY_HOP_HEADERS | {"content-length"}

_UPSTREAM_UNAVAILABLE_MESSAGE = (
    "ダッシュボード(Streamlit)に接続できませんでした。"
    "home_dashboard.service が起動しているか確認してください。"
)


class DashboardProxyService:
    """Streamlitダッシュボードへのリバースプロキシ。

    `config` をモジュールレベルで直接参照する既存の方針(DIコンテナは導入しない。
    CLAUDE.md「依存性注入(DI)について」)に合わせ、設定は都度 `config` から読む。
    """

    def __init__(self) -> None:
        # httpx.AsyncClient はイベントループに紐づくため、モジュールのimport時ではなく
        # 最初のリクエスト時に生成する(テスト時に不要な接続プールを作らないためでもある)。
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    # --- ライフサイクル ---

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            async with self._client_lock:
                if self._client is None or self._client.is_closed:
                    self._client = httpx.AsyncClient(
                        timeout=httpx.Timeout(config.DASHBOARD_PROXY_TIMEOUT_SEC),
                        # 中継先(Streamlit)が返すリダイレクト(例: /dashboard →
                        # /dashboard/)は追わずにブラウザへそのまま返す。Streamlit側も
                        # 同じベースパスで動くため、Location の書き換えは不要。
                        follow_redirects=False,
                    )
        return self._client

    async def aclose(self) -> None:
        """`unified_server.py` の lifespan 終了時に呼ぶ後始末。"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # --- URL / ヘッダーの組み立て ---

    def _upstream_url(self, path: str, query_string: bytes, *, scheme: str) -> str:
        """公開パス配下の `path` を、Streamlit側の同じパスへ写像したURLを返す。

        Streamlitは `--server.baseUrlPath` 付きで起動するため、ベースパスを
        剥がさずそのまま中継先にも渡す(`/dashboard/_stcore/stream` →
        `http://127.0.0.1:8501/dashboard/_stcore/stream`)。
        """
        base = config.DASHBOARD_INTERNAL_URL
        if scheme == "ws":
            base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)

        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{base}{normalized_path}"
        if query_string:
            url = f"{url}?{query_string.decode('latin-1')}"
        return url

    def _upstream_headers(
        self,
        headers: Iterable[Tuple[str, str]],
        *,
        client_host: Optional[str],
        forwarded_proto: str,
        drop_handshake_headers: bool = False,
    ) -> Dict[str, str]:
        """中継先へ渡すヘッダーを組み立てる(ホップバイホップは除去)。"""
        excluded = set(_HOP_BY_HOP_HEADERS)
        if drop_handshake_headers:
            excluded |= _WEBSOCKET_HANDSHAKE_HEADERS

        forwarded: Dict[str, str] = {
            name: value for name, value in headers if name.lower() not in excluded
        }

        # 中継先(Streamlit)から見た接続元は常に127.0.0.1になるため、
        # 本来のクライアント情報を標準的なヘッダーで引き継ぐ。
        if client_host:
            existing = forwarded.get("x-forwarded-for")
            forwarded["x-forwarded-for"] = f"{existing}, {client_host}" if existing else client_host
        forwarded["x-forwarded-proto"] = forwarded_proto

        # Streamlit(Tornado)のWebSocketハンドラは Origin と Host の一致を検証するため、
        # ブラウザが送る本来のOrigin(例: https://<公開ドメイン>)をそのまま渡すと
        # ハンドシェイクが拒否され、画面が "Connecting..." のまま進まない。
        # 中継先から見て同一オリジンになるよう書き換える。
        # (`--server.enableCORS false` / `--server.enableXsrfProtection false` で
        #  検証そのものを無効化する回避策を取らずに済ませるための処理)
        if "origin" in forwarded:
            forwarded["origin"] = config.DASHBOARD_INTERNAL_URL
        return forwarded

    # --- HTTP ---

    async def forward_http(self, request: Request, path: str) -> StreamingResponse | PlainTextResponse:
        """HTTPリクエストをStreamlitへ中継し、レスポンスをストリームで返す。"""
        url = self._upstream_url(path, request.url.query.encode("latin-1"), scheme="http")
        headers = self._upstream_headers(
            request.headers.items(),
            client_host=request.client.host if request.client else None,
            forwarded_proto=request.url.scheme,
        )

        client = await self._get_client()
        upstream_request = client.build_request(
            request.method,
            url,
            headers=headers,
            content=await request.body(),
        )

        try:
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as e:
            # Streamlitが落ちている/起動途中でも、8000番側まで500にしない。
            logger.warning(f"ダッシュボードへの中継に失敗しました ({url}): {e}")
            return PlainTextResponse(_UPSTREAM_UNAVAILABLE_MESSAGE, status_code=503)

        response_headers = {
            name: value
            for name, value in upstream_response.headers.items()
            if name.lower() not in _EXCLUDED_RESPONSE_HEADERS
        }

        return StreamingResponse(
            upstream_response.aiter_raw(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            # ストリームを閉じ忘れると接続プールを食いつぶすため、送出完了後に必ず閉じる。
            background=BackgroundTask(upstream_response.aclose),
        )

    # --- WebSocket ---

    async def forward_websocket(self, client_ws: WebSocket, path: str) -> None:
        """StreamlitのWebSocket(`_stcore/stream`)を双方向に中継する。"""
        url = self._upstream_url(path, client_ws.scope.get("query_string", b""), scheme="ws")
        requested_subprotocols = list(client_ws.scope.get("subprotocols") or [])
        headers = self._upstream_headers(
            client_ws.headers.items(),
            client_host=client_ws.client.host if client_ws.client else None,
            forwarded_proto="wss" if client_ws.url.scheme == "wss" else "ws",
            drop_handshake_headers=True,
        )

        try:
            upstream = await websockets.connect(
                url,
                subprotocols=requested_subprotocols or None,
                additional_headers=headers,
                # Streamlitのメッセージ(デルタ・画像等)はデフォルト上限(1MiB)を超えうる。
                max_size=None,
                open_timeout=config.DASHBOARD_PROXY_TIMEOUT_SEC,
                # 死活監視はブラウザ⇔Streamlit間のアプリケーションレベルで行われるため、
                # 中継区間で独自にpingを打って切断判定を二重化しない。
                ping_interval=None,
            )
        except Exception as e:
            # accept前に閉じることで、ブラウザ側には接続失敗として伝わる。
            logger.warning(f"ダッシュボードのWebSocket中継に失敗しました ({url}): {e}")
            await client_ws.close(code=1011)
            return

        try:
            await client_ws.accept(subprotocol=upstream.subprotocol)
            await self._pump_until_either_side_closes(client_ws, upstream)
        finally:
            await upstream.close()
            try:
                await client_ws.close()
            except RuntimeError:
                # 既にブラウザ側から切断済みの場合、Starletteは送信を拒否して
                # RuntimeErrorを送出する。中継の終了処理としては正常。
                pass

    async def _pump_until_either_side_closes(self, client_ws: WebSocket, upstream) -> None:
        """双方向にメッセージを流し、どちらかが閉じたら両方の転送を止める。"""
        tasks = [
            asyncio.create_task(self._client_to_upstream(client_ws, upstream)),
            asyncio.create_task(self._upstream_to_client(client_ws, upstream)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _client_to_upstream(self, client_ws: WebSocket, upstream) -> None:
        try:
            while True:
                message = await client_ws.receive()
                if message["type"] == "websocket.disconnect":
                    return
                text = message.get("text")
                if text is not None:
                    await upstream.send(text)
                    continue
                data = message.get("bytes")
                if data is not None:
                    await upstream.send(data)
        except (WebSocketDisconnect, ws_exceptions.ConnectionClosed):
            return

    async def _upstream_to_client(self, client_ws: WebSocket, upstream) -> None:
        try:
            async for message in upstream:
                if isinstance(message, str):
                    await client_ws.send_text(message)
                else:
                    await client_ws.send_bytes(message)
        except (WebSocketDisconnect, ws_exceptions.ConnectionClosed, RuntimeError):
            return


# CLAUDE.md の方針どおり、サービスはモジュールレベルのシングルトンとして公開する。
dashboard_proxy_service = DashboardProxyService()
