# MY_HOME_SYSTEM/views/dashboard/common.py
import logging
import math
import traceback
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime

import pandas as pd
import pytz
import streamlit as st
from core.utils import get_now_jst
from services import analysis_service, home_status_service, train_service

logger = logging.getLogger(__name__)

# スマートフォン幅の境界(px)。family-quest 側(Tailwindの`sm`)と同じ640pxに揃える。
MOBILE_BREAKPOINT_PX = 640

# Streamlitが出力するDOMの属性セレクタに依存するCSS。Streamlitのバージョンが上がって
# data-testid が変わると単に効かなくなるだけで、画面が壊れることはない
# (レイアウトはStreamlit既定に戻る)。旧名(`column`/`stVerticalBlock`)も併記してある。
CUSTOM_CSS = f"""
<style>
    html, body, [class*="css"] {{
        font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif;
    }}

{home_status_service.STATUS_CARD_CSS}

    /* --- 電車ルートカード --- */
    .route-card {{
        background-color: #fff; padding: 15px; border-radius: 10px;
        border: 1px solid #ddd; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .route-path {{
        margin-top: 15px; padding-top: 10px; border-top: 1px dashed #ccc; font-size: 0.95rem; color: #333;
    }}
    .station-node {{ font-weight: bold; color: #000; }}
    .line-node {{ color: #666; font-size: 0.85rem; margin: 0 5px; }}
    .transfer-mark {{ color: #f57f17; font-weight:bold; margin: 0 5px; }}

    .streamlit-expanderHeader {{
        font-weight: bold; color: #0d47a1; background-color: #f0f8ff; border-radius: 5px;
    }}

    /* --- タップ操作しやすさ(全幅共通) --- */
    /* iOS/Androidの推奨タップターゲット(44px)を下回るボタンを無くす。 */
    .stButton > button,
    .stLinkButton > a,
    .stDownloadButton > button {{
        min-height: 44px;
    }}

    /* --- スマートフォン幅 --- */
    @media (max-width: {MOBILE_BREAKPOINT_PX}px) {{
        /* 左右の既定パディングが広く、グラフ・表の実効幅が削られるため詰める。
           上だけは詰めすぎない: Streamlit 既定のヘッダー(「»」「⋮」の行)は position:fixed で
           約 3.75rem(60px)あり、本文はその**下に潜り込む**。以前ここを 1.2rem(約19px)に
           していたため、ページを開いた時点で先頭の行(更新/ファミクエのボタン)の上半分が
           ヘッダーに隠れていた(2026-09-21 に実機のスマホで確認。#822)。
           PC 幅の既定(6rem)まで戻すと縦方向を無駄にするので、ヘッダー高さ+少しにする。
           ヘッダー高さは Streamlit の版で 2.875rem〜3.75rem と変わってきたため、大きい方を
           基準に余裕を持たせている。 */
        .block-container {{
            padding-top: 4.5rem;
            padding-bottom: 3rem;
            padding-left: 0.8rem;
            padding-right: 0.8rem;
        }}

        /* st.columns は幅が足りなくても横並びを維持し、グラフ・表が読めない幅まで
           潰れる。スマホ幅では必ず縦積みにする(旧バージョンの `column` も対象)。 */
        [data-testid="stHorizontalBlock"] {{
            flex-wrap: wrap;
            gap: 0.5rem;
        }}
        [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
        [data-testid="stHorizontalBlock"] > [data-testid="column"] {{
            flex: 1 1 100% !important;
            min-width: 100% !important;
            width: 100% !important;
        }}

        /* タブが画面幅に収まらないとき、横スクロールできることが分かるようにする
           (スクロールバーは隠し、スワイプの慣性スクロールを有効にする) */
        [data-baseweb="tab-list"] {{
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
        }}
        [data-baseweb="tab-list"]::-webkit-scrollbar {{ display: none; }}
        button[data-baseweb="tab"] {{
            padding-left: 0.55rem;
            padding-right: 0.55rem;
        }}

        /* タブ選択は `st.tabs` ではなく `st.segmented_control` で描いている
           (選択中のタブの中身だけを実行するため。`dashboard.py` の
           `_render_tab_selector` のdocstring参照)。

           既定のままだと5つのタブで合計約460pxになり、390px幅では
           「🔧 システム」が画面外に出る。目的のタブを探せないのは元の
           10タブ構成で一番困っていた点なので、5つとも同時に見えるよう
           文字サイズと余白を詰めて等幅(flex: 1 1 0)で並べる。
           それでも入りきらない環境のために横スクロールは残す。

           高さは44px(タップターゲットの下限)を下回らせない。既定では32px。 */
        [data-testid="stSegmentedControl"],
        [data-testid="stButtonGroup"] {{
            /* 既定は内容幅までしか広がらず、画面右側が空いたままになる */
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
        }}
        [data-testid="stSegmentedControl"]::-webkit-scrollbar,
        [data-testid="stButtonGroup"]::-webkit-scrollbar {{ display: none; }}
        [data-testid="stSegmentedControl"] [role="radiogroup"],
        [data-testid="stButtonGroup"] [role="radiogroup"] {{
            display: flex;
            width: 100%;
        }}
        [data-testid="stSegmentedControl"] button,
        [data-testid="stButtonGroup"] button {{
            min-height: 44px;
            white-space: nowrap;
            padding-left: 0.2rem;
            padding-right: 0.2rem;
        }}
        [data-testid="stSegmentedControl"] button p,
        [data-testid="stButtonGroup"] button p {{
            white-space: nowrap;
            font-size: 0.78rem;
        }}
        /* タブ名の絵文字は1枚あたり約20pxを占め、5つ並べると390px幅では
           右端の「システム」がはみ出して横スクロールしないと押せない
           (実測: 絵文字ありで約377px、なしで約257px。360px幅の端末では
           さらに苦しい)。目的のタブを探せないのは10タブ構成で一番困っていた点
           なので、スマホ幅では絵文字を落として5つとも見えることを優先する
           (絵文字はPC幅では残る)。 */
        [data-testid="stSegmentedControl"] button span:has(> [data-testid="stIconEmoji"]),
        [data-testid="stButtonGroup"] button span:has(> [data-testid="stIconEmoji"]) {{
            display: none;
        }}

        /* Streamlit の「Deploy」ボタンは、この用途では押す機会が無いのに
           狭いヘッダーの一等地を占める。スマホ幅では隠す。 */
        [data-testid="stAppDeployButton"] {{
            display: none;
        }}

        /* 防犯カメラのギャラリーも2列で折り返す。4枚が全幅で縦に積まれると、
           下の防犯ログに届くまで数画面ぶんスクロールすることになる。 */
        .st-key-camera_gallery [data-testid="stHorizontalBlock"],
        .st-key-camera_gallery_past [data-testid="stHorizontalBlock"] {{
            flex-wrap: wrap;
        }}
        .st-key-camera_gallery [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
        .st-key-camera_gallery [data-testid="stHorizontalBlock"] > [data-testid="column"],
        .st-key-camera_gallery_past [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
        .st-key-camera_gallery_past [data-testid="stHorizontalBlock"] > [data-testid="column"] {{
            flex: 1 1 45% !important;
            min-width: 45% !important;
            width: auto !important;
        }}

        /* サマリー直下の「詳しく見る」導線。ヘッダー操作列と同じ理由で
           一律100%化の対象から外すが、4つあるので2列で折り返す。 */
        .st-key-summary_jump [data-testid="stHorizontalBlock"] {{
            flex-wrap: wrap;
        }}
        .st-key-summary_jump [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
        .st-key-summary_jump [data-testid="stHorizontalBlock"] > [data-testid="column"] {{
            flex: 1 1 45% !important;
            min-width: 45% !important;
            width: auto !important;
        }}
        .st-key-summary_jump .stButton > button {{
            font-size: 0.85rem;
            padding-left: 0.4rem;
            padding-right: 0.4rem;
            white-space: nowrap;
        }}

        /* 見出しが2〜3行に折り返してスクロール量が増えるのを抑える */
        h1 {{ font-size: 1.5rem !important; }}
        h2 {{ font-size: 1.25rem !important; }}
        h3 {{ font-size: 1.1rem !important; }}

        /* 横方向にページ全体がはみ出すと、縦スクロール時に左右へ揺れて操作しづらい */
        section[data-testid="stMain"] {{ overflow-x: hidden; }}

        /* Streamlit 既定のヘッダー(サイドバー展開「»」とメニュー「⋮」の行)は
           position:fixed かつ半透明のため、スクロールした本文がその下を通ると
           **透けて重なり読めなくなる**(2026-09-21 に実機のスマホで確認)。
           不透明にして、本文が潜り込んでも上端が滲まないようにする。
           PC 幅では画面が広く重なりが気にならないため、スマホ幅だけで適用する。 */
        [data-testid="stHeader"] {{
            background: #ffffff;
            backdrop-filter: none;
            -webkit-backdrop-filter: none;
        }}

        /* ヘッダー操作列(更新 / ファミクエ)だけは横並びを維持する。
           上の stHorizontalBlock の一律 100% 化はグラフ・表には必要だが、
           短いボタン2つには過剰で、ヘッダーが2段になりタブと本文を
           画面外へ押し下げていた。ボタンは 390px 幅でも十分に並ぶ。
           `st.container(key="header_actions")` が付ける `.st-key-header_actions`
           を目印にして、この行だけ既定の折り返し挙動へ戻す。 */
        .st-key-header_actions [data-testid="stHorizontalBlock"] {{
            flex-wrap: nowrap;
        }}
        .st-key-header_actions [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
        .st-key-header_actions [data-testid="stHorizontalBlock"] > [data-testid="column"] {{
            flex: 1 1 0 !important;
            min-width: 0 !important;
            width: auto !important;
        }}
        /* 幅が詰まるぶん、ラベルが折り返して背が高くならないよう少しだけ縮める */
        .st-key-header_actions .stButton > button,
        .st-key-header_actions .stLinkButton > a {{
            font-size: 0.85rem;
            padding-left: 0.4rem;
            padding-right: 0.4rem;
            white-space: nowrap;
        }}
    }}
</style>
"""


