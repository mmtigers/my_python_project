# MY_HOME_SYSTEM/routers/dashboard_router.py
"""
ダッシュボード(かんたん表示)を `config.DASHBOARD_BASE_PATH` 配下で配信するルーター。

#829: 以前はStreamlit版の「詳細表示」(dashboard.py)と、それへのリバースプロキシ、
そして「かんたん表示」(カードのみの単一ページ)が別々に存在した。詳細表示・
表示モード切替UIを廃止し、かんたん表示を唯一のダッシュボードとする方針変更に伴い、
この`unified_server`自身がホーム/見守り/くらし/システムの4ページをHTMLで直接返す
構成に作り替えた(外部プロセスへの中継は無くなった)。

ページの組み立ては`services/dashboard_page_service.py`(Streamlit不使用)に委譲する
(CLAUDE.md「ルーターは薄く」の方針)。カードの判定・材料の取得キャッシュは
`services/home_status_service.py` が正であることに変わりはない。

ベースパス配下には、ページのほかに次のものも置く:

- スマートフォンのホーム画面に追加するためのマニフェストとアイコン
- ホームページの自動更新用に、カードのブロックだけを返す `{DASHBOARD_BASE_PATH}/status`
- 見守りページのカメラスナップショット画像
"""
import json

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

import config
from services import dashboard_page_service, dashboard_pwa_service, home_status_service

router = APIRouter()

_BASE_PATH = config.DASHBOARD_BASE_PATH
_STATUS_PATH = f"{_BASE_PATH}/status"
# family-quest(PWA)への導線。定義の実体は `services/home_status_service.py` にある。
_QUEST_APP_PATH = home_status_service.QUEST_APP_PATH

# Streamlit版で使われていた `?tab=` の値と、新しいページパスの対応。
# 以前 `/dashboard?tab=watch` のようなURLをスマートフォンのホーム画面に置いていた
# 場合でも開けるよう、同じ値からリダイレクトする。
_LEGACY_TAB_REDIRECTS = {"home": _BASE_PATH, "watch": f"{_BASE_PATH}/watch",
                         "life": f"{_BASE_PATH}/life", "sys": f"{_BASE_PATH}/sys"}


# --- スマートフォンのホーム画面に追加するための付帯物 ---
# 中継の総当たりルートは無くなったが、他の具体的なパス(`/watch`等)より
# **前**に定義する慣習は踏襲する(FastAPIは定義順に照合するため)。


@router.get(f"{_BASE_PATH}/app.webmanifest", include_in_schema=False)
def dashboard_manifest() -> Response:
    """ホーム画面に追加したときアドレスバー無し(standalone)で開くための定義。"""
    return Response(
        content=json.dumps(dashboard_pwa_service.build_dashboard_manifest(), ensure_ascii=False),
        media_type="application/manifest+json",
    )


@router.get(f"{_BASE_PATH}/icon-{{size}}.png", include_in_schema=False)
def dashboard_icon(size: int) -> Response:
    """ホーム画面アイコン。マニフェストと apple-touch-icon から参照される。"""
    if size not in dashboard_pwa_service.DASHBOARD_ICON_SIZES:
        raise HTTPException(status_code=404, detail="unknown icon size")
    return Response(
        content=dashboard_pwa_service.render_dashboard_icon_png(size),
        media_type="image/png",
        # 内容はコードから決まり、実質変わらない。実機の再取得を減らす。
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get(f"{_BASE_PATH}/m", include_in_schema=False)
def legacy_mobile_path_redirect() -> RedirectResponse:
    """旧「かんたん表示」(`/dashboard/m`)のURLを新しいホームページへ301で送る。

    Streamlit版廃止前は本体(詳細表示)とかんたん表示が別のURLだったため、
    スマートフォンのホーム画面にこちらを追加している可能性がある。ホームページと
    かんたん表示が統合された今、`{_BASE_PATH}/` がそのまま行き先になる。
    """
    return RedirectResponse(url=f"{_BASE_PATH}/", status_code=301)


# --- ホームページ ---


@router.get(_BASE_PATH, include_in_schema=False)
@router.get(f"{_BASE_PATH}/", include_in_schema=False)
def dashboard_home(tab: str | None = None) -> Response:
    """ホームページ: ステータスカード + 見守り/くらし/システムへの導線 + 外部リンク。

    `tab` はStreamlit版で使っていたクエリパラメータ(`/dashboard?tab=watch`)。
    スマートフォンのホーム画面にこの形のURLを追加している可能性があるため、
    値があれば対応する新しいページへ302で送る。
    """
    if tab is not None:
        target = _LEGACY_TAB_REDIRECTS.get(tab, _BASE_PATH)
        return RedirectResponse(url=target, status_code=302)

    cards, fetched_at = home_status_service.collect_status_cards()
    return HTMLResponse(
        dashboard_page_service.render_home_page(
            cards,
            fetched_at,
            dashboard_path=f"{_BASE_PATH}/",
            status_path=_STATUS_PATH,
            quest_path=_QUEST_APP_PATH,
            asa_note_url=config.ASA_NOTE_URL,
            refresh_sec=home_status_service.MOBILE_PAGE_REFRESH_SEC,
            manifest_path=f"{_BASE_PATH}/app.webmanifest",
            icon_path=f"{_BASE_PATH}/icon-180.png",
        )
    )


@router.get(_STATUS_PATH, include_in_schema=False)
def dashboard_status_fragment() -> HTMLResponse:
    """ホームページの自動更新用(カードのブロックだけを返す)。"""
    cards, fetched_at = home_status_service.collect_status_cards()
    return HTMLResponse(
        dashboard_page_service.render_home_status_section(
            cards, fetched_at,
            dashboard_path=f"{_BASE_PATH}/",
            refresh_sec=home_status_service.MOBILE_PAGE_REFRESH_SEC,
        )
    )


# --- 見守り / くらし / システム ---


@router.get(f"{_BASE_PATH}/watch", include_in_schema=False)
def dashboard_watch() -> HTMLResponse:
    materials = home_status_service.get_cached_materials()
    return HTMLResponse(
        dashboard_page_service.render_watch_page(
            materials.df_sensor,
            materials.df_security_log,
            dashboard_path=f"{_BASE_PATH}/",
            snapshot_url_prefix=f"{_BASE_PATH}/snapshot",
        )
    )


@router.get(f"{_BASE_PATH}/life", include_in_schema=False)
def dashboard_life() -> HTMLResponse:
    cards, _ = home_status_service.collect_status_cards()
    return HTMLResponse(dashboard_page_service.render_life_page(cards, dashboard_path=f"{_BASE_PATH}/"))


@router.get(f"{_BASE_PATH}/sys", include_in_schema=False)
def dashboard_sys() -> HTMLResponse:
    materials = home_status_service.get_cached_materials()
    now = home_status_service.get_now_jst()
    return HTMLResponse(
        dashboard_page_service.render_sys_page(
            materials.df_sensor,
            materials.nas_data,
            materials.memory,
            materials.disk,
            now,
            dashboard_path=f"{_BASE_PATH}/",
        )
    )


@router.get(f"{_BASE_PATH}/snapshot/{{filename}}", include_in_schema=False)
def dashboard_snapshot(filename: str) -> FileResponse:
    """見守りページのカメラスナップショット画像を1枚返す(パストラバーサル対策込み)。"""
    path = dashboard_page_service.resolve_snapshot_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return FileResponse(path, media_type="image/jpeg")
