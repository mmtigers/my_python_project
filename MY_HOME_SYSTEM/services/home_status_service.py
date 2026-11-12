# MY_HOME_SYSTEM/services/home_status_service.py
"""「家のいまの状況」(サマリーカード)の算出と、その最小表示。

このモジュールが1箇所に集めているもの:
    - 各カードの判定ロジック(実家の動き・在宅・サーバー等)
    - カード1枚のHTML組み立て(XSS対策を含む。Issue #378)
    - カードのCSS
    - 時刻の相対表記(見守りページのログ表示・システムページの鮮度表示と共有)

#829: 以前はStreamlit版ダッシュボード(`views/dashboard/`)と、Streamlitを介さない
軽量ページ(`/dashboard/m`。旧かんたん表示)の2つが存在し、判定ロジックが
食い違わないようこのモジュールに1本化していた。Streamlit版は廃止し、
`routers/dashboard_router.py` 配下のページ(この`unified_server`が直接HTMLを返す)
だけになったが、「Streamlitに依存しないダッシュボードの正のロジック置き場」という
このモジュールの役割自体は変わらないため、そのまま維持する。
"""
import html
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple

import config
import pandas as pd
import pytz
import requests
from core import state_file
from core.database import get_ro_connection
from core.utils import get_now_jst

from services import analysis_service, recording_status_service

logger = logging.getLogger(__name__)

_JST = pytz.timezone("Asia/Tokyo")


def format_short_timestamp(value) -> str:
    """「09/21 03:04」形式にする。読めない値は空文字を返す。"""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ""
    return ts.strftime("%m/%d %H:%M")


def format_relative_time(value, now: datetime | None = None) -> str:
    """「3分前」のような相対表記にする。1週間より古い・未来の時刻は短縮表記で返す。"""
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

# ダッシュボードのキャッシュTTL(秒)。#829 で廃止した Streamlit 版の
# DASHBOARD_CACHE_TTL_SEC と揃えていた値を引き継いでいる。センサーの書き込み間隔(5〜10分)より
# 十分短いので表示の鮮度は実質劣化しない。
STATUS_CACHE_TTL_SEC = 60

# ダッシュボードで読むセンサー行数。カードの判定・見守りページのログ表示に
# 必要なのが「直近」と「今日」だけのため、多くは読まない。
MOBILE_SENSOR_ROW_LIMIT = 3000

# カード1枚のCSS。ホームページと各サブページ(`services/dashboard_page_service.py`)が共有する。
STATUS_CARD_CSS = """
    /* --- ステータスカード ---
       CSS Grid で「入るだけ並べて自動で折り返す」(スマホ幅では2列、PCでは4〜6列)。
       色は `static/dashboard/dashboard.css` のデザイントークンを参照する(ダークモードも変数側で切替)。
       状態は背景色だけでなく、左端の太い帯・文字中の絵文字(⚠️/⚪等)でも判別できる。 */
    .status-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
        gap: 10px;
        margin-bottom: 8px;
    }
    /* カードが1枚だけのグループ。auto-fit は1枚だとその1枚を行いっぱいに引き伸ばすため、
       列を2つに固定して上下のグループと同じ幅・同じ左端にし、広い画面では頭打ちにする。
       (`minmax(0, ...)` なのは、極端に狭い画面で横にはみ出さないため) */
    .status-grid-solo {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .status-grid-solo > .status-card {
        max-width: 300px;
    }
    .status-card {
        padding: 12px 12px 12px 14px;
        border-radius: var(--radius);
        text-align: left;
        box-shadow: var(--shadow);
        /* 値や補足が折り返しても溢れないよう固定heightではなく min-height */
        min-height: 92px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: flex-start;
    }
    /* カード自体が詳細ページへのリンク(<a>)になる場合の打ち消し(下線・リンク色)。
       テーマ色で「状態」を表しているため、既定のリンク表示にしない。 */
    a.status-card {
        text-decoration: none;
        -webkit-tap-highlight-color: var(--tap);
        transition: transform 0.1s, box-shadow 0.15s;
    }
    a.status-card:hover { box-shadow: 0 2px 4px rgba(20,30,50,0.1), 0 4px 14px rgba(20,30,50,0.08); }
    a.status-card:active {
        /* 押したことが分かるように少しだけ沈ませる(タップの手応え) */
        transform: scale(0.98);
    }
    .status-title {
        font-size: 0.8rem; color: var(--muted); margin-bottom: 4px; font-weight: bold;
    }
    .status-value {
        font-size: 1.1rem; font-weight: bold; line-height: 1.3; white-space: normal;
        word-break: break-word;
    }
    /* 値の下の補足(前回値・前日比・最終検知時刻)。主役は値なので小さく薄く置く。 */
    .status-sub {
        font-size: 0.8rem; font-weight: normal; line-height: 1.35; margin-top: 4px;
        color: var(--muted); white-space: normal; word-break: break-word;
    }
    .theme-green { background-color: var(--ok-bg); color: var(--ok); border: 1px solid var(--ok-bd); border-left: 5px solid var(--ok); }
    .theme-yellow { background-color: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn-bd); border-left: 5px solid var(--warn); }
    .theme-red { background-color: var(--bad-bg); color: var(--bad); border: 1px solid var(--bad-bd); border-left: 5px solid var(--bad); }
    .theme-blue { background-color: var(--accent-soft); color: var(--accent); border: 1px solid var(--accent-bd); border-left: 5px solid var(--accent); }
    .theme-gray { background-color: var(--neutral-bg); color: var(--neutral); border: 1px solid var(--neutral-bd); border-left: 5px solid var(--faint); }

    /* カードのグループ見出し。カード自体より小さく薄くして「並びの区切り」として見せる。 */
    .group-title {
        font-size: 0.8rem;
        font-weight: bold;
        color: var(--muted);
        margin: 18px 0 8px;
        letter-spacing: 0.04em;
    }
"""


# === ダッシュボードのタブ定義 ===
# ここに置いてある理由:
#     各カードは「どのページに詳細があるか」(`StatusCard.tab`)を持つ。ホームは
#     その情報からカードを `{DASHBOARD_BASE_PATH}/<キー>` のリンクにするので、
#     キーを知る必要がある。#829 以前は Streamlit 版の `dashboard.py` もこれを
#     読み込んでいたため、Streamlit を import しないここに置いた(Streamlit 版の
#     廃止後も、カードの組み立てと同じ場所に置く理由は変わらない)。
#
# スマホ対応の再設計: 以前はサマリーを常時最上部に出したうえでタブが10個
# (クエスト/電車遅延/防犯カメラ/電力・環境/気温詳細/健康管理/高砂実家/
#  ログ分析/システム管理/駐輪場)あり、スマートフォンでは
#   - どのタブを開いてもサマリーを越えるスクロールが必要
#   - タブ列が画面幅の数倍になり、目的のタブを探せない
# という状態だった。用途で束ね直し、サマリーも「ホーム」タブに入れてある。
# (当初は5つ。「🚃 おでかけ」は中身の電車運行情報・駐輪場を退役させたため撤去した)
#
# キーはサブページのパス(`/watch` 等)でもある。Streamlit 版の `?tab=` の値と同じため、
# 旧URLのリダイレクト(`routers/dashboard_router.py`)もこのキーで対応付く。
DASHBOARD_TABS: tuple[tuple[str, str], ...] = (
    ("home", "🏠 ホーム"),
    ("watch", "👀 見守り"),
    ("life", "💡 くらし"),
    ("sys", "🔧 システム"),
)
DASHBOARD_TAB_KEYS: tuple[str, ...] = tuple(key for key, _ in DASHBOARD_TABS)

# family-quest(PWA)への導線。`unified_server.py` が `/quest` にSPAをマウントしている。
# ここに置いてあるのはタブ定義と同じ理由で、カードの組み立てとページの組み立ての
# 両方から使うため(#829 以前は Streamlit 版の `dashboard.py` からも使っていた)。
QUEST_APP_PATH = "/quest"

