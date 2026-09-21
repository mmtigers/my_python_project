# MY_HOME_SYSTEM/tests/test_dashboard_proxy.py
"""
Streamlitダッシュボードのリバースプロキシ(services/dashboard_proxy_service.py・
routers/dashboard_router.py)のテスト。

ダッシュボードは認証機構を持たないため 127.0.0.1:8501 束縛のままにし、
スマートフォンからは Cloudflare Access の保護下にある unified_server.py(8000番)の
`config.DASHBOARD_BASE_PATH` 配下経由でのみ到達させる、という構成の要点を固定する:

- HTTP・WebSocketの両方を中継すること(WebSocketが欠けると画面は "Connecting..." のまま)
- ブラウザのOriginをそのまま渡さないこと(StreamlitのTornadoがOrigin/Host不一致で
  ハンドシェイクを拒否するため。`--server.enableCORS false` のような
  Streamlit側の保護の無効化で回避しない)
- 中継先が落ちていても 8000番 側を巻き込んで 500 にしないこと
"""
import asyncio
import gzip
import io
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import websockets
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from routers import dashboard_router
from services.dashboard_proxy_service import DashboardProxyService, dashboard_proxy_service


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestUpstreamUrl:
    """公開パス配下のURLが、そのままStreamlit側の同じパスへ写ること。

    Streamlitは `--server.baseUrlPath` 付きで起動するため、ベースパスは剥がさない。
    剥がしてしまうと静的アセットが404になり画面が真っ白になる。
    """

    def test_http_path_and_query_are_preserved(self, monkeypatch):
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", "http://127.0.0.1:8501")
        service = DashboardProxyService()

        url = service._upstream_url("/dashboard/_stcore/health", b"a=1&b=2", scheme="http")

        assert url == "http://127.0.0.1:8501/dashboard/_stcore/health?a=1&b=2"

    def test_websocket_scheme_is_converted(self, monkeypatch):
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", "http://127.0.0.1:8501")
        service = DashboardProxyService()

        url = service._upstream_url("/dashboard/_stcore/stream", b"", scheme="ws")

        assert url == "ws://127.0.0.1:8501/dashboard/_stcore/stream"


class TestUpstreamHeaders:
    def test_hop_by_hop_and_host_headers_are_dropped(self, monkeypatch):
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", "http://127.0.0.1:8501")
        service = DashboardProxyService()

        headers = service._upstream_headers(
            [
                ("host", "example.com"),
                ("connection", "keep-alive"),
                ("upgrade", "websocket"),
                ("transfer-encoding", "chunked"),
                ("cookie", "_xsrf=abc"),
            ],
            client_host="192.168.1.50",
            forwarded_proto="https",
        )

        for dropped in ("host", "connection", "upgrade", "transfer-encoding"):
            assert dropped not in headers
        # Streamlit の XSRF 用クッキー等、通すべきヘッダーは残ること
        assert headers["cookie"] == "_xsrf=abc"

    def test_websocket_handshake_headers_are_dropped(self, monkeypatch):
        """`websockets` クライアントが自前で生成するため、転送すると重複して失敗する。"""
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", "http://127.0.0.1:8501")
        service = DashboardProxyService()

        headers = service._upstream_headers(
            [
                ("sec-websocket-key", "dGhlIHNhbXBsZSBub25jZQ=="),
                ("sec-websocket-version", "13"),
                ("sec-websocket-extensions", "permessage-deflate"),
                ("sec-websocket-protocol", "streamlit"),
            ],
            client_host=None,
            forwarded_proto="wss",
            drop_handshake_headers=True,
        )

        assert headers == {"x-forwarded-proto": "wss"}

    def test_origin_is_rewritten_to_upstream(self, monkeypatch):
        """Origin を書き換えないと Streamlit(Tornado) がWebSocketを拒否し、
        画面が "Connecting..." のまま進まなくなる。"""
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", "http://127.0.0.1:8501")
        service = DashboardProxyService()

        headers = service._upstream_headers(
            [("origin", "https://example.com")],
            client_host=None,
            forwarded_proto="https",
        )

        assert headers["origin"] == "http://127.0.0.1:8501"

    def test_origin_is_not_added_when_absent(self, monkeypatch):
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", "http://127.0.0.1:8501")
        service = DashboardProxyService()

        headers = service._upstream_headers([], client_host=None, forwarded_proto="http")

        assert "origin" not in headers

    def test_client_ip_is_appended_to_x_forwarded_for(self, monkeypatch):
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", "http://127.0.0.1:8501")
        service = DashboardProxyService()

        headers = service._upstream_headers(
            [("x-forwarded-for", "203.0.113.1")],
            client_host="127.0.0.1",
            forwarded_proto="https",
        )

        assert headers["x-forwarded-for"] == "203.0.113.1, 127.0.0.1"