# === データ読み込みのキャッシュ (Issue #741) ===
# `dashboard.py` の「🔄 データを更新」ボタンは以前から `st.cache_data.clear()` を
# 呼んでいたが、リポジトリ全体に `@st.cache_data` / `@st.cache_resource` が
# 1つも存在せず、実際には何も消していなかった(=キャッシュが効いていると
# 認識して書かれたコードだけが残っていた)。
#
# Streamlit はウィジェット操作・タブ切替・ページ読み込みのたびにスクリプト
# 全体を再実行するため、この状態では「タブを1回切り替える」たびに
# SQLite から約33,000行(センサー最大30,000 + 駐輪場3,000 + 各種500)を
# 読み直していた。長い読み取りは `unified_server` 側の書き込みと競合して
# "database is locked" の確率も上げる(`analysis_service` 冒頭のコメント参照)。
#
# `analysis_service` は `unified_server.py` からも import されるため、
# Streamlit 依存をそちらへ持ち込んではいけない。キャッシュはこのView層に閉じ込める。
#
# TTL はセンサーの書き込み間隔(5〜10分)より十分短いので表示の鮮度は実質
# 劣化せず、「🔄 データを更新」ボタンが TTL を待たずに捨てる手段として
# ようやく意味を持つようになる。
DASHBOARD_CACHE_TTL_SEC = 60


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def load_sensor_data_cached(limit: int) -> pd.DataFrame:
    """`analysis_service.load_sensor_data` のキャッシュ付きラッパー。"""
    return analysis_service.load_sensor_data(limit=limit)


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def load_generic_data_cached(table_name: str, limit: int = 500) -> pd.DataFrame:
    """`analysis_service.load_generic_data` のキャッシュ付きラッパー。"""
    return analysis_service.load_generic_data(table_name, limit=limit)


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def load_bicycle_data_cached(limit: int) -> pd.DataFrame:
    """`analysis_service.load_bicycle_data` のキャッシュ付きラッパー。"""
    return analysis_service.load_bicycle_data(limit=limit)


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def load_nas_status_cached() -> pd.Series | None:
    """`analysis_service.load_nas_status` のキャッシュ付きラッパー。"""
    return analysis_service.load_nas_status()