# === カードの並びとグループ ===
# 並び順は「スマホで開いたときに上から見たい順」= 利用頻度で決めてある。
# 見守り(留守・在宅・駐車場・カメラ)を最初に、毎回は見ないシステム系を最後に置く。
# グループの見出しは、9枚が1つの塊に見えて目的のカードを探しにくくなるのを防ぐ。
#
# #829: ファミクエ(承認待ち件数)のカードは廃止した。ダッシュボード内にファミクエの
# 状態表示を残さず、外部リンクカードのみにするという方針変更のため
# (詳細は routers/dashboard_router.py の新しいページ構成を参照)。
#
# 「🌡️ 室温」は拠点別の温湿度カード(伊丹・高砂・長岡京)。見守りの次に置き、
# 直下に過去30日の温湿度ヒートマップを差し込む(カード→その推移が隣り合う)。
CARD_GROUPS: tuple[tuple[str, str], ...] = (
    ("watch", "👀 見守り"),
    ("climate", "🌡️ 室温"),
    ("life", "💡 くらし"),
    ("sys", "🔧 システム"),
)
CARD_GROUP_LABELS: dict[str, str] = dict(CARD_GROUPS)


# === 温湿度を表示する拠点 ===
# (拠点名, `?hm_loc=` のキー, カードの見出し)。並び順もこの順(カード・ヒートマップの切り替えとも)。
# 以前は拠点名・URLキーの対応が `dashboard_page_service` の3か所(`CLIMATE_LOCATIONS`・
# `parse_climate_location`・`_climate_switch`)に散らばっており、1か所だけ足すと
# 切り替えリンクの組み立てが KeyError でホームごと500になる構造だった。ここを唯一の定義元にする。
# 拠点名は `devices.json` の `location` と、Nature Remo の保存名の先頭(`伊丹_…`)に一致させる。
# ここに置くのは、カード(このモジュール)とヒートマップ(`dashboard_page_service`)の両方が使い、
# `dashboard_page_service` は循環importになるためこのモジュールを import 元にできないから。
# カードの見出しは見守りの「🏠 伊丹 (自宅)」「👵 高砂 (実家)」と同じにしない(同じ見出しが2枚並ぶと
# どちらが温湿度か分からない)。「〜の室温」で揃え、長岡京(単身赴任先)は🧳で示す。
CLIMATE_SITES: tuple[tuple[str, str, str], ...] = (
    ("伊丹", "itami", "🏠 伊丹の室温"),
    ("高砂", "takasago", "👵 高砂の室温"),
    ("長岡京", "nagaokakyo", "🧳 長岡京の室温"),
)
CLIMATE_LOCATIONS: tuple[str, ...] = tuple(name for name, _, _ in CLIMATE_SITES)
CLIMATE_LOCATION_KEYS: dict[str, str] = {name: key for name, key, _ in CLIMATE_SITES}

# 温湿度の記録は5分ごと(scheduler_boot の位相ずれで最大5分遅れる)。これを超えたら
# 「更新が止まっています」として灰色にする。システムページの「⚡ 電気・環境の見守り」と同じ閾値。
CLIMATE_STALE_MIN = 30
# これより古い値は出さない(「データなし」)。
CLIMATE_MAX_AGE_MIN = 24 * 60
# 温度の色分け。ヒートマップの5段階(15/20/25/30℃)の両端に合わせる。
CLIMATE_COLD_C = 15.0
CLIMATE_HOT_C = 30.0
# これ未満の湿度は補足に「乾燥」と添える。
CLIMATE_DRY_PCT = 30.0


def climate_location(device_id: Any, device_name: Any) -> str | None:
    """温湿度の記録がどの拠点のものか。`devices.json`の`location`を優先し、無ければ機器名の先頭
    (`伊丹_`/`高砂_`。Nature Remoの保存名)で判定する。どちらでも分からなければ None。

    Nature Remo 由来の行は device_id が devices.json に無く、`load_sensor_data` の `location` 列は
    「その他」になるため、カード・ヒートマップとも `location` 列ではなくここで判定する。"""
    for dev in config.MONITOR_DEVICES:
        if dev.get("id") == device_id and dev.get("location") in CLIMATE_LOCATIONS:
            return dev["location"]
    name = str(device_name or "")
    return next((loc for loc in CLIMATE_LOCATIONS if name.startswith(loc)), None)


class StatusCard(NamedTuple):
    """サマリーに並べる1枚のステータスカード。

    `value_is_html`: `value` に意図的なHTML断片(色付けの`<span>`・改行の`<br>`等)を
    含める呼び出し元だけ True にする。詳細は `render_status_card_html` を参照。
    `tab`: このカードの詳細が載っているページのキー(`DASHBOARD_TABS`)。ホームは
    これを使ってカード自体をそのページへのリンクにする(`card_detail_href`)。
    `sub`: 値の下に小さく出す補足(前回値・前日比・最終検知時刻など)。「いまの値」
    だけでは高いのか低いのか判断できないカードに、比べる相手を添えるためのもの。
    常にHTMLエスケープされる(`value` と違い、HTML断片は渡せない)。
    `href`: 詳細がダッシュボードのタブではなく別のアプリにあるカード(ファミクエ)
    のための、直接のリンク先。指定すると `tab` より優先される。
    `group`: カードが属する見出し(`CARD_GROUPS` のキー)。よく見るものから順に
    並べたうえで、どこからどこまでが同じ用途かを見出しで示すために使う。
    `anchor`: `tab` のURLの末尾にそのまま付け足す文字列(例: `#takasago-log`、
    `?camera=xxx#camera-section`)。同じタブに複数のカードが属するとき、
    どのカードをタップしても同じページの先頭に飛ぶだけになってしまうのを防ぎ、
    タップしたカードの内容が実際に載っているページ内の場所まで連れて行く。
    """
    title: str
    value: str
    theme: str
    value_is_html: bool = False
    tab: str | None = None
    sub: str | None = None
    href: str | None = None
    group: str | None = None
    anchor: str | None = None


def render_status_card_html(
    title: str,
    value: str,
    theme: str,
    *,
    value_is_html: bool = False,
    href: str | None = None,
    sub: str | None = None,
) -> str:
    """
    ステータスカードのHTMLを生成。

    Issue #378: `title`/`value`はそのまま描画されるため、以前はスクレイピング由来・
    DB由来の文字列(quest_title/reward_title等)をそのまま埋め込むと格納型XSSに
    なりえた。`title`は常にHTMLエスケープする。`value`も既定でエスケープするが、
    色付け・改行等で意図的にHTML断片
    (`<span>`/`<br>`等)を組み立てて渡す呼び出し元は、`value_is_html=True`を
    指定してエスケープをスキップできる(その場合、`value`の構築元に外部/DB由来の
    生文字列を含めないこと)。

    `href` を渡すとカード全体がリンク(`<a>`)になる。#829 で廃止した Streamlit 版は
    渡していなかった(リンクを踏むとページ全体が再読み込みになり、同じ移動を
    `dashboard.py` の「詳しく見る」ボタンが担っていたため)。現在は
    `unified_server` が返すページだけが呼び出し元で、ホームのカードが使う。
    """
    safe_title = html.escape(title)
    safe_value = value if value_is_html else html.escape(value)
    # 改行・インデントを入れずに1行で返す。#829 で廃止した Streamlit 版の `st.markdown` は本文に
    # `textwrap.dedent()` をかけてからMarkdownとして解釈していたため、整形用の空白が
    # 残っていると「空白だけの行がHTMLブロックを終端し、続く字下げがコードブロックに
    # なる」ことで、2枚目以降のカードが生のタグ文字列として画面に出ていた(#807)。
    if href is None:
        open_tag = f'<div class="status-card {theme}">'
        close_tag = "</div>"
    else:
        open_tag = f'<a class="status-card {theme}" href="{html.escape(href)}">'
        close_tag = "</a>"
    sub_html = "" if not sub else f'<div class="status-sub">{html.escape(sub)}</div>'
    return (
        f"{open_tag}"
        f'<div class="status-title">{safe_title}</div>'
        f'<div class="status-value">{safe_value}</div>'
        f"{sub_html}"
        f"{close_tag}"
    )