class TestRouterRegistration:
    def test_routes_are_registered_under_the_configured_base_path(self):
        paths = {getattr(route, "path", None) for route in dashboard_router.router.routes}

        assert config.DASHBOARD_BASE_PATH in paths
        assert f"{config.DASHBOARD_BASE_PATH}/{{path:path}}" in paths

    def test_routes_are_hidden_from_the_openapi_schema(self):
        """`tests/test_unified_server_app.py` の外部Webhook整合テストは
        OpenAPIスキーマ上のパス一覧を使うため、中継のワイルドカードを
        スキーマに出すとその検査を濁す。"""
        for route in dashboard_router.router.routes:
            if hasattr(route, "include_in_schema"):
                assert route.include_in_schema is False


class TestUpstreamUnavailable:
    def test_returns_503_instead_of_500_when_streamlit_is_down(self, monkeypatch):
        """home_dashboard.service が停止中でも、8000番側を500にしない
        (グローバル例外ハンドラに落ちると原因が分からないエラー画面になる)。"""
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", f"http://127.0.0.1:{_free_port()}")

        app = FastAPI()
        app.include_router(dashboard_router.router)

        with TestClient(app) as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/_stcore/health")

        assert res.status_code == 503
        assert "home_dashboard.service" in res.text


class _RecordingHttpUpstream:
    """中継先のStreamlitに見立てたHTTPサーバー。

    受け取ったリクエストヘッダーを記録し、`Accept-Encoding` に gzip が含まれていれば
    gzip 圧縮した本文を `Content-Encoding: gzip` 付きで返す(Tornado/Streamlit と同じ挙動)。
    ついでに `Date` / `Server` ヘッダーも返し、中継側で二重にならないことを確認する。
    """

    BODY = b"<html>dashboard</html>"

    def __init__(self, body: bytes | None = None, content_type: str = "text/html") -> None:
        self.body = self.BODY if body is None else body
        self.content_type = content_type
        self.port = _free_port()
        self.received_headers: dict = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_RecordingHttpUpstream":
        upstream = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler の規約)
                upstream.received_headers = {k.lower(): v for k, v in self.headers.items()}
                accept_encoding = upstream.received_headers.get("accept-encoding", "")
                if "gzip" in accept_encoding:
                    body = gzip.compress(upstream.body)
                    encoding = "gzip"
                else:
                    body = upstream.body
                    encoding = None

                self.send_response(200)
                self.send_header("Content-Type", upstream.content_type)
                self.send_header("Content-Length", str(len(body)))
                if encoding:
                    self.send_header("Content-Encoding", encoding)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