# --- DB以外の重い読み取り ---
# Issue #741 では SQLite の読み取りだけをキャッシュしたが、1回の描画で走る
# 重い処理はそれだけではなかった。スマホでの初回表示は次のものにも待たされる。
#
#   - JR運行情報 / Yahoo!路線情報のスクレイピング(いずれもHTTP、timeout 5秒)
#   - `journalctl` のサブプロセス起動
#   - 年間気温の集計SQL(2テーブルを1年分)
#
# しかもJR運行情報はサマリーと「おでかけ」タブの2箇所から呼ばれており、
# 同じ1回の描画の中で2回スクレイピングしていた。DBの読み取りと同じTTLで
# キャッシュし、「🔄 データを更新」でまとめて捨てられるようにする
# (`st.cache_data.clear()` は本モジュールのラッパーもすべて対象にする)。


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def load_jr_traffic_status_cached() -> dict:
    """`train_service.get_jr_traffic_status` のキャッシュ付きラッパー。"""
    return train_service.get_jr_traffic_status()


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def load_route_info_cached(from_station: str, to_station: str) -> dict:
    """`train_service.get_route_info` のキャッシュ付きラッパー。

    検索する出発時刻は「現在時刻+20分」で、TTL(60秒)の間は同じ結果を
    使い回すことになるが、乗換案内の結果が60秒で変わることは実質無い。
    """
    return train_service.get_route_info(from_station, to_station)


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def load_yearly_temperature_stats_cached(year: int) -> pd.DataFrame:
    """`analysis_service.load_yearly_temperature_stats` のキャッシュ付きラッパー。"""
    return analysis_service.load_yearly_temperature_stats(year)


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def get_disk_usage_cached() -> dict | None:
    """`analysis_service.get_disk_usage` のキャッシュ付きラッパー。"""
    return analysis_service.get_disk_usage()


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def get_memory_usage_cached() -> dict | None:
    """`analysis_service.get_memory_usage` のキャッシュ付きラッパー。

    サマリーの「🖥️ サーバー」カードと「🔧 システム」タブのリソース表示の
    2箇所から呼ばれる。
    """
    return analysis_service.get_memory_usage()


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def get_monthly_cost_cached() -> int:
    """`analysis_service.calculate_monthly_cost_cumulative` のキャッシュ付きラッパー。

    今月ぶんの電力ログを集計するSQLで、ホームタブを開くたびに走っていた。
    """
    return analysis_service.calculate_monthly_cost_cumulative()


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def get_system_logs_cached(lines: int = 50, priority=None, target_date=None) -> str:
    """`analysis_service.get_system_logs`(journalctl)のキャッシュ付きラッパー。

    呼び出し側の「🔄 ログを更新」ボタンは、TTLを待たずに取り直すために
    この関数の `.clear()` を呼ぶこと。
    """
    return analysis_service.get_system_logs(
        lines=lines, priority=priority, target_date=target_date
    )