def card_detail_href(card: StatusCard, dashboard_path: str) -> str | None:
    """カードの詳細へのURLを返す(行き先が無いカードは None)。

    `href` を持つカードはそれを、そうでなければ `tab` から見守り/くらし/システムの
    各ページへのURLを作る(#829: 以前はStreamlit版の `?tab=` クエリだったが、
    Streamlit版廃止に伴い専用ページ(`/watch`/`/life`/`/sys`)へのパスに変えた)。

    `dashboard_path` は閲覧中のオリジンからのルート相対パス(例: `/dashboard/`)。
    固定URLを埋めると、LAN内のIP・Cloudflare 経由の公開ドメインのどちらか一方でしか
    繋がらなくなる(`href` 側も同じ理由でルート相対にすること)。
    """
    if card.href is not None:
        return card.href
    if card.tab is None:
        return None
    # ホームはサブページではなくダッシュボードのルート(`/dashboard/`)。`anchor`でページ内へ飛ばす
    # (温湿度カード → 同じホームのヒートマップ)。
    href = f"{dashboard_path.rstrip('/')}/" + ("" if card.tab == "home" else card.tab)
    if card.anchor:
        href += card.anchor
    return href


def group_cards(cards) -> list[tuple[str | None, list[StatusCard]]]:
    """カードを `group` ごとの塊に分ける(並び順は変えない)。

    `CARD_GROUPS` の順に並んでいる前提で、**隣り合う同じグループ**をまとめる。
    グループを持たないカードは見出し `None` の塊になるので、`group` を付けずに
    カードを足しても表示から漏れない。
    """
    grouped: list[tuple[str | None, list[StatusCard]]] = []
    for card in cards:
        if grouped and grouped[-1][0] == card.group:
            grouped[-1][1].append(card)
        else:
            grouped.append((card.group, [card]))
    return grouped


def render_status_grid_html(
    cards,
    *,
    dashboard_path: str | None = None,
    show_group_titles: bool = True,
    group_extras: dict[str, str] | None = None,
) -> str:
    """カードを自動折り返しのグリッドにまとめたHTMLを返す。

    `group` を持つカードには見出しが付く(`CARD_GROUPS`)。9枚が1つの塊に見えると
    目的のカードを探すのに時間がかかるため、用途の変わり目を示す。

    `dashboard_path` を渡すと、詳細のあるカードがその行き先へのリンクになる
    (軽量ページ専用。理由は `render_status_card_html` の docstring を参照)。

    `group_extras` は `{グループのキー: HTML}`。そのグループのカードの直後に差し込む
    (ホームの「🌡️ 室温」の直下に温湿度ヒートマップを置くため)。

    `show_group_titles=False` はグループ見出しを出さない(ページ自身の見出しが
    グループ名と同じで、同じ見出しが2つ並んでしまうサブページ用。表示だけの違い)。
    """
    blocks = []
    for group_key, group_cards_ in group_cards(cards):
        label = CARD_GROUP_LABELS.get(group_key or "")
        if label and show_group_titles:
            blocks.append(f'<h2 class="group-title">{html.escape(label)}</h2>')
        # 1枚だけのグループは、カードが行いっぱいに伸びないよう目印を付ける
        # (理由は `.status-grid-solo` のCSSコメントを参照)。
        solo = " status-grid-solo" if len(group_cards_) == 1 else ""
        cards_html = "".join(
            render_status_card_html(
                card.title,
                card.value,
                card.theme,
                value_is_html=card.value_is_html,
                href=None if dashboard_path is None else card_detail_href(card, dashboard_path),
                sub=card.sub,
            )
            for card in group_cards_
        )
        blocks.append(f'<div class="status-grid{solo}">{cards_html}</div>')
        if group_extras and group_key in group_extras:
            blocks.append(group_extras[group_key])
    return "".join(blocks)


# === 各カードの判定 ===
# いずれも副作用を持たない純粋関数。入力(DataFrame・取得済みの値)は呼び出し側が渡す
# (下記の collect_status_cards から。#829 以前は Streamlit 版のキャッシュ付きローダからも)。

def _takasago_activity(df_sensor: pd.DataFrame) -> pd.DataFrame:
    """高砂(実家)で「動きがあった」とみなす行。

    判定(`get_takasago_status`)と補足表示(`describe_takasago`)の両方がここを使う。
    どちらかが別の条件で拾うと、カードの色と「最終検知」の時刻が食い違う。
    """
    if df_sensor.empty or "location" not in df_sensor.columns or "contact_state" not in df_sensor.columns:
        return df_sensor.iloc[0:0]
    return df_sensor[
        (df_sensor["location"] == "高砂") & (df_sensor["contact_state"].isin(["open", "detected"]))
    ]


def get_takasago_status(df_sensor: pd.DataFrame, now: datetime) -> tuple[str, str]:
    val = "⚪ データなし"
    theme = "theme-gray"

    df_taka = _takasago_activity(df_sensor)
    if not df_taka.empty:
        last_active = df_taka.iloc[0]["timestamp"]
        diff_min = (now - last_active).total_seconds() / 60
        if diff_min < 60:
            val = "🟢 元気 (1h以内)"
            theme = "theme-green"
        elif diff_min < 180:
            val = "🟡 静か (3h以内)"
            theme = "theme-yellow"
        else:
            val = f"🔴 {int(diff_min/60)}時間 動きなし"
            theme = "theme-red"
    return val, theme


def _itami_motion(df_sensor: pd.DataFrame) -> pd.DataFrame:
    """伊丹(自宅)の人感センサー由来の検知行(新しい順)。

    高砂側と同じ理由で、判定と補足表示が同じ抽出を共有する。
    """
    required_cols = ["location", "device_type", "movement_state", "contact_state"]
    if df_sensor.empty or not all(col in df_sensor.columns for col in required_cols):
        return df_sensor.iloc[0:0]

    # 1. デバイスタイプの判定: 'Motion' を含むか、または 'Webhook' (SwitchBot) である
    is_motion_device = (
        df_sensor["device_type"].str.contains("Motion", na=False) |
        (df_sensor["device_type"] == "Webhook")
    )

    # 2. 検知ステータスの判定: movement_state または contact_state が 'detected' である
    # (webhook_routerが contact_state に保存してしまう問題への対応)
    is_detected = (
        (df_sensor["movement_state"] == "detected") |
        (df_sensor["contact_state"] == "detected")
    )

    return df_sensor[
        (df_sensor["location"] == "伊丹") &
        is_motion_device &
        is_detected
    ].sort_values("timestamp", ascending=False)


def _itami_contact(df_sensor: pd.DataFrame) -> pd.DataFrame:
    """伊丹(自宅)の開閉センサーが開いた行(新しい順)。人感センサーが無いときの代替。"""
    required_cols = ["location", "contact_state"]
    if df_sensor.empty or not all(col in df_sensor.columns for col in required_cols):
        return df_sensor.iloc[0:0]
    return df_sensor[
        (df_sensor["location"] == "伊丹") & (df_sensor["contact_state"] == "open")
    ].sort_values("timestamp", ascending=False)


def get_itami_status(df_sensor: pd.DataFrame, now: datetime) -> tuple[str, str]:
    """伊丹（自宅）のステータス判定（修正版）"""
    val = "⚪ データなし"
    theme = "theme-gray"

    df_motion = _itami_motion(df_sensor)

    if not df_motion.empty:
        diff_m = (now - df_motion.iloc[0]["timestamp"]).total_seconds() / 60
        if diff_m < 10:
            val = "🟢 活動中 (今)"
            theme = "theme-green"
        elif diff_m < 60:
            # 何分前かは補足(最終検知 HH:MM)に出る。値はスマホ幅で1行に収まる長さにする
            # (「活動中 (16分前)」は2行に折れ、隣のカードと高さが揃わなかった)。
            val = "🟢 活動中"
            theme = "theme-green"
        else:
            val = f"🟡 静か ({int(diff_m/60)}h前)"
            theme = "theme-yellow"
    else:
        # 開閉センサーのロジック
        df_contact = _itami_contact(df_sensor)
        if not df_contact.empty:
            diff_c = (now - df_contact.iloc[0]["timestamp"]).total_seconds() / 60
            if diff_c < 60:
                val = "🟢 活動中"
                theme = "theme-green"
    return val, theme


# サーバーのメモリ使用率がこれ以上なら「メモリ不足」(赤)。
SERVER_MEMORY_ALERT_PERCENT = 80


def get_server_status(memory: dict[str, float] | None) -> tuple[str, str]:
    """サーバーが正常か。値は家族にも伝わる言葉(正常/メモリ不足)にし、使用率の数字は補足(`describe_server`)に出す。"""
    if memory:
        if memory["percent"] < SERVER_MEMORY_ALERT_PERCENT:
            return "🟢 正常", "theme-green"
        return "🔴 メモリ不足", "theme-red"
    return "⚪ 取得できません", "theme-gray"


