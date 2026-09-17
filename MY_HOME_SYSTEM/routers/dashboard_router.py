# MY_HOME_SYSTEM/routers/dashboard_router.py
"""
Streamlitダッシュボードを `config.DASHBOARD_BASE_PATH` 配下で配信するルーター。

スマートフォンからダッシュボードを見られるようにするための入口で、実際の中継処理は
`services/dashboard_proxy_service.py` に委譲する(CLAUDE.md「ルーターは薄く」の方針)。
経緯・セキュリティ上の前提は同サービスのモジュールdocstringを参照。

`config.DASHBOARD_PROXY_ENABLED=false` のときは `unified_server.py` がこのルーターを
include しないため、パス自体が存在しなくなる(404)。
"""
import logging

from fastapi import APIRouter, Request, WebSocket

import config
from services.dashboard_proxy_service import dashboard_proxy_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Streamlitが扱うHTTPメソッド。ダッシュボードはGETが大半だが、
# `_stcore/upload_file` 等のためにPOST系も通す。
_PROXIED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

_BASE_PATH = config.DASHBOARD_BASE_PATH


@router.api_route(_BASE_PATH, methods=_PROXIED_METHODS, include_in_schema=False)
async def proxy_dashboard_root(request: Request):
    """ベースパスそのもの(例: `/dashboard`)へのアクセスを中継する。

    Streamlitは末尾スラッシュ付きへリダイレクトを返すが、中継先も同じベースパスで
    動いているため `Location` はそのままブラウザに返して問題ない。
    """
    return await dashboard_proxy_service.forward_http(request, _BASE_PATH)


@router.api_route(f"{_BASE_PATH}/{{path:path}}", methods=_PROXIED_METHODS, include_in_schema=False)
async def proxy_dashboard(request: Request, path: str):
    """ベースパス配下の静的アセット・API(`_stcore/*` 等)を中継する。"""
    return await dashboard_proxy_service.forward_http(request, f"{_BASE_PATH}/{path}")


@router.websocket(f"{_BASE_PATH}/{{path:path}}")
async def proxy_dashboard_websocket(websocket: WebSocket, path: str):
    """StreamlitのWebSocket(`_stcore/stream`)を中継する。

    これが無いとHTMLと静的アセットだけが配信され、画面は "Connecting..." のまま
    永久にデータが表示されない。
    """
    await dashboard_proxy_service.forward_websocket(websocket, f"{_BASE_PATH}/{path}")