# === グラフのデータ量 ===
# plotly に渡した点は、そのまま WebSocket のペイロードとしてスマートフォンへ
# 転送される。駐輪場の推移(3系列 × 1,000点前後)のように「折れ線の形しか
# 読まない」グラフでは、点を間引いても読み取れる情報は変わらない。
CHART_MAX_POINTS_PER_SERIES = 500


def downsample_for_chart(
    df: pd.DataFrame,
    *,
    timestamp_col: str = "timestamp",
    series_col: str | None = None,
    max_points: int = CHART_MAX_POINTS_PER_SERIES,
) -> pd.DataFrame:
    """系列あたりの点数が `max_points` を超えるとき、等間隔に間引いて返す。

    平均でリサンプルせず等間隔の間引き(stride)にしているのは、
      - 列の型・構成をそのまま保てる(色分けに使う文字列列を落とさない)
      - 欠測区間に元データに無い値を作らない
    ため。ただし瞬間的なスパイクは間引きで落ちうるので、しきい値判定
    (炊飯器の500W等)はグラフ用のこの関数を通さない生データ側で行うこと。

    最新の点は必ず残す(右端が欠けると「止まっている」ように見えるため)。
    """
    if df is None or df.empty or max_points <= 0 or timestamp_col not in df.columns:
        return df

    if series_col and series_col in df.columns:
        groups = [group for _, group in df.groupby(series_col, sort=False)]
    else:
        groups = [df]

    if all(len(group) <= max_points for group in groups):
        return df

    thinned = []
    for group in groups:
        if len(group) <= max_points:
            thinned.append(group)
            continue
        ordered = group.sort_values(timestamp_col)
        step = math.ceil(len(ordered) / max_points)
        picked = ordered.iloc[::step]
        last_row = ordered.iloc[[-1]]
        if picked.index[-1] != ordered.index[-1]:
            picked = pd.concat([picked, last_row])
        thinned.append(picked)

    return pd.concat(thinned).sort_values(timestamp_col)