def get_nas_status_simple(nas_data: pd.Series | None) -> tuple[str, str]:
    if nas_data is None:
        return "⚪ データなし", "theme-gray"
    try:
        if nas_data["status_ping"] == "OK":
            return "🟢 稼働中", "theme-green"
        else:
            return "🔴 応答なし", "theme-red"
    except KeyError:
        return "🟡 データ異常", "theme-yellow"


# カメラの動体検知が `device_records` に入るときの `device_type`
# (`monitors/camera_monitor.py` が ONVIF のイベントを受けて書く)。
CAMERA_DEVICE_TYPE = "ONVIF_CAMERA"

# 駐車場カメラの `config.CAMERAS` 上の `id`(`device_records.device_id` に
# `monitors/camera_monitor.py` が書き込む値と同じ)。
#
# 以前は表示名(`name`)の完全一致(`friendly_name == "駐車場"`)で判定していたが、
# `name` は運用者がdevices.jsonで自由に付ける文字列(実際には「駐車場カメラ」)で
# あり、`name` を変えるたびに一致しなくなって静かに「⚪ データなし」に壊れる
# (エラーにならないので気づけない)。`id` は変更されにくいため、`id` で照合する。
#
# #829: 以前は「車(伊丹)」カードを car_records(action='LEAVE'/'ARRIVE')テーブルから
# 判定していたが、このテーブルへ書き込む経路がリポジトリ内のどこにも存在せず
# (car_recordsは常に空)、恒常的に「🏠 在宅」を返し続ける死んだ機能だった。
# 駐車場カメラは既に動体検知イベントを`device_records`へ記録しているため、
# これを流用する。ただし動体検知は人の往来も拾うため「車が今あるかどうか」を
# 断定はできず、あくまで「駐車場での最後の動き」という情報として扱う
# (get_camera_status と同じ理由で常に情報色(青)/グレーにし、誤って外出/在宅を
# 断定しない)。
PARKING_CAMERA_ID = "VIGI_C540_Parking"


def _camera_motion(df_sensor: pd.DataFrame) -> pd.DataFrame:
    """カメラが動きを捉えた行(新しい順)。

    判定(`get_camera_status`)と補足表示(`describe_camera`)が同じ抽出を共有する。
    """
    required_cols = ["device_type", "movement_state"]
    if df_sensor.empty or not all(col in df_sensor.columns for col in required_cols):
        return df_sensor.iloc[0:0]
    return df_sensor[
        (df_sensor["device_type"] == CAMERA_DEVICE_TYPE) &
        (df_sensor["movement_state"] == "ON")
    ].sort_values("timestamp", ascending=False)


def get_camera_status(
    df_sensor: pd.DataFrame, now: datetime, recording: list[dict[str, Any]] | None = None
) -> tuple[str, str]:
    """カメラが録画できているか(分かるとき)。分からないときは、最後に動きを捉えたのはいつか。

    `recording` は `recording_status_service.camera_recording_states()` の結果(カメラごとの
    `{"name", "state"}`。`state` は `ok`/`stalled`/`missing`)。渡されたときは録画の状態を値にする:
    1台でも止まっていれば赤(どのカメラか)、全台録画できていれば緑。カメラの故障・接続断は
    動体検知の有無からは分からない(「24時間 検知なし」は静かな日と区別できない)ため、
    録画ファイルの新しさを正とする。

    `recording` が None(NAS未マウントで判定できない・取得失敗)のときは従来どおり最後の動体検知を出す。
    **このときの色は常に情報色(青)かグレーにする**。家族が出入りすれば毎日検知するため、
    赤・黄にするとホームの「確認が必要です」が毎回埋まって意味を失う。
    """
    if recording:
        stopped = [r for r in recording if r.get("state") != "ok"]
        if len(stopped) == 1:
            return f"🔴 {stopped[0]['name']} 録画停止", "theme-red"
        if stopped:
            return f"🔴 {len(stopped)}台 録画停止", "theme-red"
        if len(recording) == 1:
            return "🟢 録画中", "theme-green"
        return f"🟢 {len(recording)}台とも録画中", "theme-green"

    df_cam = _camera_motion(df_sensor)
    if df_cam.empty:
        return "⚪ データなし", "theme-gray"

    diff_m = (now - df_cam.iloc[0]["timestamp"]).total_seconds() / 60
    if diff_m < 10:
        return "いま動きあり", "theme-blue"
    if diff_m < 60:
        return f"{int(diff_m)}分前に検知", "theme-blue"
    if diff_m < 24 * 60:
        return f"{int(diff_m / 60)}時間前に検知", "theme-blue"
    return "24時間 検知なし", "theme-gray"


def _parking_camera_motion(df_sensor: pd.DataFrame) -> pd.DataFrame:
    """駐車場カメラの動体検知行(新しい順)。`get_parking_status`/`describe_parking`が共有する。"""
    df_cam = _camera_motion(df_sensor)
    if df_cam.empty or "device_id" not in df_cam.columns:
        return df_cam.iloc[0:0]
    return df_cam[df_cam["device_id"] == PARKING_CAMERA_ID]


def get_parking_status(df_sensor: pd.DataFrame, now: datetime) -> tuple[str, str]:
    """駐車場カメラが最後に動きを捉えたのはいつか(`get_camera_status`と同じ考え方)。

    人の往来も拾うため「在宅/外出中」を断定できない。常に情報色(青)かグレーにする。
    """
    df_park = _parking_camera_motion(df_sensor)
    if df_park.empty:
        return "⚪ データなし", "theme-gray"

    diff_m = (now - df_park.iloc[0]["timestamp"]).total_seconds() / 60
    if diff_m < 10:
        return "いま動きあり", "theme-blue"
    if diff_m < 60:
        return f"{int(diff_m)}分前に検知", "theme-blue"
    if diff_m < 24 * 60:
        return f"{int(diff_m / 60)}時間前に検知", "theme-blue"
    return "24時間 検知なし", "theme-gray"


# === 補足表示(カードの値の下に小さく出す) ===
# 「いまの値」だけでは高いのか低いのか・普段どおりなのかが判断できないカードに、
# 比べる相手(前回いつ・先月の同じ時点・最終検知時刻)を添える。
# 判定に使う行の抽出は上の `_*_activity` / `_*_rows` を共有するので、カードの色と
# ここに出す時刻が食い違うことはない。


def _format_moment(moment, now: datetime) -> str | None:
    """`09:40` / `昨日 18:40` / `9/18 18:40` のいずれかにする。"""
    if moment is None or pd.isna(moment):
        return None
    moment = pd.Timestamp(moment)
    day_diff = (now.date() - moment.date()).days
    if day_diff == 0:
        return moment.strftime("%H:%M")
    if day_diff == 1:
        return moment.strftime("昨日 %H:%M")
    return f"{moment.month}/{moment.day} {moment.strftime('%H:%M')}"


def _latest_timestamp(df: pd.DataFrame):
    """新しい順に並んでいる前提の DataFrame から先頭の時刻を取る。"""
    if df.empty or "timestamp" not in df.columns:
        return None
    return df.iloc[0]["timestamp"]


def describe_takasago(df_sensor: pd.DataFrame, now: datetime) -> str | None:
    at = _format_moment(_latest_timestamp(_takasago_activity(df_sensor)), now)
    return None if at is None else f"最終検知 {at}"


def describe_itami(df_sensor: pd.DataFrame, now: datetime) -> str | None:
    # 判定と同じ優先順位(人感センサーがあればそれ、無ければ開閉センサー)。
    df = _itami_motion(df_sensor)
    if df.empty:
        df = _itami_contact(df_sensor)
    at = _format_moment(_latest_timestamp(df), now)
    return None if at is None else f"最終検知 {at}"


def describe_parking(df_sensor: pd.DataFrame, now: datetime) -> str | None:
    at = _format_moment(latest_parking_motion_at(df_sensor), now)
    return None if at is None else f"最終検知 {at}"


def latest_parking_motion_at(df_sensor: pd.DataFrame):
    """駐車場カメラが最後に動きを捉えた時刻(無ければ None)。

    システムページ(`services/dashboard_page_service.py`)の鮮度一覧が、カード判定と
    同じ抽出(`_parking_camera_motion`)を使うための公開ラッパー。
    """
    return _latest_timestamp(_parking_camera_motion(df_sensor))


