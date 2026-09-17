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
import os
import socket
import sys
import threading

import httpx
import pytest
import websockets
from fastapi import FastAPI
from starlette.testclient import TestClient

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