class TestResponsePassthrough:
    """実機(Streamlit + uvicorn)での疎通確認で見つかった2件の回帰テスト。"""

    def _get(self, monkeypatch, upstream, *, accept_encoding: str | None):
        """`accept_encoding=None` は「ヘッダーを一切送らないクライアント」を再現する。"""
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", f"http://127.0.0.1:{upstream.port}")
        app = FastAPI()
        app.include_router(dashboard_router.router)
        with TestClient(app) as client:
            # TestClient(httpx)は既定で accept-encoding を付けるため、明示的に取り除く
            client.headers.pop("accept-encoding", None)
            headers = {} if accept_encoding is None else {"accept-encoding": accept_encoding}
            return client.get(f"{config.DASHBOARD_BASE_PATH}/", headers=headers)

    def test_body_is_not_gzipped_when_the_client_did_not_ask_for_it(self, monkeypatch):
        """httpx は明示しないと既定で `Accept-Encoding: gzip, ...` を付けるため、
        圧縮を要求していないクライアントにも gzip 本文をそのまま流してしまい、
        画面がバイナリのまま表示される(curl や `Accept-Encoding` を送らない
        ヘルスチェックで実際に再現した)。"""
        with _RecordingHttpUpstream() as upstream:
            res = self._get(monkeypatch, upstream, accept_encoding=None)

        assert upstream.received_headers["accept-encoding"] == "identity"
        assert res.content == _RecordingHttpUpstream.BODY

    def test_client_accept_encoding_is_honored(self, monkeypatch):
        """ブラウザが gzip を要求した場合は、中継先の圧縮をそのまま通す。"""
        with _RecordingHttpUpstream() as upstream:
            res = self._get(monkeypatch, upstream, accept_encoding="gzip")

        assert upstream.received_headers["accept-encoding"] == "gzip"
        # httpx(TestClient)側が Content-Encoding に従って解凍できること
        assert res.content == _RecordingHttpUpstream.BODY

    def test_upstream_date_and_server_headers_are_not_carried_over(self, monkeypatch):
        """`date`/`server` は uvicorn が自前で付けるため、中継先の値を持ち越すと
        1レスポンスに2つずつ並ぶ(実機の `curl -D -` で `server: uvicorn` と
        `server: TornadoServer/...`、`date` 2つを確認した)。中継先の値は落とす。"""
        with _RecordingHttpUpstream() as upstream:
            res = self._get(monkeypatch, upstream, accept_encoding="gzip")

        # 中継先(http.server)は "BaseHTTP/..." を名乗る。これが出てこないこと。
        assert "BaseHTTP" not in res.headers.get("server", "")
        assert len(res.headers.get_list("date")) <= 1
        assert len(res.headers.get_list("server")) <= 1


class TestWebSocketShutdownIsQuiet:
    """切断時の後始末で例外を漏らさないこと。

    スマートフォンでは「タブを閉じる」「別アプリへ切り替える」「復帰時に
    自動再読込する」たびに切断が起きる。ここで例外を拾い漏らすと、そのたびに
    サーバーログへトレースバックが出る(実機相当の疎通確認で再現した)。
    """

    def _service_with_closed_client(self, close_exception):
        from unittest.mock import AsyncMock, MagicMock

        service = DashboardProxyService()
        client_ws = MagicMock()
        client_ws.accept = AsyncMock()
        client_ws.close = AsyncMock(side_effect=close_exception)
        client_ws.headers.items.return_value = []
        client_ws.client = None
        client_ws.url.scheme = "ws"
        client_ws.scope = {"query_string": b"", "subprotocols": []}
        return service, client_ws

    @pytest.mark.parametrize("close_exception", [
        RuntimeError("Cannot call 'send' once a close message has been sent."),
        WebSocketDisconnect(code=1006),
    ])
    def test_close_failures_after_disconnect_are_swallowed(self, close_exception, monkeypatch):
        import asyncio
        from unittest.mock import AsyncMock, patch

        service, client_ws = self._service_with_closed_client(close_exception)
        upstream = AsyncMock()
        upstream.subprotocol = None

        async def _run():
            with patch.object(websockets, "connect", AsyncMock(return_value=upstream)), \
                 patch.object(service, "_pump_until_either_side_closes", AsyncMock()):
                await service.forward_websocket(client_ws, f"{config.DASHBOARD_BASE_PATH}/_stcore/stream")

        # 例外が送出されなければ成功(送出されるとそのままサーバーログに出る)
        asyncio.run(_run())
        upstream.close.assert_awaited()