def camera_motion_log(df_sensor: pd.DataFrame) -> pd.DataFrame:
    """見守りページの防犯ログに出す、カメラが動きを捉えた行(新しい順)。

    以前は`security_logs`テーブルを読んでいたが、このテーブルへ書き込むコードが
    リポジトリ内のどこにも存在せず(camera_monitor.pyが動体検知を記録するのは
    `device_records`のみ)、防犯ログは常に「表示できるデータがありません」の
    ままだった。カード側の判定(`get_camera_status`)と同じ抽出(`_camera_motion`)を
    正のデータとして使う。
    """
    return _camera_motion(df_sensor)


def describe_camera(
    df_sensor: pd.DataFrame, now: datetime, recording: list[dict[str, Any]] | None = None
) -> str | None:
    """どのカメラが捉えたのか。値の「5分前に検知」だけでは場所が分からない。

    `recording`(`get_camera_status` と同じ)で止まっているカメラがあるときは、いつから止まっているか
    (最新の録画の開始時刻)を優先して出す。全台録画中のときは「最終検知 …」として動体検知を添える。
    """
    if recording:
        stopped = [r for r in recording if r.get("state") != "ok"]
        if stopped:
            latest = stopped[0].get("latest")
            at = _format_moment(latest.replace(tzinfo=None) if latest is not None else None, now.replace(tzinfo=None))
            return "今日・昨日の録画がありません" if at is None else f"最新の録画 {at}"
    df_cam = _camera_motion(df_sensor)
    at = _format_moment(_latest_timestamp(df_cam), now)
    if at is None:
        return None
    prefix = "最終検知 " if recording else ""
    if "friendly_name" not in df_cam.columns:
        return f"{prefix}{at}"
    name = df_cam.iloc[0]["friendly_name"]
    return f"{prefix}{at}" if pd.isna(name) else f"{prefix}{name} {at}"


def get_cost_status(monthly_cost: int | None) -> tuple[str, str]:
    """今月の電気代。取得に失敗したとき(None)は「0 円」と区別できるよう灰色の「取得できません」にする。"""
    if monthly_cost is None:
        return "⚪ 取得できません", "theme-gray"
    return f"{monthly_cost:,} 円", "theme-blue"


def describe_cost(monthly_cost: int | None, last_month_cost: int | None) -> str | None:
    """先月の同じ時点と比べる。比較対象が無い(初月・取得失敗)ときは出さない。"""
    if monthly_cost is None or not last_month_cost:
        return None
    diff = monthly_cost - last_month_cost
    sign = "+" if diff > 0 else ""
    return f"先月同日 {last_month_cost:,}円 ({sign}{diff:,})"


def describe_server(disk: dict[str, float] | None, memory: dict[str, float] | None = None) -> str | None:
    """メモリ・ディスクの使用率(取れたものだけ)。値は「正常/メモリ不足」なので数字はここに出す。"""
    parts = []
    if memory and "percent" in memory:
        parts.append(f"メモリ {int(memory['percent'])}%")
    if disk and "percent" in disk:
        parts.append(f"ディスク {int(disk['percent'])}%")
    return "・".join(parts) or None


def describe_nas(nas_data: pd.Series | None) -> str | None:
    if nas_data is None:
        return None
    try:
        free_gb = nas_data["free_gb"]
    except KeyError:
        return None
    if free_gb is None or pd.isna(free_gb):
        return None
    return f"空き {int(free_gb):,}GB"


def _climate_rows(df_sensor: pd.DataFrame, location: str) -> pd.DataFrame:
    """拠点の温湿度の記録(新しい順)。判定(`get_climate_status`)と補足(`describe_climate`)が共有する。

    材料はカードの他の判定と同じ `df_sensor`(`load_sensor_data` の新しい順の結合結果。
    `switchbot_meter_logs` の行は `temperature_celsius`/`humidity_percent` を持つ)。専用の
    クエリ・キャッシュは持たない。件数上限(`MOBILE_SENSOR_ROW_LIMIT`)の外に出るほど古い
    記録は「データなし」になるが、その頃には `CLIMATE_MAX_AGE_MIN` も超えている想定。
    同じ拠点に複数の機器があるときは、どれでも一番新しい記録を使う。
    """
    required = {"timestamp", "device_id", "device_name", "temperature_celsius"}
    if df_sensor.empty or not required.issubset(df_sensor.columns):
        return df_sensor.iloc[0:0]
    df = df_sensor[df_sensor["temperature_celsius"].notna()]
    if df.empty:
        return df
    # 拠点の判定は機器ごとに1回だけ(5分おきの記録で同じ機器の行が大量に並ぶため)
    pairs = df[["device_id", "device_name"]].drop_duplicates()
    locations = {
        (i, n): climate_location(i, n) for i, n in zip(pairs["device_id"], pairs["device_name"])
    }
    keep = [locations[(i, n)] == location for i, n in zip(df["device_id"], df["device_name"])]
    return df[keep].sort_values("timestamp", ascending=False)


def _climate_latest(df_sensor: pd.DataFrame, location: str, now: datetime) -> tuple[pd.Series, float] | None:
    """拠点の最新の記録と、その経過分数。`CLIMATE_MAX_AGE_MIN` より古い・無いときは None。"""
    rows = _climate_rows(df_sensor, location)
    if rows.empty:
        return None
    latest = rows.iloc[0]
    age_min = (now - latest["timestamp"]).total_seconds() / 60
    if age_min > CLIMATE_MAX_AGE_MIN:
        return None
    return latest, age_min


def _valid_humidity(value: Any) -> float | None:
    """湿度。取れなかったときは0.0%で保存される(`switchbot_power_monitor`・`nature_remo_monitor`)ため、
    0以下は欠損とみなす(`analysis_service.load_hourly_climate` と同じ規則)。"""
    if value is None or pd.isna(value) or float(value) <= 0:
        return None
    return float(value)


def get_climate_status(df_sensor: pd.DataFrame, location: str, now: datetime) -> tuple[str, str]:
    """拠点の今の温度・湿度(例: `19.3℃ / 43%`)と色。

    - 記録が無い・`CLIMATE_MAX_AGE_MIN`(24時間)より古い → `⚪ データなし`(灰色)
    - `CLIMATE_STALE_MIN`(30分)より古い → 値は出すが灰色(補足で「更新が止まっています」)
    - それ以外は温度で色分け: 30℃以上=赤、15℃未満=青、その間=緑
    - 湿度が欠損(0以下)なら `--%`(前の記録の湿度は混ぜない。何分前の値かがずれるため)
    """
    found = _climate_latest(df_sensor, location, now)
    if found is None:
        return "⚪ データなし", "theme-gray"
    latest, age_min = found
    temp = float(latest["temperature_celsius"])
    humidity = _valid_humidity(latest.get("humidity_percent"))
    value = f"{temp:.1f}℃ / " + ("--%" if humidity is None else f"{humidity:.0f}%")
    if age_min > CLIMATE_STALE_MIN:
        return value, "theme-gray"
    if temp >= CLIMATE_HOT_C:
        return value, "theme-red"
    if temp < CLIMATE_COLD_C:
        return value, "theme-blue"
    return value, "theme-green"


def describe_climate(df_sensor: pd.DataFrame, location: str, now: datetime) -> str | None:
    """何分前の値か。古いときは「更新が止まっています」、乾燥しているときは「乾燥」を添える。"""
    found = _climate_latest(df_sensor, location, now)
    if found is None:
        return None
    latest, age_min = found
    at = format_relative_time(latest["timestamp"], now)
    if age_min > CLIMATE_STALE_MIN:
        return f"{at}の値(更新が止まっています)"
    humidity = _valid_humidity(latest.get("humidity_percent"))
    if humidity is not None and humidity < CLIMATE_DRY_PCT:
        return f"{at}・乾燥"
    return at


def build_climate_cards(df_sensor: pd.DataFrame, now: datetime) -> list[StatusCard]:
    """拠点別の温湿度カード(`CLIMATE_SITES` の順)。タップすると「くらし」ページのヒートマップでその拠点を開く。"""
    return [
        StatusCard(
            title,
            *get_climate_status(df_sensor, name, now),
            tab="life",
            group="climate",
            sub=describe_climate(df_sensor, name, now),
            anchor=f"?hm_loc={key}&hm_metric=temp#climate-heatmap",
        )
        for name, key, title in CLIMATE_SITES
    ]


