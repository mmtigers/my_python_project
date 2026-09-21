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

スマートフォン向けの付帯物(ホーム画面への追加・復帰時の再接続):
    Streamlit のHTMLは自前では PWA のマニフェストを持たないため、ホーム画面に
    追加してもブラウザのUIごと開き、アイコンも既定のスクリーンショットになる。
    Streamlit 側のテンプレートには手を入れられない(pip管理の静的ファイル)ので、
    この中継層で `</head>` の直前にマニフェスト・アイコン・復帰時の再読込
    スクリプトを差し込む。マニフェストとアイコンの実体も同じベースパス配下で
    このアプリが返す(`routers/dashboard_router.py`)。

    注意: ホーム画面から開いた(standalone)ときのCookieの扱いはブラウザ依存で、
    iOSではSafariのCookieと分離されることがある。その場合はホーム画面アイコンから
    開いた最初の1回だけ Cloudflare Access のログインが要る(想定内の挙動)。

実装上の注意:
    - StreamlitはHTTPだけでなく WebSocket(`_stcore/stream`)でブラウザと双方向通信
      するため、HTTPの中継だけでは画面が永久に "Connecting..." のままになる。
      HTTP・WebSocketの両方を中継する。
    - Streamlit側は `--server.baseUrlPath` を `config.DASHBOARD_BASE_PATH` と
      揃えて起動する必要がある(揃っていないと静的アセットのURLがずれて404になる)。
      起動引数は `deploy/systemd/home_dashboard.service` と `start_all.sh` にある。
"""
import asyncio
import io
import logging
from functools import lru_cache
from typing import Dict, Iterable, Optional, Tuple

import httpx
import websockets
from websockets import exceptions as ws_exceptions
from fastapi import Request, WebSocket
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
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
# `date`/`server` は uvicorn が自前で付けるため、持ち越すと二重になる。
_EXCLUDED_RESPONSE_HEADERS = _HOP_BY_HOP_HEADERS | {"content-length", "date", "server"}

_UPSTREAM_UNAVAILABLE_MESSAGE = (
    "ダッシュボード(Streamlit)に接続できませんでした。"
    "home_dashboard.service が起動しているか確認してください。"
)


# === スマートフォン向けの付帯物 ===

# ホーム画面に追加したときの表示名・配色。
DASHBOARD_APP_NAME = "My Home Dashboard"
DASHBOARD_APP_SHORT_NAME = "おうち"
DASHBOARD_THEME_COLOR = "#0d47a1"
DASHBOARD_BACKGROUND_COLOR = "#ffffff"

# PWAマニフェストとapple-touch-iconで参照するアイコンのサイズ(px)。
DASHBOARD_ICON_SIZES = (180, 192, 512)

# バックグラウンドに回っていた時間がこれを超えて戻ってきたら、画面を作り直す(秒)。
# スマートフォンでアプリを切り替えるとStreamlitのWebSocketは切断され、戻っても
# "Connecting..." のままだったり、切れる前の古い値が出たままになる。
# 表示データのキャッシュTTL(60秒)と揃えてあり、これを超えていれば
# どのみち表示は作り直しになる。タブは `?tab=` に保存されているので復元される。
MOBILE_RELOAD_AFTER_HIDDEN_SEC = 60


def build_dashboard_manifest() -> dict:
    """ホーム画面に追加するためのWebアプリマニフェストを組み立てる。

    `start_url` / `scope` を `config.DASHBOARD_BASE_PATH` 配下に閉じることで、
    ホーム画面から開いたときにダッシュボードだけがアプリとして開く
    (`/quest` のPWAとは別アイコン・別スコープになる)。
    """
    base = config.DASHBOARD_BASE_PATH
    return {
        "name": DASHBOARD_APP_NAME,
        "short_name": DASHBOARD_APP_SHORT_NAME,
        "lang": "ja",
        "start_url": f"{base}/",
        "scope": f"{base}/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": DASHBOARD_BACKGROUND_COLOR,
        "theme_color": DASHBOARD_THEME_COLOR,
        "icons": [
            {
                "src": f"{base}/icon-{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any",
            }
            for size in DASHBOARD_ICON_SIZES
        ],
    }


@lru_cache(maxsize=len(DASHBOARD_ICON_SIZES))
def render_dashboard_icon_png(size: int) -> bytes:
    """ホーム画面アイコンのPNGを生成する(サイズごとに1回だけ描画してキャッシュ)。

    絵文字やフォントに頼ると、実機のフォント事情で崩れたり豆腐になったりする。
    図形(屋根の三角・本体の四角・窓)だけで描く。
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), DASHBOARD_THEME_COLOR)
    draw = ImageDraw.Draw(image)
    unit = size / 16

    # 屋根
    draw.polygon(
        [(unit * 8, unit * 3), (unit * 14, unit * 8), (unit * 2, unit * 8)],
        fill="#ffffff",
    )
    # 本体
    draw.rectangle([unit * 4, unit * 8, unit * 12, unit * 13], fill="#ffffff")
    # 窓(本体をくり抜いて見せる)
    draw.rectangle([unit * 7, unit * 10, unit * 9, unit * 13], fill=DASHBOARD_THEME_COLOR)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def build_mobile_head_snippet() -> str:
    """`</head>` の直前に差し込むHTML断片を返す。

    - マニフェスト: ホーム画面に追加したときアドレスバー無しで開く(standalone)
    - `apple-touch-icon`: iOSはマニフェストのiconsを見ないため別途必要
    - 復帰時の再読込: スマートフォンでアプリを切り替えるとWebSocketが切れ、
      戻っても "Connecting..." のままだったり古い値が出たままになる

    マニフェストの取得は既定で認証情報を送らない(no-cors / credentials omit)。
    このパスはエッジのCloudflare Accessの内側にあるため、`use-credentials` を
    付けないと取得が弾かれてホーム画面追加が効かない。
    """
    base = config.DASHBOARD_BASE_PATH
    return (
        f'<link rel="manifest" href="{base}/app.webmanifest" crossorigin="use-credentials">'
        f'<link rel="apple-touch-icon" href="{base}/icon-180.png">'
        f'<meta name="apple-mobile-web-app-capable" content="yes">'
        f'<meta name="mobile-web-app-capable" content="yes">'
        f'<meta name="apple-mobile-web-app-status-bar-style" content="default">'
        f'<meta name="apple-mobile-web-app-title" content="{DASHBOARD_APP_SHORT_NAME}">'
        f'<meta name="theme-color" content="{DASHBOARD_THEME_COLOR}">'
        "<script>"
        "(function(){"
        "var hiddenAt=null;"
        "document.addEventListener('visibilitychange',function(){"
        "if(document.hidden){hiddenAt=Date.now();return;}"
        f"if(hiddenAt&&Date.now()-hiddenAt>{MOBILE_RELOAD_AFTER_HIDDEN_SEC * 1000}){{window.location.reload();}}"
        "hiddenAt=null;"
        "});"
        "})();"
        "</script>"
    )


