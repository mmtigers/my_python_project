# MY_HOME_SYSTEM/routers/dashboard_router.py
"""
Streamlitダッシュボードを `config.DASHBOARD_BASE_PATH` 配下で配信するルーター。

スマートフォンからダッシュボードを見られるようにするための入口で、実際の中継処理は
`services/dashboard_proxy_service.py` に委譲する(CLAUDE.md「ルーターは薄く」の方針)。
経緯・セキュリティ上の前提は同サービスのモジュールdocstringを参照。

`config.DASHBOARD_PROXY_ENABLED=false` のときは `unified_server.py` がこのルーターを
include しないため、パス自体が存在しなくなる(404)。

ベースパス配下には、中継のほかに次のものも置く(いずれも中継先のStreamlitは
持っていないため、このアプリが直接返す):

- スマートフォンのホーム画面に追加するためのマニフェストとアイコン
- 軽量ページ `{DASHBOARD_BASE_PATH}/m`(Streamlitを介さない読み取り専用のサマリー)
- その自動更新用に、カードのブロックだけを返す `{DASHBOARD_BASE_PATH}/m/status`
"""
import json

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, Response

import config
from services import home_status_service
from services.dashboard_proxy_service import (
    DASHBOARD_ICON_SIZES,
    build_dashboard_manifest,
    dashboard_proxy_service,
    render_dashboard_icon_png,
)

# 軽量ページ(Streamlitを介さない読み取り専用のサマリー)のパス。
_MOBILE_PATH = f"{config.DASHBOARD_BASE_PATH}/m"
# 軽量ページが自動更新で差し替える、カードのブロックだけを返すパス。
_MOBILE_STATUS_PATH = f"{_MOBILE_PATH}/status"
# ファミクエ(PWA)への導線。`unified_server.py` が `/quest` にSPAをマウントしている。
_QUEST_APP_PATH = "/quest"

router = APIRouter()

# Streamlitが扱うHTTPメソッド。ダッシュボードはGETが大半だが、
# `_stcore/upload_file` 等のためにPOST系も通す。
_PROXIED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

_BASE_PATH = config.DASHBOARD_BASE_PATH


# --- スマートフォンのホーム画面に追加するための付帯物 ---
# 中継先(Streamlit)は持っていないので、このアプリ自身が返す。
# 中継の総当たりルート(`{path:path}`)より**前**に定義すること
# (FastAPIは定義順に照合するため、後ろに置くと中継側が拾って404になる)。


@router.get(f"{_BASE_PATH}/app.webmanifest", include_in_schema=False)
def dashboard_manifest() -> Response:
    """ホーム画面に追加したときアドレスバー無し(standalone)で開くための定義。"""
    return Response(
        content=json.dumps(build_dashboard_manifest(), ensure_ascii=False),
        media_type="application/manifest+json",
    )


@router.get(f"{_BASE_PATH}/icon-{{size}}.png", include_in_schema=False)
def dashboard_icon(size: int) -> Response:
    """ホーム画面アイコン。マニフェストと apple-touch-icon から参照される。"""
    if size not in DASHBOARD_ICON_SIZES:
        raise HTTPException(status_code=404, detail="unknown icon size")
    return Response(
        content=render_dashboard_icon_png(size),
        media_type="image/png",
        # 内容はコードから決まり、実質変わらない。実機の再取得を減らす。
        headers={"Cache-Control": "public, max-age=86400"},
    )


# --- 軽量ページ ---


@router.get(_MOBILE_PATH, include_in_schema=False)
def mobile_status_page() -> HTMLResponse:
    """Streamlitを介さない読み取り専用のサマリーページ。

    スマートフォンで見るのは結局ステータスカードの9枚、という用途に対して、
    Streamlit の初期化・WebSocket接続・Reactの読み込みを丸ごと省く。
    ダッシュボード本体(Streamlit)はグラフ・ログ・メンテナンス操作を持つ
    「詳しく見る側」として残し、このページからリンクする。

    `async def` にしないのは、中でDBの読み取りとHTTPスクレイピング(同期)を
    行うため。`def` にしておくと Starlette がスレッドプールで実行し、
    イベントループ(=IoT制御・Webhook受信)を止めない。
    """
    cards, fetched_at = home_status_service.collect_status_cards()
    return HTMLResponse(
        home_status_service.render_mobile_status_page_html(
            cards,
            fetched_at,
            manifest_path=f"{_MOBILE_PATH}/app.webmanifest",
            icon_path=f"{_BASE_PATH}/icon-180.png",
            dashboard_path=f"{_BASE_PATH}/",
            quest_path=_QUEST_APP_PATH,
            status_path=_MOBILE_STATUS_PATH,
        )
    )


@router.get(_MOBILE_STATUS_PATH, include_in_schema=False)
def mobile_status_section() -> HTMLResponse:
    """カードのブロックだけを返す(軽量ページの自動更新用)。

    以前の自動更新は `<meta http-equiv="refresh">` による全ページ再読み込みで、
    60秒ごとに画面が白く瞬き、スクロール位置も先頭へ戻っていた。この断片だけを
    差し替えることで、見ている位置を保ったまま値が新しくなる。

    取得は `collect_status_cards`(TTL60秒のメモ付き)なので、ページ全体を返す
    経路と同じ材料を共有し、DB・スクレイピングの回数は増えない。
    """
    cards, fetched_at = home_status_service.collect_status_cards()
    return HTMLResponse(
        home_status_service.render_status_section_html(
            cards,
            fetched_at,
            dashboard_path=f"{_BASE_PATH}/",
        )
    )


@router.get(f"{_MOBILE_PATH}/app.webmanifest", include_in_schema=False)
def mobile_status_manifest() -> Response:
    """軽量ページ専用のマニフェスト。

    ダッシュボード本体とは `start_url` だけを変えてある。軽量ページから
    ホーム画面に追加すれば軽量ページが、本体から追加すれば本体が開く
    (追加した画面と違うものが開くと戸惑うため、1つにまとめない)。
    """
    manifest = build_dashboard_manifest()
    manifest["name"] = home_status_service.MOBILE_PAGE_TITLE
    manifest["short_name"] = home_status_service.MOBILE_PAGE_TITLE
    manifest["start_url"] = _MOBILE_PATH
    manifest["scope"] = _MOBILE_PATH
    return Response(
        content=json.dumps(manifest, ensure_ascii=False),
        media_type="application/manifest+json",
    )


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