def build_status_cards(
    now: datetime,
    df_sensor: pd.DataFrame,
    nas_data: pd.Series | None,
    memory: dict[str, float] | None,
    monthly_cost: int | None,
    last_month_cost: int | None = None,
    disk: dict[str, float] | None = None,
    recording: list[dict[str, Any]] | None = None,
) -> list[StatusCard]:
    """渡された材料から、並べる順にカードを組み立てる(取得は行わない)。

    並び順は「スマホで上から見たい順」= 利用頻度で決めてある(`CARD_GROUPS`)。
    見守り(実家・自宅・駐車場・カメラ)→ 室温(伊丹・高砂・長岡京)→ くらし → システムの順で、
    毎回は見ないシステム系が最後に来る。順番を変えるとどの位置に何があるかの
    記憶が無効になるので、変えるときは(#829でStreamlit側は廃止済みのため)
    ここが唯一の定義元であることを保ったまま変えること。

    `last_month_cost`・`disk` は補足表示(`sub`)にだけ使う。渡さなければ補足が
    出ないだけで、カードの値と色は変わらない。

    `monthly_cost` が None(取得失敗)なら電気代カードは灰色の「取得できません」になる。
    `recording`(カメラごとの録画の状態)を渡すと「🎥 カメラ」カードが録画の状態を示す
    (`get_camera_status` を参照。None なら従来どおり最後の動体検知)。
    """
    taka_val, taka_theme = get_takasago_status(df_sensor, now)
    itami_val, itami_theme = get_itami_status(df_sensor, now)
    parking_val, parking_theme = get_parking_status(df_sensor, now)
    camera_val, camera_theme = get_camera_status(df_sensor, now, recording)
    cost_val, cost_theme = get_cost_status(monthly_cost)
    server_val, server_theme = get_server_status(memory)
    nas_val, nas_theme = get_nas_status_simple(nas_data)

    # `tab` は「このカードの詳細が載っているタブ」。軽量ページはこれを使って
    # カード自体をリンクにする(異常に気づいてから詳細を開くまでを1タップにする)。
    # 見守りグループの4枚はすべて `tab="watch"` の同じページを指すため、`anchor` で
    # ページ内のどの場所(セクション)に連れて行くかを分ける(#829後の見直し。
    # 以前は`anchor`が無く、4枚のどれを押しても同じページ先頭=カメラ映像に
    # 飛ぶだけで、実質どのカードも同じ場所にしか行けなかった)。
    return [
        StatusCard("👵 高砂 (実家)", taka_val, taka_theme, tab="watch", group="watch",
                   sub=describe_takasago(df_sensor, now), anchor="#takasago-log"),
        StatusCard("🏠 伊丹 (自宅)", itami_val, itami_theme, tab="watch", group="watch",
                   sub=describe_itami(df_sensor, now), anchor="#itami-log"),
        StatusCard("🚗 駐車場", parking_val, parking_theme, tab="watch", group="watch",
                   sub=describe_parking(df_sensor, now),
                   anchor=f"?camera={PARKING_CAMERA_ID}#camera-section"),
        StatusCard("🎥 カメラ", camera_val, camera_theme, tab="watch", group="watch",
                   sub=describe_camera(df_sensor, now, recording), anchor="#camera-section"),
        *build_climate_cards(df_sensor, now),
        StatusCard("💰 今月の電気代", cost_val, cost_theme, tab="life", group="life",
                   sub=describe_cost(monthly_cost, last_month_cost)),
        StatusCard("🖥️ サーバー", server_val, server_theme, tab="sys", group="sys",
                   sub=describe_server(disk, memory)),
        StatusCard("🗄️ NAS", nas_val, nas_theme, tab="sys", group="sys",
                   sub=describe_nas(nas_data), anchor="#nas-history"),
    ]