# === スマホ向けのグラフ設定 ===
# plotly の既定はPCのマウス操作前提で、スマホでは次の3点が実害になる。
#   - モードバー(ズーム・保存等のアイコン列)が 390px 幅ではグラフ本体を圧迫し、
#     しかも指では押しにくい
#   - グラフ上のドラッグが既定でズーム操作になるため、ページをスクロールしようと
#     してグラフに触れると縦スクロールが奪われ、画面から抜け出せなくなる
#   - 既定の高さ(450px)はスマホだとグラフ1枚で画面が埋まる
PLOTLY_MOBILE_CONFIG = {
    "displayModeBar": False,
    "displaylogo": False,
    "scrollZoom": False,
}
CHART_HEIGHT_PX = 280


def render_chart(fig, *, height: int = CHART_HEIGHT_PX) -> None:
    """plotlyのグラフをスマホ向けの設定で描画する共通ヘルパー。

    `dragmode=False` でグラフ上のドラッグ(既定ではズーム)を無効にしている。
    PCでもズームできなくなるが、このダッシュボードのグラフは「形と水準を見る」
    用途で、拡大が要るときは元データ側(表・ログ)を見るほうが早い。
    """
    fig.update_layout(
        height=height,
        dragmode=False,
        margin={"l": 8, "r": 8, "t": 40, "b": 8},
    )
    st.plotly_chart(fig, width="stretch", config=PLOTLY_MOBILE_CONFIG)


# === 時刻の表示 ===
_JST = pytz.timezone("Asia/Tokyo")


def format_short_timestamp(value) -> str:
    """「09/21 03:04」形式にする。読めない値は空文字を返す。

    DBから来る `2026-09-21 03:04:12+09:00` のようなISO文字列は、それだけで
    スマホの表の1列を食い、しかも読み取りづらい。
    """
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ""
    return ts.strftime("%m/%d %H:%M")


def format_relative_time(value, now: datetime | None = None) -> str:
    """「3分前」のような相対表記にする。

    スマホでは「いつのデータか」を一目で判断したい場面が多い(見守り・防犯ログ)。
    1週間より古いもの・未来の時刻は、相対表記にしても分かりにくいので短縮表記で返す。
    """
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ""

    now = now or get_now_jst()
    if ts.tzinfo is None:
        # DB由来の naive な時刻は JST として扱う(`core.utils` のJST固定方針と同じ)
        ts = ts.tz_localize(_JST)

    seconds = (now - ts).total_seconds()
    if seconds < 0 or seconds >= 7 * 86400:
        return format_short_timestamp(ts)
    if seconds < 60:
        return "たった今"
    if seconds < 3600:
        return f"{int(seconds // 60)}分前"
    if seconds < 86400:
        return f"{int(seconds // 3600)}時間前"
    return f"{int(seconds // 86400)}日前"


@st.cache_data(ttl=DASHBOARD_CACHE_TTL_SEC, show_spinner=False)
def cache_generation_started_at() -> datetime:
    """いま表示しているキャッシュ世代が作られた時刻を返す。

    各ローダと同じTTLで期限切れになるため、「画面に出ているデータがいつのものか」の
    目安として使える(厳密には各ローダの初回呼び出し時刻との間にTTL未満のずれが出る)。
    キャッシュが効いている以上、表示時刻をそのまま「最終更新」と書くと嘘になるため、
    この値をヘッダーに出す。
    """
    return get_now_jst()


