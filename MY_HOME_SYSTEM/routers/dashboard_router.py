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
- ページ共通のCSS・各ページのJS(`static/dashboard/`。許可リスト方式、#949)
"""
import json

import pandas as pd
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

import config
from services import (
    dashboard_page_service,
    dashboard_pwa_service,
    home_status_service,
    power_analysis_service,
    power_page_service,
    release_history_service,
    system_info_service,
    timelapse_service,
)

router = APIRouter()

_BASE_PATH = config.DASHBOARD_BASE_PATH
_STATUS_PATH = f"{_BASE_PATH}/status"
# family-quest(PWA)への導線。定義の実体は `services/home_status_service.py` にある。
_QUEST_APP_PATH = home_status_service.QUEST_APP_PATH

# Streamlit版で使われていた `?tab=` の値と、新しいページパスの対応。
# 以前 `/dashboard?tab=watch` のようなURLをスマートフォンのホーム画面に置いていた
# 場合でも開けるよう、同じ値からリダイレクトする。
_LEGACY_TAB_REDIRECTS = {"home": _BASE_PATH, "watch": f"{_BASE_PATH}/watch",
                         "life": f"{_BASE_PATH}/life", "sys": f"{_BASE_PATH}/sys",
                         "updates": f"{_BASE_PATH}/updates"}


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
def dashboard_home(
    tab: str | None = None, hm_loc: str | None = None, hm_metric: str | None = None, hm_src: str | None = None,
) -> Response:
    """ホームページ: ステータスカード + 見守り/くらし/システムへの導線 + 外部リンク。

    `?hm_loc=itami|takasago&hm_metric=temp|hum|act&hm_src=security|takasago|itami`は過去30日ヒートマップの
    拠点・指標・動きの対象(不正な値は既定)。

    `tab` はStreamlit版で使っていたクエリパラメータ(`/dashboard?tab=watch`)。
    スマートフォンのホーム画面にこの形のURLを追加している可能性があるため、
    値があれば対応する新しいページへ302で送る。
    """
    if tab is not None:
        target = _LEGACY_TAB_REDIRECTS.get(tab, _BASE_PATH)
        return RedirectResponse(url=target, status_code=302)

    cards, fetched_at = home_status_service.collect_status_cards()
    link_health = home_status_service.collect_link_health(config.ASA_NOTE_URL, config.YORU_NOTE_URL)
    heatmap_html = _home_heatmap_html()
    climate_html = _home_climate_html(hm_loc, hm_metric, hm_src)
    return HTMLResponse(
        dashboard_page_service.render_home_page(
            cards,
            fetched_at,
            dashboard_path=f"{_BASE_PATH}/",
            status_path=_STATUS_PATH,
            quest_path=_QUEST_APP_PATH,
            asa_note_url=config.ASA_NOTE_URL,
            yoru_note_url=config.YORU_NOTE_URL,
            refresh_sec=home_status_service.MOBILE_PAGE_REFRESH_SEC,
            manifest_path=f"{_BASE_PATH}/app.webmanifest",
            icon_path=f"{_BASE_PATH}/icon-180.png",
            link_health=link_health,
            heatmap_html=heatmap_html,
            climate_html=climate_html,
        )
    )


def _home_heatmap_html() -> str:
    """ホームの「📊 今日の動き」。今日(JST)の全件(60秒キャッシュ)から作る。"""
    return dashboard_page_service.render_home_activity_heatmaps(
        home_status_service.get_sensor_data_today(),
        home_status_service.get_now_jst(),
        dashboard_path=f"{_BASE_PATH}/",
    )


def _home_climate_html(hm_loc: str | None, hm_metric: str | None, hm_src: str | None = None) -> str:
    """ホームの「過去30日の温度/湿度/動き」。どれも10分キャッシュの集計から作る。
    選んでいる指標の分だけを読む(動きを選んでいないときは動きの集計に触れない)。"""
    metric = dashboard_page_service.parse_climate_metric(hm_metric)
    is_activity = metric == "act"
    return dashboard_page_service.render_home_climate_heatmap(
        pd.DataFrame() if is_activity else home_status_service.get_hourly_climate(),
        home_status_service.get_now_jst(),
        dashboard_path=f"{_BASE_PATH}/",
        location=dashboard_page_service.parse_climate_location(hm_loc),
        metric=metric,
        days=home_status_service.CLIMATE_DAYS,
        activity=(
            home_status_service.get_hourly_activity(dashboard_page_service.activity_hourly_grids) if is_activity else None
        ),
        source=dashboard_page_service.parse_climate_source(hm_src),
    )


@router.get(_STATUS_PATH, include_in_schema=False)
def dashboard_status_fragment(
    hm_loc: str | None = None, hm_metric: str | None = None, hm_src: str | None = None,
) -> HTMLResponse:
    """ホームページの自動更新用(カードのブロックだけを返す)。"""
    cards, fetched_at = home_status_service.collect_status_cards()
    link_health = home_status_service.collect_link_health(config.ASA_NOTE_URL, config.YORU_NOTE_URL)
    return HTMLResponse(
        dashboard_page_service.render_home_status_section(
            cards, fetched_at,
            dashboard_path=f"{_BASE_PATH}/",
            refresh_sec=home_status_service.MOBILE_PAGE_REFRESH_SEC,
            quest_path=_QUEST_APP_PATH,
            asa_note_url=config.ASA_NOTE_URL,
            yoru_note_url=config.YORU_NOTE_URL,
            link_health=link_health,
            heatmap_html=_home_heatmap_html(),
            climate_html=_home_climate_html(hm_loc, hm_metric, hm_src),
        )
    )


# --- 見守り / くらし / システム ---


@router.get(f"{_BASE_PATH}/watch", include_in_schema=False)
def dashboard_watch(
    date: str | None = None,
    device: str | None = None,
    hour: str | None = None,
    src: str | None = None,
    hdev: str | None = None,
    tl_all: str | None = None,
) -> HTMLResponse:
    """見守りページ。`?date=YYYY-MM-DD`でログ(防犯・センサー)をその日(JST)に絞る。
    `?device=<device_id>`でセンサーログをその機器だけに絞る(組み合わせ可)。
    `?hour=0〜23&src=security|takasago|itami[&hdev=<device_id>]`はヒートマップのマスをタップした先で、
    その日のその時間のログを「時間帯の詳細」として出す(`date`未指定なら今日)。
    `?tl_all=1`はタイムラプス一覧の先頭10件の上限を外す(最近の写真の下。動画は`/assets/timelapse/`の静的配信を使う)。
    形式が不正な値は無視して通常表示にする(ブックマークの壊れたURLでも開けるように)。"""
    selected_date = dashboard_page_service.parse_log_date(date)
    if selected_date is not None:
        df_sensor = home_status_service.get_sensor_data_for_day(selected_date)
        df_heatmap = None  # 指定日の全件が既に df_sensor
    else:
        df_sensor = home_status_service.get_cached_materials().df_sensor
        # キャッシュは直近N件のため、ヒートマップ用に今日(JST)の全件を別に取る
        df_heatmap = home_status_service.get_sensor_data_today()
    return HTMLResponse(
        dashboard_page_service.render_watch_page(
            df_sensor,
            dashboard_path=f"{_BASE_PATH}/",
            snapshot_url_prefix=f"{_BASE_PATH}/snapshot",
            selected_date=selected_date,
            selected_device=device or None,
            df_heatmap=df_heatmap,
            hour=dashboard_page_service.parse_log_hour(hour),
            hour_src=dashboard_page_service.parse_log_source(src),
            hour_device=hdev or None,
            timelapses=timelapse_service.list_timelapses(),
            show_all_timelapses=tl_all == "1",
        )
    )


@router.get(f"{_BASE_PATH}/life", include_in_schema=False)
def dashboard_life() -> HTMLResponse:
    cards, _ = home_status_service.collect_status_cards()
    materials = home_status_service.get_cached_materials()
    return HTMLResponse(
        dashboard_page_service.render_life_page(
            cards, dashboard_path=f"{_BASE_PATH}/", daily_cost_rows=materials.daily_cost_rows
        )
    )


@router.get(f"{_BASE_PATH}/sys", include_in_schema=False)
def dashboard_sys(nas_days: str | None = None) -> HTMLResponse:
    """システムページ。`?nas_days=7|30|90`でNAS容量グラフの表示期間を切り替える
    (それ以外の値・数値でない値は既定の30日。壊れたブックマークでも開けるようにする)。"""
    try:
        days = int(nas_days) if nas_days is not None else None
    except ValueError:
        days = None
    nas_days = days if days in dashboard_page_service.NAS_HISTORY_DAYS_CHOICES else dashboard_page_service.NAS_HISTORY_DEFAULT_DAYS
    materials = home_status_service.get_cached_materials()
    nas_history = home_status_service.get_nas_history(nas_days)
    now = home_status_service.get_now_jst()
    return HTMLResponse(
        dashboard_page_service.render_sys_page(
            materials.df_sensor,
            materials.nas_data,
            nas_history,
            materials.memory,
            materials.disk,
            now,
            dashboard_path=f"{_BASE_PATH}/",
            nas_days=nas_days,
            boot_history=system_info_service.get_boot_history(),
        )
    )


@router.get(f"{_BASE_PATH}/power", include_in_schema=False)
def dashboard_power(days: str | None = None) -> HTMLResponse:
    """電気ページ。月ごとの推移・日別の電気代・気温との関係・テレビの使い方を表示する。
    `?days=30|60|90`で日別・気温・テレビの対象期間を切り替える(それ以外の値・数値でない値は30日。
    壊れたブックマークでも開けるようにする)。分析は10分キャッシュされる。
    `async def`にしない(DB取得とpandasの計算が同期のため、イベントループを止めない)。"""
    try:
        selected = int(days) if days is not None else None
    except ValueError:
        selected = None
    selected = selected if selected in power_analysis_service.DAYS_CHOICES else power_analysis_service.DEFAULT_DAYS
    report = power_analysis_service.build_power_report(selected)
    return HTMLResponse(power_page_service.render_power_page(report, dashboard_path=f"{_BASE_PATH}/"))


@router.get(f"{_BASE_PATH}/updates", include_in_schema=False)
def dashboard_updates(app: str | None = None) -> HTMLResponse:
    """アップデートページ。ダッシュボード・ファミクエ・あさノート・よるノートの更新履歴を
    新しい順にまとめて表示する。`?app=dashboard|quest|asa|yoru`で1アプリに絞る
    (未知の値は無視して全件)。あさノート・よるノートは外部から取得する(タイムアウト・
    キャッシュ付き。失敗しても残りのアプリは表示する)。
    `async def`にしない(外部取得が同期のため、イベントループを止めない)。"""
    history = release_history_service.get_release_history()
    return HTMLResponse(
        dashboard_page_service.render_updates_page(
            history["entries"], history["sources"], dashboard_path=f"{_BASE_PATH}/", selected_app=app,
        )
    )


@router.get(f"{_BASE_PATH}/static/{{filename}}", include_in_schema=False)
def dashboard_static(filename: str) -> FileResponse:
    """ページ共通のCSS・JSを返す。URLの`?v=`(更新時刻)で版を切り替えるので、古い版を握らせないよう毎回再検証させる。"""
    asset = dashboard_page_service.resolve_static_asset(filename)
    if asset is None:
        raise HTTPException(status_code=404, detail="static asset not found")
    path, media_type = asset
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})


@router.get(f"{_BASE_PATH}/snapshot/{{filename}}", include_in_schema=False)
def dashboard_snapshot(filename: str) -> FileResponse:
    """見守りページのカメラスナップショット画像を1枚返す(パストラバーサル対策込み)。"""
    path = dashboard_page_service.resolve_snapshot_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return FileResponse(path, media_type="image/jpeg")