# === 軽量ページ用の取得(TTLキャッシュ付き) ===
# `unified_server` は Streamlit を使わないため(#829)、`@st.cache_data` の代わりに
# 同じTTLの小さなメモを自前で持つ。読み取り専用なので、並行制御の前提
# (CLAUDE.md「単一プロセス前提」)には触れない。

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, loader: Callable[[], Any], ttl: float | None = None) -> Any:
    """`ttl`(省略時は`STATUS_CACHE_TTL_SEC`)の間は前回の値を返す。

    取得に失敗した場合は例外を伝播させず None を返す(1枚のカードの取得失敗で
    ページ全体を落とさない)。失敗はキャッシュしないので、次の要求で再試行する。
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < (STATUS_CACHE_TTL_SEC if ttl is None else ttl):
            return cached[1]

    try:
        value = loader()
    except Exception as e:  # noqa: BLE001 (1枚のカードの取得失敗でページ全体を落とさない)
        logger.warning(f"{key} の取得に失敗しました: {e}")
        return None

    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def clear_status_cache() -> None:
    """TTLを待たずにキャッシュを捨てる(テスト・明示的な再取得用)。"""
    with _cache_lock:
        _cache.clear()


# くらしページの電気代詳細(日別推移)で遡る日数。
DAILY_COST_HISTORY_DAYS = 14


class DashboardMaterials(NamedTuple):
    """ダッシュボードの各ページ(ホーム・見守り・くらし・システム)が共有する材料。

    `get_cached_materials()` が1回のTTL(`STATUS_CACHE_TTL_SEC`)内で使い回すため、
    同じリクエストで複数ページぶんの材料を集めても、DB・スクレイピングの回数は
    増えない(`_cached`のキーはページに関わらず共通)。
    """
    df_sensor: pd.DataFrame
    nas_data: pd.Series | None
    nas_history: pd.DataFrame
    memory: dict[str, float] | None
    disk: dict[str, float] | None
    monthly_cost: int | None
    last_month_cost: int | None
    daily_cost_rows: list[tuple[Any, int]]


def get_cached_materials() -> DashboardMaterials:
    """DB・スクレイピングから材料を集める(TTLキャッシュ付き)。取得はすべて読み取りで、
    いずれかが失敗しても他の材料は使える(`_cached`が個別に None を返すだけ)。
    """
    empty = pd.DataFrame()

    df_sensor = _cached("sensor", lambda: analysis_service.load_sensor_data(limit=MOBILE_SENSOR_ROW_LIMIT))
    nas_data = _cached("nas", analysis_service.load_nas_status)
    nas_history = _cached("nas_history", analysis_service.load_nas_history)
    memory = _cached("memory", analysis_service.get_memory_usage)
    monthly_cost = _cached("cost", analysis_service.calculate_monthly_cost_cumulative)
    last_month_cost = _cached("cost_last_month", analysis_service.calculate_last_month_cost_same_point)
    disk = _cached("disk", analysis_service.get_disk_usage)
    daily_cost_rows = _cached(
        "daily_cost_rows", lambda: analysis_service.calculate_daily_cost_series(DAILY_COST_HISTORY_DAYS)
    )

    return DashboardMaterials(
        df_sensor=df_sensor if df_sensor is not None else empty,
        nas_data=nas_data,
        nas_history=nas_history if nas_history is not None else empty,
        memory=memory,
        disk=disk,
        # 取得失敗(None)は 0 円に丸めない(カードが「取得できません」と出し分ける)
        monthly_cost=monthly_cost,
        daily_cost_rows=daily_cost_rows or [],
        last_month_cost=last_month_cost,
    )


# === ホームの外部リンク(ファミクエ・あさノート)の稼働チェック ===

# あさノート(外部サービス)へのチェックのタイムアウト(秒)。ホームの表示を待たせないよう短くする。
ASA_NOTE_HEALTH_TIMEOUT_SEC = 3


def check_quest_app_health() -> bool:
    """ファミクエが使える状態か。次の両方を満たすとき True。

    - ビルド成果物 `config.QUEST_DIST_DIR/index.html` がある(無いと `/quest` が404になる)。
    - DBに届く(`quest_users`を読み取れる。マイグレーション失敗・DB破損・恒久ロックで
      `/api/quest/data` が500になる状態を検知する)。

    ファミクエは`unified_server`自身が配信するため、自分自身へのHTTPは使わず
    プロセス内で確認する。例外は握りつぶして False(=異常)にする。
    """
    try:
        if not os.path.isfile(os.path.join(config.QUEST_DIST_DIR, "index.html")):
            return False
        with get_ro_connection() as conn:
            conn.execute("SELECT 1 FROM quest_users LIMIT 1").fetchall()
        return True
    except Exception as e:  # noqa: BLE001 (チェック失敗=異常として表示する)
        logger.warning(f"ファミクエの稼働チェックに失敗しました: {e}")
        return False


def check_asa_note_health(url: str) -> bool:
    """あさノート(外部サービス)が応答するか。最終的なHTTPステータスが200〜399なら True。
    接続失敗・タイムアウト・4xx/5xx は False。本文は読まない。"""
    try:
        with requests.get(url, timeout=ASA_NOTE_HEALTH_TIMEOUT_SEC, stream=True) as res:
            return 200 <= res.status_code < 400
    except requests.exceptions.RequestException as e:
        logger.warning(f"あさノートの稼働チェックに失敗しました: {type(e).__name__}: {e}")
        return False


def collect_link_health(asa_note_url: str, yoru_note_url: str | None = None) -> dict[str, bool]:
    """ホームの外部リンクの稼働状況(`{"quest": bool, "asa_note": bool}`。`yoru_note_url`を
    渡したときだけ `"yoru_note"` も入る)。`STATUS_CACHE_TTL_SEC`のTTLキャッシュ付き。
    よるノートもあさノートと同じ外部(Vercel)サービスなので同じチェック関数を使う。"""
    quest = _cached("link_health_quest", check_quest_app_health)
    asa_note = _cached(f"link_health_asa_note:{asa_note_url}", lambda: check_asa_note_health(asa_note_url))
    health = {"quest": bool(quest), "asa_note": bool(asa_note)}
    if yoru_note_url:
        yoru_note = _cached(f"link_health_yoru_note:{yoru_note_url}", lambda: check_asa_note_health(yoru_note_url))
        health["yoru_note"] = bool(yoru_note)
    return health


def get_nas_history(days: int) -> pd.DataFrame:
    """システムページのNAS容量グラフ用に、直近`days`日の履歴を返す(期間ごとのTTLキャッシュ付き)。
    期間は呼び出し側(`dashboard_router`)が7/30/90に絞るので、キャッシュのキーは3種類に収まる。
    取得失敗は空のDataFrame(ページ全体を落とさない)。"""
    df = _cached(f"nas_history:{days}", lambda: analysis_service.load_nas_history(days=days))
    return df if df is not None else pd.DataFrame()


def get_sensor_data_for_day(day: date) -> pd.DataFrame:
    """見守りページのログの日付フィルタ用に、指定日(JST)のセンサーデータを全件返す。

    `get_cached_materials()`の`df_sensor`は直近`MOBILE_SENSOR_ROW_LIMIT`件までなので、
    それより古い日は含まれない。日付指定時だけDBから範囲取得する。日付ごとに
    キーが増えて`_cache`が肥大しないよう、このデータはキャッシュしない
    (読み取り専用の1回きりの取得で、閲覧のたびに走るのは日付を指定したときだけ)。
    """
    try:
        return analysis_service.load_sensor_data(day=day)
    except Exception as e:  # noqa: BLE001 (取得失敗でページ全体を落とさない)
        logger.warning(f"{day} のセンサーデータの取得に失敗しました: {e}")
        return pd.DataFrame()


def get_sensor_data_today() -> pd.DataFrame:
    """見守りページの活動ヒートマップ用に、今日(JST)のセンサーデータを全件返す。

    `get_cached_materials()`の`df_sensor`は直近`MOBILE_SENSOR_ROW_LIMIT`件までで、温湿度計などの
    定期記録が多いと今日の早い時間帯が欠けるため、今日分だけ別に取る。固定キーで
    `STATUS_CACHE_TTL_SEC`だけキャッシュする(日付入りのキーにすると日ごとにDataFrameが
    溜まり続けるため。日付をまたいだ直後の最大TTL秒は前日のデータが返りうるが、
    呼び出し側は表示日で絞るので、その間は「記録なし」になるだけ)。
    """
    today = get_now_jst().date()
    df = _cached("sensor_today", lambda: analysis_service.load_sensor_data(day=today))
    return df if df is not None else pd.DataFrame()


# ホームの温湿度ヒートマップ。5分おきの記録を1時間ごとに平均するだけなので、ホームの
# 自動更新(60秒)より長くキャッシュして、閲覧のたびの集計を避ける。
CLIMATE_CACHE_TTL_SEC = 600
CLIMATE_DAYS = 30


def get_hourly_climate() -> pd.DataFrame:
    """過去`CLIMATE_DAYS`日(今日を含む)の温湿度を、日付×時間×機器の平均で返す(10分キャッシュ)。
    取得に失敗したときは空のDataFrame。"""
    start = get_now_jst().date() - timedelta(days=CLIMATE_DAYS - 1)
    df = _cached(f"climate:{start.isoformat()}", lambda: analysis_service.load_hourly_climate(start), ttl=CLIMATE_CACHE_TTL_SEC)
    return df if df is not None else pd.DataFrame()


def get_hourly_activity(build: Callable[[pd.DataFrame], dict]) -> dict:
    """過去`CLIMATE_DAYS`日の動きを、`build`(開閉・動体の記録のDataFrame → 集計済みの辞書)で集計して返す(10分キャッシュ)。

    DataFrameは保持せず集計結果の軽い辞書だけをキャッシュする。集計の中身(今日の動きと同じ数え方)は
    `dashboard_page_service`にあり、そちらを import すると循環するため、呼び出し側から`build`を渡す。
    取得・集計に失敗したときは空の辞書。
    """
    start = get_now_jst().date() - timedelta(days=CLIMATE_DAYS - 1)
    value = _cached(
        f"activity:{start.isoformat()}",
        lambda: build(analysis_service.load_activity_events(start)),
        ttl=CLIMATE_CACHE_TTL_SEC,
    )
    return value if isinstance(value, dict) else {}


# === NAS上のファイルを読む取得(時間制限付き) ===
# 録画の状態・カメラの写真はNAS上のファイル一覧から作る。NAS(CIFS)は不調時に応答が返らないまま
# 待たされることがあり、そのまま呼ぶとホームの表示ごと固まる。別スレッドで実行して
# `NAS_READ_TIMEOUT_SEC` で打ち切り、間に合わなければ「分からない」(None)として扱う。
# ワーカー数を絞ってあるのは、固まった読み取りが溜まり続けてスレッドを食い潰さないため
# (埋まっている間の新しい要求は待ち行列で時間切れになり、同じく None になる)。
NAS_READ_TIMEOUT_SEC = 3.0
NAS_READ_CACHE_TTL_SEC = 120
_nas_read_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dash-nas-read")


def cached_with_timeout(
    key: str, loader: Callable[[], Any], *, ttl: float = NAS_READ_CACHE_TTL_SEC, timeout: float = NAS_READ_TIMEOUT_SEC
) -> Any:
    """`loader` を別スレッドで実行し、`timeout` 秒以内に終わらなければ None(失敗はキャッシュしない)。"""
    return _cached(key, lambda: _nas_read_pool.submit(loader).result(timeout=timeout), ttl=ttl)


def get_camera_recording_states() -> list[dict[str, Any]] | None:
    """カメラごとの録画の状態(`recording_status_service.camera_recording_states`)。
    NAS未マウント・時間切れ・取得失敗は None(カードは最後の動体検知の表示に戻る)。"""
    return cached_with_timeout("recording_states", recording_status_service.camera_recording_states)


# === ホーム最上部の「いまの状態」 ===

class HomeAlert(NamedTuple):
    """ホームのサマリーに出す1件。`href` はその詳細が載っている場所(無ければ None)。"""
    text: str
    href: str | None = None


def build_home_alerts(
    cards, *, dashboard_path: str, health: dict[str, Any] | None = None
) -> list[HomeAlert]:
    """「確認が必要なこと」の一覧を作る。空なら「すべて正常」。

    - 赤のカード(`theme-red`)を、カードの並び順のまま1件ずつ出す。黄・灰・青は出さない
      (黄は「しばらく動きがない」程度で留守のたびに付き、毎回埋まるとサマリーが意味を失うため)。
    - `health`(`read_health_result()` の結果)に、家の状態に関わる異常(`HEALTH_HOME_KEYS`)が
      あれば1件にまとめて出す。更新の遅れ・構成の差分など管理者向けの項目はホームには出さない
      (システムページには全件出る)。
    """
    alerts = [
        HomeAlert(f"{_strip_leading_emoji(card.title)}: {_strip_leading_emoji(card.value)}", card_detail_href(card, dashboard_path))
        for card in cards
        if card.theme == "theme-red" and not card.value_is_html
    ]
    if health and not health.get("stale"):
        labels = [a["label"] for a in health.get("anomalies", []) if a.get("key") in HEALTH_HOME_KEYS]
        if labels:
            alerts.append(HomeAlert(
                "定期チェック: " + "・".join(labels), f"{dashboard_path.rstrip('/')}/sys#health-check"
            ))
    return alerts


def _strip_leading_emoji(text: str) -> str:
    """先頭の絵文字・記号(🟢/🔴/👵 等)と空白を落とす。サマリーは文字だけで読めるようにするため。"""
    i = 0
    while i < len(text) and not (text[i].isalnum() or text[i] in "(（「"):
        i += 1
    return text[i:] or text


def render_home_alerts_html(alerts: list[HomeAlert]) -> str:
    """ホーム最上部のサマリー。異常なしは緑の1行、ありは件数と各項目(タップで詳細へ)。"""
    if not alerts:
        return '<p class="alerts alerts-ok home-summary">✅ すべて正常です</p>'
    items = "".join(
        "<li>" + (
            f'<a href="{html.escape(a.href)}">{html.escape(a.text)}</a>' if a.href else html.escape(a.text)
        ) + "</li>"
        for a in alerts
    )
    return (
        '<div class="alerts alerts-bad home-summary" role="status">'
        f"<strong>⚠️ {len(alerts)}件、確認が必要です</strong><ul>{items}</ul></div>"
    )


# === 定期チェック(health_watch)の結果 ===
# `monitors/health_watch.py`(毎時cron)が検知結果をこのファイルへ書く。以前は結果がDiscordにしか
# 出ず、Discordを見ない家族には「録画が止まっている」「保存装置が外れている」が伝わらなかった。
HEALTH_RESULT_FILENAME = ".health_watch_last_result"
# 毎時実行なので、これより古ければ「チェック自体が止まっている」とみなす(分)。
HEALTH_RESULT_STALE_MIN = 150
# チェック名 → 画面に出す短い名前。
HEALTH_CHECK_LABELS: dict[str, str] = {
    "service": "サーバーの停止",
    "api": "サーバーの応答",
    "journal": "システムログのエラー",
    "app_logs": "アプリのログのエラー",
    "disk": "ディスクの空き",
    "memory": "メモリ不足",
    "nas": "保存装置(NAS)の接続",
    "recording": "カメラの録画",
    "deploy_config": "実機の設定の差分",
    "quest_master": "クエスト定義の差分",
    "orphaned_rows": "データの不整合",
    "repo_behind": "更新の取り込み遅れ",
    "stale_code": "再起動待ちの更新",
    "python_version": "Pythonの版の差",
}
# ホームのサマリーに出す(家の状態に関わる)チェック。それ以外は管理者向けでシステムページだけに出す。
HEALTH_HOME_KEYS: frozenset[str] = frozenset({"service", "api", "disk", "memory", "nas", "recording"})


def _as_jst(value: datetime) -> datetime:
    """aware ならJSTへ変換し、naive ならJSTの時刻とみなす。"""
    return _JST.localize(value) if value.tzinfo is None else value.astimezone(_JST)


def health_result_path() -> str:
    return os.path.join(config.LOG_DIR, HEALTH_RESULT_FILENAME)


def read_health_result(now: datetime | None = None) -> dict[str, Any] | None:
    """定期チェックの最新結果を読む。まだ記録が無い・壊れているときは None。

    戻り値: `{"checked_at": datetime, "stale": bool, "anomalies": [{"key","label","text"}],
    "history": [{"at": datetime, "labels": [str]}]}`。`stale` はチェック自体が
    `HEALTH_RESULT_STALE_MIN` 分以上動いていないこと。
    """
    try:
        raw = state_file.read_json(health_result_path())
    except Exception as e:  # noqa: BLE001 (LOG_DIR の解決失敗等でページを落とさない)
        logger.warning(f"定期チェックの結果を読めませんでした: {e}")
        return None
    if not isinstance(raw, dict):
        return None
    try:
        checked_at = _as_jst(datetime.fromisoformat(raw["checked_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    now_jst = _as_jst(now or get_now_jst())
    anomalies = [
        {"key": str(a.get("key", "")), "label": HEALTH_CHECK_LABELS.get(str(a.get("key", "")), str(a.get("key", ""))),
         "text": str(a.get("text", ""))}
        for a in raw.get("anomalies", []) if isinstance(a, dict)
    ]
    history = []
    for h in raw.get("history", []):
        try:
            at = _as_jst(datetime.fromisoformat(h["at"]))
        except (KeyError, TypeError, ValueError):
            continue
        history.append({"at": at, "labels": [HEALTH_CHECK_LABELS.get(str(k), str(k)) for k in h.get("keys", [])]})
    return {
        "checked_at": checked_at,
        "stale": (now_jst - checked_at).total_seconds() / 60 > HEALTH_RESULT_STALE_MIN,
        "anomalies": anomalies,
        "history": history,
    }


# === 今日の天気(ホームの1行) ===

def get_today_weather() -> dict[str, Any] | None:
    """今日(JST)の天気と最高/最低気温。`weather_history`(`monitors/weather_monitor.py` が毎日書く)から読む。
    記録が無い・取得失敗は None(ホームはその行を出さない)。10分キャッシュ。"""
    today = get_now_jst().date().isoformat()

    def _load() -> dict[str, Any] | None:
        with get_ro_connection() as conn:
            row = conn.execute(
                "SELECT weather_desc, max_temp, min_temp FROM weather_history WHERE date = ? AND location = ?",
                (today, config.WEATHER_LOCATION_NAME),
            ).fetchone()
        if row is None or (row[1] is None and row[2] is None):
            return None
        return {"desc": row[0], "max_temp": row[1], "min_temp": row[2]}

    # 固定キー(日付入りにすると日ごとに溜まる)。日付をまたいだ直後の最大10分は前日の値が残りうる。
    return _cached("weather_today", _load, ttl=CLIMATE_CACHE_TTL_SEC)


def render_today_weather_html(weather: dict[str, Any] | None) -> str:
    """「今日の天気: 晴れ・最高 18℃ / 最低 9℃」。記録が無ければ空文字。"""
    if not weather:
        return ""
    parts = []
    if weather.get("desc"):
        parts.append(str(weather["desc"]))
    temps = []
    if weather.get("max_temp") is not None:
        temps.append(f"最高 {float(weather['max_temp']):.0f}℃")
    if weather.get("min_temp") is not None:
        temps.append(f"最低 {float(weather['min_temp']):.0f}℃")
    if temps:
        parts.append(" / ".join(temps))
    if not parts:
        return ""
    return f'<p class="today-weather">今日の天気: {html.escape("・".join(parts))}</p>'


def collect_status_cards(now: datetime | None = None) -> tuple[list[StatusCard], datetime]:
    """材料を集めてカードを組み立て、(カード, 取得時刻)を返す。ホームページ用。"""
    now = now or get_now_jst()
    materials = get_cached_materials()

    cards = build_status_cards(
        now,
        materials.df_sensor,
        materials.nas_data,
        materials.memory,
        materials.monthly_cost,
        last_month_cost=materials.last_month_cost,
        disk=materials.disk,
        recording=get_camera_recording_states(),
    )
    return cards, now


# === ページ組み立て共通の定数 ===
# HTML自体の組み立ては `services/dashboard_page_service.py`(Streamlit不使用)が担う。
# ここに置くのは、ホームページ(`routers/dashboard_router.py`)と自動更新フラグメントの
# 両方が参照する、ページ組み立てに依存しない値だけ。

# ページの自動更新間隔(秒)。表示側のキャッシュTTLと同じにしてある。
MOBILE_PAGE_REFRESH_SEC = STATUS_CACHE_TTL_SEC

# 自動更新で差し替える範囲を囲む要素のid。
STATUS_SECTION_ID = "status"