class TestMobileHomeScreenAssets:
    """スマートフォンのホーム画面に追加するための付帯物。

    Streamlit の静的ファイルは pip 管理で手を入れられないため、マニフェスト・
    アイコンはこのアプリが返し、HTMLへの参照は中継層で `</head>` の直前に
    差し込む。中継の総当たりルートより**前**に定義されていないと、
    これらのパスも Streamlit に中継されて404になる。
    """

    def _client(self, monkeypatch, port: int = 1):
        # port=1 は「中継先が居ない」状態。マニフェスト・アイコンは中継せずに
        # このアプリが返すため、中継先が落ちていても 200 で返るのが正しい。
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", f"http://127.0.0.1:{port}")
        app = FastAPI()
        app.include_router(dashboard_router.router)
        return TestClient(app)

    def test_manifest_is_served_by_this_app_not_proxied(self, monkeypatch):
        with self._client(monkeypatch) as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/app.webmanifest")

        assert res.status_code == 200
        manifest = res.json()
        assert manifest["display"] == "standalone"
        # ホーム画面から開いたときにダッシュボードだけがアプリとして開くこと
        assert manifest["scope"] == f"{config.DASHBOARD_BASE_PATH}/"
        assert manifest["start_url"].startswith(config.DASHBOARD_BASE_PATH)
        assert manifest["lang"] == "ja"

    def test_icons_are_png_of_the_requested_size(self, monkeypatch):
        from PIL import Image

        with self._client(monkeypatch) as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/icon-192.png")

        assert res.status_code == 200
        assert res.headers["content-type"] == "image/png"
        assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert Image.open(io.BytesIO(res.content)).size == (192, 192)

    def test_unknown_icon_size_is_404_not_proxied(self, monkeypatch):
        with self._client(monkeypatch) as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/icon-9999.png")

        assert res.status_code == 404

    def test_manifest_lists_every_icon_size_that_is_served(self, monkeypatch):
        """マニフェストが参照しているのに404になるサイズが無いこと。"""
        with self._client(monkeypatch) as client:
            manifest = client.get(f"{config.DASHBOARD_BASE_PATH}/app.webmanifest").json()
            for icon in manifest["icons"]:
                assert client.get(icon["src"]).status_code == 200


class TestMobileHeadInjection:
    """HTMLの `</head>` 直前への差し込み。"""

    def _get_html(self, monkeypatch, upstream):
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", f"http://127.0.0.1:{upstream.port}")
        app = FastAPI()
        app.include_router(dashboard_router.router)
        with TestClient(app) as client:
            return client.get(
                f"{config.DASHBOARD_BASE_PATH}/",
                headers={"accept": "text/html,application/xhtml+xml"},
            )

    def test_manifest_and_icon_are_injected(self, monkeypatch):
        html = b"<html><head><title>Streamlit</title></head><body></body></html>"
        with _RecordingHttpUpstream(body=html) as upstream:
            res = self._get_html(monkeypatch, upstream)

        body = res.text
        assert f'href="{config.DASHBOARD_BASE_PATH}/app.webmanifest"' in body
        assert 'rel="apple-touch-icon"' in body
        assert 'name="apple-mobile-web-app-capable"' in body
        # 差し込みで head が壊れていないこと
        assert body.count("</head>") == 1
        assert body.index("app.webmanifest") < body.index("</head>")

    def test_manifest_is_fetched_with_credentials(self, monkeypatch):
        """マニフェストの取得は既定で認証情報を送らない。このパスは
        Cloudflare Access の内側にあるため、`use-credentials` が無いと
        取得が弾かれてホーム画面追加が効かない。"""
        html = b"<html><head></head><body></body></html>"
        with _RecordingHttpUpstream(body=html) as upstream:
            res = self._get_html(monkeypatch, upstream)

        assert 'crossorigin="use-credentials"' in res.text

    def test_returning_from_the_background_reloads_the_page(self, monkeypatch):
        """スマホでアプリを切り替えるとWebSocketが切れ、戻っても
        "Connecting..." のままだったり古い値が出たままになる。"""
        html = b"<html><head></head><body></body></html>"
        with _RecordingHttpUpstream(body=html) as upstream:
            res = self._get_html(monkeypatch, upstream)

        assert "visibilitychange" in res.text
        assert "location.reload()" in res.text

    def test_html_requests_ask_for_uncompressed_bodies(self, monkeypatch):
        """差し込みのために本文を読む必要があるため、HTMLだけは非圧縮を要求する。"""
        html = b"<html><head></head><body></body></html>"
        with _RecordingHttpUpstream(body=html) as upstream:
            self._get_html(monkeypatch, upstream)

        assert upstream.received_headers["accept-encoding"] == "identity"

    def test_non_html_responses_are_passed_through_untouched(self, monkeypatch):
        """静的アセット(JS等)に差し込むとファイルが壊れる。"""
        script = b"console.log('streamlit');"
        with _RecordingHttpUpstream(body=script, content_type="application/javascript") as upstream:
            monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", f"http://127.0.0.1:{upstream.port}")
            app = FastAPI()
            app.include_router(dashboard_router.router)
            with TestClient(app) as client:
                res = client.get(f"{config.DASHBOARD_BASE_PATH}/static/js/main.js")

        assert res.content == script

    def test_missing_head_is_left_alone(self):
        """Streamlit側のテンプレートが変わって `</head>` が見つからなくても、
        画面が壊れるのではなく差し込みだけが行われないこと。"""
        from services import dashboard_proxy_service as proxy_module

        original = "<html><body>no head</body></html>"
        assert proxy_module.inject_mobile_head(original) == original