def render_table(
    df: pd.DataFrame,
    columns: dict,
    *,
    time_cols: Iterable[str] = ("timestamp",),
    relative_time: bool = False,
    height: int | None = None,
) -> None:
    """スマホ幅でも読める表を描画する共通ヘルパー。

    `st.dataframe` は画面幅を超えると横スクロールの箱になり、スマホでは
    縦スクロールの途中で指を横に振らないと端の列が読めない。そのため、
      - 列は `columns`(元の列名 -> 表示名)で挙げたものだけに絞る
      - 時刻列は「09/21 03:04」に短縮する。`relative_time=True` を渡すと
        「09/21 03:04 (3分前)」になる(見守り・防犯ログのように「どれくらい前か」を
        一目で知りたい表向け)
      - 行番号(index)は情報量が無いので隠す
    """
    available = {src: label for src, label in columns.items() if src in df.columns}
    if df.empty or not available:
        st.info("表示できるデータがありません")
        return

    view = df[list(available)].copy()
    now = get_now_jst() if relative_time else None
    for col in time_cols:
        if col not in view.columns:
            continue
        if relative_time:
            view[col] = view[col].map(
                lambda v: f"{format_short_timestamp(v)} ({format_relative_time(v, now)})".strip()
            )
        else:
            view[col] = view[col].map(format_short_timestamp)
    view = view.rename(columns=available)
    st.dataframe(view, width="stretch", hide_index=True, height=height)


# カードの型とHTML組み立ては `services/home_status_service.py` が持つ
# (Streamlit を介さない軽量ページ `/dashboard/m` と同じものを使うため)。
# View 側から従来の名前で参照できるよう再エクスポートする。
StatusCard = home_status_service.StatusCard
render_status_card_html = home_status_service.render_status_card_html


def render_status_grid(cards: Iterable[StatusCard]) -> None:
    """ステータスカードを自動折り返しのグリッドで1ブロックにまとめて描画する。

    以前は `st.columns(3)` を3段重ねて9枚を並べていたが、Streamlitの列は画面幅が
    足りなくても横並びを維持するため、スマートフォンでは1枚あたり約100pxまで潰れて
    値が読めなかった。列数をCSS(`.status-grid`のauto-fit)側に委ねることで、
    スマホでは2列・PCでは3〜5列に自動で切り替わる。
    """
    st.markdown(home_status_service.render_status_grid_html(cards), unsafe_allow_html=True)


def lazy_section(label: str, *, key: str, default_open: bool = False) -> bool:
    """折りたたみセクションを描画し、「開いているか」を返す。

    `st.expander` を使わない理由(スマホでの体感速度):
        Streamlit の `st.expander` は**折りたたまれていても中身のPythonを実行する**
        (結果をクライアント側で隠しているだけ)。そのため、たたまれた
        「📜 サーバーログ」のために `journalctl` のサブプロセスが毎回起動し、
        「🌡️ 気温・湿度の詳細」のために年間分の集計SQLが毎回走っていた。
        開閉状態がPython側から読める `st.toggle` に置き換えることで、
        呼び出し側が `if lazy_section(...):` で中身の実行ごと省ける。

    開閉状態は `st.session_state` に残るため、同じセッションの中では
    expander と同じ感覚(開いたら開いたまま)で使える。ページを再読み込み
    すると閉じた状態に戻る。
    """
    return bool(st.toggle(label, key=f"lazy_section_{key}", value=default_open))


@contextmanager
def safe_section(section_name: str):
    """
    ダッシュボードの1セクション(タブ・サマリー等)の描画を例外から保護する共通ヘルパー。

    Issue #438: ダッシュボード各所で列存在チェック・try/exceptの方針が関数ごとに
    バラバラだった。特に以前の`dashboard.py`は`main()`全体を1つの`try/except`で
    囲んでおり、いずれか1つのタブの描画で例外が起きるとダッシュボード全体が
    エラー画面になり、無関係な他のタブの表示まで巻き込んでいた。本ヘルパーで
    セクション単位に例外を隔離し、失敗したセクションだけプレースホルダを表示して
    他のセクションの描画には影響させないようにする。

    L-L5 (#410)と同じ理由で、`traceback`等の内部詳細(ファイルパス・設定値等)は
    画面には出さずログにのみ残す。
    """
    try:
        yield
    except Exception as e:
        logger.error(f"{section_name}の表示中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        st.error(f"⚠️ {section_name}の表示中にエラーが発生しました。ログを確認してください。")