def inject_mobile_head(html_text: str) -> str:
    """StreamlitのHTMLの `</head>` 直前にスマホ向けの指定を差し込む。

    `</head>` が見つからない(Streamlitのテンプレートが変わった等)ときは
    何もしない。差し込めなくてもダッシュボードは従来どおり動く。
    """
    if "</head>" not in html_text:
        logger.warning("ダッシュボードのHTMLに </head> が見つからず、スマホ向けの指定を差し込めませんでした")
        return html_text
    return html_text.replace("</head>", build_mobile_head_snippet() + "</head>", 1)


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

    @staticmethod
    def _pin_accept_encoding(headers: Dict[str, str], *, force_identity: bool = False) -> Dict[str, str]:
        """`accept-encoding` をブラウザが送ってきた値に固定する。

        httpx は明示しないと既定の `Accept-Encoding: gzip, deflate, ...` を付けるため、
        ブラウザが圧縮を要求していない場合でも中継先が gzip で返し、こちらはそれを
        そのまま流してしまう(=クライアントが解凍できずバイナリのまま表示される)。
        ヘルスチェックや `Accept-Encoding` を送らないクライアントで実際に壊れる。

        `force_identity=True`(HTMLを取りに行くリクエスト)のときは非圧縮を要求する。
        `</head>` への差し込み(`inject_mobile_head`)のために本文を読む必要があり、
        圧縮されていると展開してから詰め直すことになるため。
        """
        pinned = dict(headers)
        if force_identity:
            pinned["accept-encoding"] = "identity"
        else:
            pinned.setdefault("accept-encoding", "identity")
        return pinned

    @staticmethod
    def _wants_html(request: Request) -> bool:
        """ブラウザが画面(HTML)を取りに来たリクエストかどうか。"""
        return "text/html" in request.headers.get("accept", "")

    # --- HTTP ---

    async def forward_http(self, request: Request, path: str) -> StreamingResponse | PlainTextResponse:
        """HTTPリクエストをStreamlitへ中継し、レスポンスをストリームで返す。"""
        url = self._upstream_url(path, request.url.query.encode("latin-1"), scheme="http")
        headers = self._pin_accept_encoding(
            self._upstream_headers(
                request.headers.items(),
                client_host=request.client.host if request.client else None,
                forwarded_proto=request.url.scheme,
            ),
            force_identity=self._wants_html(request),
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

        # HTML(画面本体)だけは本文を読み切って、スマホ向けの指定を差し込む。
        # Streamlit の index.html は数KBで、ストリームのまま流す利点が無い。
        # 圧縮されて返ってきた場合(`force_identity` が効かない経路)は、展開して
        # 詰め直すより素通しするほうが安全なので何もしない。
        content_type = upstream_response.headers.get("content-type", "")
        content_encoding = upstream_response.headers.get("content-encoding", "")
        if content_type.startswith("text/html") and content_encoding in ("", "identity"):
            body = await upstream_response.aread()
            await upstream_response.aclose()
            injected = inject_mobile_head(body.decode("utf-8", errors="replace"))
            return Response(
                content=injected.encode("utf-8"),
                status_code=upstream_response.status_code,
                headers=response_headers,
            )

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
            except (RuntimeError, WebSocketDisconnect):
                # 既にブラウザ側から切断済みの場合、Starletteは送信を拒否する。
                # 例外の型は状況で変わり、送信拒否は RuntimeError、切断済みの
                # 通知は WebSocketDisconnect になる。中継の終了処理としては
                # どちらも正常で、握りつぶしてよい。
                #
                # WebSocketDisconnect を拾い漏らすと、タブを閉じる・スマホで
                # 別アプリへ切り替える・復帰時に自動再読込する(上記の
                # MOBILE_RELOAD_AFTER_HIDDEN_SEC)たびにサーバーログへ
                # トレースバックが出る。
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