class TestPinAcceptEncoding:
    def test_identity_is_used_when_the_client_sent_none(self):
        assert DashboardProxyService._pin_accept_encoding({})["accept-encoding"] == "identity"

    def test_client_value_is_kept(self):
        pinned = DashboardProxyService._pin_accept_encoding({"accept-encoding": "br, gzip"})

        assert pinned["accept-encoding"] == "br, gzip"


class _UpstreamWebSocketEcho:
    """中継先のStreamlitに見立てた、受け取った文字列をそのまま返すWebSocketサーバー。

    TestClient(のポータルスレッド)とは別スレッド・別イベントループで動かす。
    """

    def __init__(self) -> None:
        self.port = _free_port()
        self.received_headers: dict = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    async def _handler(self, connection) -> None:
        self.received_headers = dict(connection.request.headers)
        async for message in connection:
            await connection.send(f"echo:{message}")

    def _run(self) -> None:
        async def _serve() -> None:
            async with websockets.serve(self._handler, "127.0.0.1", self.port):
                # Event はこのループ上で生成してから ready を立てる
                # (__exit__ が別スレッドから参照するため順序が重要)。
                self._stop = asyncio.Event()
                self._ready.set()
                await self._stop.wait()

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(_serve())
        finally:
            self._loop.close()

    def __enter__(self) -> "_UpstreamWebSocketEcho":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=10), "テスト用WebSocketサーバーが起動しなかった"
        return self

    def __exit__(self, *exc_info) -> None:
        # ループを止めるのではなく、serve の async with を正常に抜けさせて
        # サーバーを片付ける(強制停止だと未完了タスクが GC 時に例外を出す)。
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=5)


class TestWebSocketRelay:
    def test_messages_are_relayed_in_both_directions(self, monkeypatch):
        """HTTPだけ中継してWebSocketを忘れると、画面は表示されるがデータが
        永久に来ない(Streamlitは `_stcore/stream` で描画差分を送る)。"""
        with _UpstreamWebSocketEcho() as upstream:
            monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", f"http://127.0.0.1:{upstream.port}")

            app = FastAPI()
            app.include_router(dashboard_router.router)

            with TestClient(app) as client:
                with client.websocket_connect(
                    f"{config.DASHBOARD_BASE_PATH}/_stcore/stream",
                    headers={"origin": "https://example.com"},
                ) as ws:
                    ws.send_text("hello")
                    assert ws.receive_text() == "echo:hello"

            # ブラウザのOriginではなく、中継先から見て同一オリジンの値が渡ること
            assert upstream.received_headers["origin"] == f"http://127.0.0.1:{upstream.port}"

    def test_connection_is_closed_when_upstream_is_unreachable(self, monkeypatch):
        monkeypatch.setattr(config, "DASHBOARD_INTERNAL_URL", f"http://127.0.0.1:{_free_port()}")

        app = FastAPI()
        app.include_router(dashboard_router.router)

        from starlette.websockets import WebSocketDisconnect

        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"{config.DASHBOARD_BASE_PATH}/_stcore/stream") as ws:
                    ws.receive_text()


class TestClientLifecycle:
    async def test_aclose_is_idempotent(self):
        """lifespan終了時に二重に呼ばれても例外にならないこと。"""
        service = DashboardProxyService()
        await service.aclose()
        await service.aclose()

        assert service._client is None

    async def test_client_is_recreated_after_close(self, monkeypatch):
        monkeypatch.setattr(config, "DASHBOARD_PROXY_TIMEOUT_SEC", 5)
        service = DashboardProxyService()

        first = await service._get_client()
        await service.aclose()
        second = await service._get_client()

        assert isinstance(second, httpx.AsyncClient)
        assert second is not first
        await service.aclose()


def test_module_level_singleton_is_exposed():
    """CLAUDE.md の方針(DIコンテナを入れず、サービスはモジュールレベルの
    シングルトンを直接importする)に従っていること。"""
    assert isinstance(dashboard_proxy_service, DashboardProxyService)
