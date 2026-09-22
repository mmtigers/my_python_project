# MY_HOME_SYSTEM/services/home_status_service.py
"""「家のいまの状況」(サマリーカード)の算出と、その最小表示。

このモジュールが1箇所に集めているもの:
    - 各カードの判定ロジック(実家の動き・在宅・炊飯器・サーバー等)
    - カード1枚のHTML組み立て(XSS対策を含む。Issue #378)
    - カードのCSS

なぜ View 層(`views/dashboard/`)から出したか:
    判定ロジックは以前 Streamlit の View 層にあり、`unified_server.py` 側からは
    使えなかった(`import streamlit` を持ち込むとサーバーが巻き添えになる)。
    スマートフォン向けの軽量ページ(`/dashboard/m`)は、Streamlit を介さずに
    同じ内容を出すため、同じ判定を2つ書くことになっていた。

    このモジュールは **Streamlit を import しない**。Streamlit 側
    (`views/dashboard/summary.py`)もサーバー側(`routers/dashboard_router.py`)も
    ここを参照することで、「片方だけ直して食い違う」状態を防ぐ。
"""
import html
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple

import config
import pandas as pd
from core.utils import get_now_jst

from services import analysis_service

logger = logging.getLogger(__name__)

# 軽量ページ側のキャッシュTTL(秒)。Streamlit 側(`views/dashboard/common.py` の
# DASHBOARD_CACHE_TTL_SEC)と揃えてある。センサーの書き込み間隔(5〜10分)より
# 十分短いので表示の鮮度は実質劣化しない。
STATUS_CACHE_TTL_SEC = 60

# 軽量ページで読む行数。Streamlit 側(10,000行)より小さいのは、カードの判定に
# 必要なのが「直近」と「今日」だけのため。
MOBILE_SENSOR_ROW_LIMIT = 3000

# カード1枚のCSS。Streamlit 側の CUSTOM_CSS と軽量ページの両方がこれを使う
# (どちらかだけ直して見た目が食い違うのを防ぐ)。
STATUS_CARD_CSS = """
    /* --- ステータスカード --- */
    /* スマホでは横3枚固定だと1枚あたりが潰れて値が読めなくなるため、
       CSS Grid で「入るだけ並べて自動で折り返す」方式にする
       (auto-fit + minmax: スマホ幅では2列、タブレット〜PCでは3〜5列になる)。 */
    .status-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 8px;
        margin-bottom: 8px;
    }
    .status-card {
        padding: 10px 6px;
        border-radius: 12px;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        /* 固定heightだと値や補足が2〜3行に折り返すカードで文字が溢れるため min-height にする */
        min-height: 92px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }
    /* 軽量ページではカード自体が詳細タブへのリンク(<a>)になる。
       ブラウザ既定の下線・リンク色が付くと、テーマ色(theme-green 等)で表している
       「状態」が読み取りにくくなるため打ち消す。見た目は <div> のときと同じ。 */
    a.status-card {
        text-decoration: none;
        -webkit-tap-highlight-color: rgba(0,0,0,0.08);
    }
    a.status-card:active {
        /* 押したことが分かるように少しだけ沈ませる(タップの手応え) */
        transform: scale(0.98);
    }
    .status-title {
        font-size: 0.8rem; color: #555; margin-bottom: 5px; font-weight: bold; opacity: 0.8;
    }
    .status-value {
        font-size: 1.1rem; font-weight: bold; line-height: 1.25; white-space: normal;
        word-break: break-word;
    }
    /* 値の下の補足(前回値・前日比・最終検知時刻)。主役は値なので小さく薄く置く。 */
    .status-sub {
        font-size: 0.7rem; font-weight: normal; line-height: 1.3; margin-top: 4px;
        opacity: 0.75; white-space: normal; word-break: break-word;
    }
    .theme-green { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
    .theme-yellow { background-color: #fffde7; color: #f9a825; border: 1px solid #fff9c4; }
    .theme-red { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
    .theme-blue { background-color: #e3f2fd; color: #1565c0; border: 1px solid #bbdefb; }
    .theme-gray { background-color: #f5f5f5; color: #757575; border: 1px solid #e0e0e0; }

    /* カードのグループ見出し。カード自体より小さく薄くして、
       「読むもの」ではなく「並びの区切り」として見えるようにする。 */
    .group-title {
        font-size: 0.8rem;
        font-weight: bold;
        color: #666;
        margin: 14px 0 6px;
        letter-spacing: 0.04em;
    }
"""


# === ダッシュボードのタブ定義 ===
# ここに置いてある理由:
#     各カードは「どのタブに詳細があるか」(`StatusCard.tab`)を持つ。軽量ページは
#     その情報からカードを `?tab=...` のリンクにするので、タブのキーを知る必要が
#     ある。定義が `dashboard.py` 側にあると軽量ページ(Streamlit を持たない
#     `unified_server` 側)からは読めないため、Streamlit を import しないここに置き、
#     `dashboard.py` はこれを読み込んで使う。
#
# スマホ対応の再設計: 以前はサマリーを常時最上部に出したうえでタブが10個
# (クエスト/電車遅延/防犯カメラ/電力・環境/気温詳細/健康管理/高砂実家/
#  ログ分析/システム管理/駐輪場)あり、スマートフォンでは
#   - どのタブを開いてもサマリーを越えるスクロールが必要
#   - タブ列が画面幅の数倍になり、目的のタブを探せない
# という状態だった。用途で束ね直し、サマリーも「ホーム」タブに入れてある。
# (当初は5つ。「🚃 おでかけ」は中身の電車運行情報・駐輪場を退役させたため撤去した)
#
# キーは `?tab=` のクエリパラメータに入る値でもある
# (`dashboard.py` の `_render_tab_selector`)。
DASHBOARD_TABS: tuple[tuple[str, str], ...] = (
    ("home", "🏠 ホーム"),
    ("watch", "👀 見守り"),
    ("life", "💡 くらし"),
    ("sys", "🔧 システム"),
)
DASHBOARD_TAB_KEYS: tuple[str, ...] = tuple(key for key, _ in DASHBOARD_TABS)

# family-quest(PWA)への導線。`unified_server.py` が `/quest` にSPAをマウントしている。
# ここに置いてあるのはタブ定義と同じ理由で、Streamlit を import しない
# `dashboard.py` 以外(軽量ページ・カードの組み立て)からも使うため。
QUEST_APP_PATH = "/quest"

# === カードの並びとグループ ===
# 並び順は「スマホで開いたときに上から見たい順」= 利用頻度で決めてある。
# 見守り(留守・在宅・車・カメラ)を最初に、毎回は見ないシステム系を最後に置く。
# グループの見出しは、9枚が1つの塊に見えて目的のカードを探しにくくなるのを防ぐ。
CARD_GROUPS: tuple[tuple[str, str], ...] = (
    ("watch", "👀 見守り"),
    ("quest", "⚔️ ファミクエ"),
    ("life", "💡 くらし"),
    ("sys", "🔧 システム"),
)
CARD_GROUP_LABELS: dict[str, str] = dict(CARD_GROUPS)


class StatusCard(NamedTuple):
    """サマリーに並べる1枚のステータスカード。

    `value_is_html`: `value` に意図的なHTML断片(色付けの`<span>`・改行の`<br>`等)を
    含める呼び出し元だけ True にする。詳細は `render_status_card_html` を参照。
    `tab`: このカードの詳細が載っているタブのキー(`DASHBOARD_TABS`)。軽量ページは
    これを使ってカード自体を `?tab=...` へのリンクにする。
    `sub`: 値の下に小さく出す補足(前回値・前日比・最終検知時刻など)。「いまの値」
    だけでは高いのか低いのか判断できないカードに、比べる相手を添えるためのもの。
    常にHTMLエスケープされる(`value` と違い、HTML断片は渡せない)。
    `href`: 詳細がダッシュボードのタブではなく別のアプリにあるカード(ファミクエ)
    のための、直接のリンク先。指定すると `tab` より優先される。
    `group`: カードが属する見出し(`CARD_GROUPS` のキー)。よく見るものから順に
    並べたうえで、どこからどこまでが同じ用途かを見出しで示すために使う。
    """
    title: str
    value: str
    theme: str
    value_is_html: bool = False
    tab: str | None = None
    sub: str | None = None
    href: str | None = None
    group: str | None = None


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

    `href` を渡すとカード全体がリンク(`<a>`)になる。軽量ページ専用で、Streamlit 側は
    渡さない — Streamlit ではリンクを踏むとページ全体が再読み込みになり
    (セッションが作り直され数秒かかる)、同じ移動を `dashboard.py` の
    「詳しく見る」ボタン(再実行だけで切り替わる)が既に担っているため。
    """
    safe_title = html.escape(title)
    safe_value = value if value_is_html else html.escape(value)
    # 改行・インデントを入れずに1行で返すこと。Streamlit 側の `st.markdown` は本文に
    # `textwrap.dedent()` をかけてからMarkdownとして解釈するため、整形用の空白が
    # 残っていると「空白だけの行がHTMLブロックを終端し、続く字下げがコードブロックに
    # なる」ことで、2枚目以降のカードが生のタグ文字列として画面に出る(#807)。
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

    `href` を持つカード(詳細がダッシュボードの外にあるファミクエ)はそれを、
    そうでなければ `tab` からダッシュボードのタブへのURLを作る。

    `dashboard_path` は閲覧中のオリジンからのルート相対パス(例: `/dashboard/`)。
    固定URLを埋めると、LAN内のIP・Cloudflare 経由の公開ドメインのどちらか一方でしか
    繋がらなくなる(`href` 側も同じ理由でルート相対にすること)。
    """
    if card.href is not None:
        return card.href
    if card.tab is None:
        return None
    return f"{dashboard_path}?tab={card.tab}"


# 「気になること」として拾うテーマ。赤(異常)を先に、黄(注意)を後に並べる。
ALERT_THEMES: tuple[str, ...] = ("theme-red", "theme-yellow")


def summarize_alerts(cards) -> list[StatusCard]:
    """いま気にすべきカードだけを、赤 → 黄 の順で返す。

    カードの並び自体は動かさない。「左上が高砂」と位置で覚えている画面で順番が
    入れ替わると、かえって読み違えるため(並べ替えではなく要約で解決する)。
    """
    return [card for theme in ALERT_THEMES for card in cards if card.theme == theme]


def render_alerts_html(cards, *, dashboard_path: str | None = None) -> str:
    """要約行のHTML。気になることが無いときも同じ高さの行を出す(画面が跳ねない)。"""
    alerts = summarize_alerts(cards)
    if not alerts:
        return '<p class="alerts alerts-ok">✅ 気になることはありません</p>'

    items = []
    for card in alerts:
        label = html.escape(card.title)
        href = None if dashboard_path is None else card_detail_href(card, dashboard_path)
        items.append(label if href is None else f'<a href="{html.escape(href)}">{label}</a>')
    return f'<p class="alerts alerts-warn">⚠️ 気になること: {"、".join(items)}</p>'


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


def render_status_grid_html(cards, *, dashboard_path: str | None = None) -> str:
    """カードを自動折り返しのグリッドにまとめたHTMLを返す。

    `group` を持つカードには見出しが付く(`CARD_GROUPS`)。9枚が1つの塊に見えると
    目的のカードを探すのに時間がかかるため、用途の変わり目を示す。

    `dashboard_path` を渡すと、詳細のあるカードがその行き先へのリンクになる
    (軽量ページ専用。理由は `render_status_card_html` の docstring を参照)。
    """
    blocks = []
    for group_key, group_cards_ in group_cards(cards):
        label = CARD_GROUP_LABELS.get(group_key or "")
        if label:
            blocks.append(f'<h2 class="group-title">{html.escape(label)}</h2>')
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
        blocks.append(f'<div class="status-grid">{cards_html}</div>')
    return "".join(blocks)


# === 各カードの判定 ===
# いずれも副作用を持たない純粋関数。入力(DataFrame・取得済みの値)は呼び出し側が渡す
# (Streamlit 側はキャッシュ付きローダから、サーバー側は下記の collect_status_cards から)。

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
            val = f"🟢 活動中 ({int(diff_m)}分前)"
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
                val = f"🟢 活動中 ({int(diff_c)}分前)"
                theme = "theme-green"
    return val, theme


def get_server_status(memory: dict[str, float] | None) -> tuple[str, str]:
    if memory:
        return f"💻 RAM: {int(memory['percent'])}%", "theme-green" if memory["percent"] < 80 else "theme-red"
    return "⚪ 取得失敗", "theme-gray"


def get_nas_status_simple(nas_data: pd.Series | None) -> tuple[str, str]:
    if nas_data is None:
        return "⚪ データなし", "theme-gray"
    try:
        if nas_data["status_ping"] == "OK":
            return "🗄️ NAS: 稼働中", "theme-green"
        else:
            return "⚠️ NAS: 応答なし", "theme-red"
    except KeyError:
        return "⚠️ NAS: データ異常", "theme-yellow"


def get_quest_status(pending: dict[str, Any] | None) -> tuple[str, str]:
    """ファミクエで承認待ちになっている申請の件数。

    クエストの進捗やランキングではなく承認待ちを出すのは、これだけが
    「見た人が今すぐ動く必要がある」情報だからで、ほかは family-quest(PWA)側で見る。
    待たせている状態を拾ってほしいので、1件でもあれば黄色にして要約行に出す。
    """
    if pending is None:
        return "⚪ 取得失敗", "theme-gray"
    count = int(pending.get("count", 0) or 0)
    if count == 0:
        return "✅ なし", "theme-green"
    return f"⏳ {count}件", "theme-yellow"


def get_car_status(df_car: pd.DataFrame) -> tuple[str, str]:
    if not df_car.empty and df_car.iloc[0]["action"] == "LEAVE":
        return "🚗 外出中", "theme-yellow"
    return "🏠 在宅", "theme-green"


# カメラの動体検知が `device_records` に入るときの `device_type`
# (`monitors/camera_monitor.py` が ONVIF のイベントを受けて書く)。
CAMERA_DEVICE_TYPE = "ONVIF_CAMERA"


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


def get_camera_status(df_sensor: pd.DataFrame, now: datetime) -> tuple[str, str]:
    """カメラが最後に動きを捉えたのはいつか。

    **色は常に情報色(青)かグレーにする**。家族が出入りすれば毎日検知するため、
    赤・黄にすると「気になること」の要約行が毎回埋まって意味を失う。
    「誰かが来たかどうか」は人が見て判断する情報として出すだけにとどめる。
    """
    df_cam = _camera_motion(df_sensor)
    if df_cam.empty:
        return "⚪ データなし", "theme-gray"

    diff_m = (now - df_cam.iloc[0]["timestamp"]).total_seconds() / 60
    if diff_m < 10:
        return "🎥 いま動きあり", "theme-blue"
    if diff_m < 60:
        return f"🎥 {int(diff_m)}分前に検知", "theme-blue"
    if diff_m < 24 * 60:
        return f"🎥 {int(diff_m / 60)}時間前に検知", "theme-blue"
    return "🎥 24時間 検知なし", "theme-gray"


# 炊飯器が「稼働していた」とみなす消費電力(W)。
RICE_COOKER_ON_WATTS = 500


def _rice_cooking_rows(df_sensor: pd.DataFrame) -> pd.DataFrame:
    """炊飯器が稼働していた記録(日付で絞らない)。

    判定(`get_rice_status`, 今日ぶん)と補足表示(`describe_rice`, 前回いつ)が
    同じ条件を共有する。
    """
    if "device_name" not in df_sensor.columns or "power_watts" not in df_sensor.columns:
        return df_sensor.iloc[0:0]
    return df_sensor[
        (df_sensor["device_name"].astype(str).str.contains("炊飯器")) &
        (df_sensor["power_watts"] >= RICE_COOKER_ON_WATTS)
    ]


def get_rice_status(df_sensor: pd.DataFrame, now: datetime) -> tuple[str, str]:
    val = "🍚 炊いてない"
    theme = "theme-red"

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    df_rice = _rice_cooking_rows(df_sensor)
    if not df_rice.empty and not df_rice[df_rice["timestamp"] >= today_start].empty:
        val = "🍚 ご飯あり"
        theme = "theme-green"
    return val, theme


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


def describe_car(df_car: pd.DataFrame, now: datetime) -> str | None:
    at = _format_moment(_latest_timestamp(df_car), now)
    if at is None or "action" not in df_car.columns:
        return None
    return f"{at} に出発" if df_car.iloc[0]["action"] == "LEAVE" else f"{at} に帰宅"


def describe_camera(df_sensor: pd.DataFrame, now: datetime) -> str | None:
    """どのカメラが捉えたのか。値の「5分前に検知」だけでは場所が分からない。"""
    df_cam = _camera_motion(df_sensor)
    at = _format_moment(_latest_timestamp(df_cam), now)
    if at is None:
        return None
    if "friendly_name" not in df_cam.columns:
        return at
    name = df_cam.iloc[0]["friendly_name"]
    return at if pd.isna(name) else f"{name} {at}"


def describe_quest(pending: dict[str, Any] | None, now: datetime) -> str | None:
    """いちばん長く待たせている申請(誰の・いつ)。0件のときは出さない。"""
    if not pending or not pending.get("count"):
        return None
    at = _format_moment(pd.to_datetime(pending.get("oldest_at"), errors="coerce"), now)
    if at is None:
        return None
    name = pending.get("oldest_name")
    return f"{name} {at} から" if name else f"{at} から"


def describe_rice(df_sensor: pd.DataFrame, now: datetime) -> str | None:
    at = _format_moment(_latest_timestamp(_rice_cooking_rows(df_sensor)), now)
    return None if at is None else f"前回 {at}"


def describe_cost(monthly_cost: int, last_month_cost: int | None) -> str | None:
    """先月の同じ時点と比べる。比較対象が無い(初月・取得失敗)ときは出さない。"""
    if not last_month_cost:
        return None
    diff = monthly_cost - last_month_cost
    sign = "+" if diff > 0 else ""
    return f"先月同日 {last_month_cost:,}円 ({sign}{diff:,})"


def describe_server(disk: dict[str, float] | None) -> str | None:
    if not disk or "percent" not in disk:
        return None
    return f"ディスク {int(disk['percent'])}%"


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


def build_status_cards(
    now: datetime,
    df_sensor: pd.DataFrame,
    df_car: pd.DataFrame,
    nas_data: pd.Series | None,
    memory: dict[str, float] | None,
    monthly_cost: int,
    last_month_cost: int | None = None,
    disk: dict[str, float] | None = None,
    pending_quests: dict[str, Any] | None = None,
) -> list[StatusCard]:
    """渡された材料から、並べる順にカードを組み立てる(取得は行わない)。

    並び順は「スマホで上から見たい順」= 利用頻度で決めてある(`CARD_GROUPS`)。
    見守り(実家・自宅・車・カメラ)→ ファミクエ → くらし → システムの順で、
    毎回は見ないシステム系が最後に来る。順番を変えるとどの位置に何があるかの
    記憶が無効になるので、変えるときは軽量ページ・Streamlit の両方が同じ順に
    なること(ここが唯一の定義元であること)を保ったまま変えること。

    `last_month_cost`・`disk` は補足表示(`sub`)にだけ使う。渡さなければ補足が
    出ないだけで、カードの値と色は変わらない。
    """
    taka_val, taka_theme = get_takasago_status(df_sensor, now)
    itami_val, itami_theme = get_itami_status(df_sensor, now)
    car_val, car_theme = get_car_status(df_car)
    camera_val, camera_theme = get_camera_status(df_sensor, now)
    quest_val, quest_theme = get_quest_status(pending_quests)
    rice_val, rice_theme = get_rice_status(df_sensor, now)
    server_val, server_theme = get_server_status(memory)
    nas_val, nas_theme = get_nas_status_simple(nas_data)

    # `tab` は「このカードの詳細が載っているタブ」。軽量ページはこれを使って
    # カード自体をリンクにする(異常に気づいてから詳細を開くまでを1タップにする)。
    # ファミクエだけは詳細がダッシュボードの外(PWA)にあるので `href` を使う。
    return [
        StatusCard("👵 高砂 (実家)", taka_val, taka_theme, tab="watch", group="watch",
                   sub=describe_takasago(df_sensor, now)),
        StatusCard("🏠 伊丹 (自宅)", itami_val, itami_theme, tab="watch", group="watch",
                   sub=describe_itami(df_sensor, now)),
        StatusCard("🚗 車 (伊丹)", car_val, car_theme, tab="watch", group="watch",
                   sub=describe_car(df_car, now)),
        StatusCard("🎥 カメラ", camera_val, camera_theme, tab="watch", group="watch",
                   sub=describe_camera(df_sensor, now)),
        # 見出し(「⚔️ ファミクエ」)と同じ言葉をカード名にすると2行続けて同じに
        # 見えるため、カード側は中身(何を待たせているか)を名前にする。
        StatusCard("📝 承認待ち", quest_val, quest_theme, href=QUEST_APP_PATH, group="quest",
                   sub=describe_quest(pending_quests, now)),
        StatusCard("🍚 炊飯器", rice_val, rice_theme, tab="life", group="life",
                   sub=describe_rice(df_sensor, now)),
        StatusCard("💰 今月の電気代", f"⚡ {monthly_cost:,} 円", "theme-blue", tab="life", group="life",
                   sub=describe_cost(monthly_cost, last_month_cost)),
        StatusCard("🖥️ サーバー", server_val, server_theme, tab="sys", group="sys",
                   sub=describe_server(disk)),
        StatusCard("🗄️ NAS", nas_val, nas_theme, tab="sys", group="sys",
                   sub=describe_nas(nas_data)),
    ]


# === 軽量ページ用の取得(TTLキャッシュ付き) ===
# `unified_server` は Streamlit を持たないため `@st.cache_data` は使えない。
# 同じTTLの小さなメモを自前で持つ。読み取り専用なので、並行制御の前提
# (CLAUDE.md「単一プロセス前提」)には触れない。

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, loader: Callable[[], Any]) -> Any:
    """`STATUS_CACHE_TTL_SEC` の間は前回の値を返す。

    取得に失敗した場合は例外を伝播させず None を返す(1枚のカードの取得失敗で
    ページ全体を落とさない)。失敗はキャッシュしないので、次の要求で再試行する。
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < STATUS_CACHE_TTL_SEC:
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


def collect_status_cards(now: datetime | None = None) -> tuple[list[StatusCard], datetime]:
    """DB・スクレイピングから材料を集めてカードを組み立て、(カード, 取得時刻)を返す。

    Streamlit を介さない軽量ページ(`/dashboard/m`)用。取得はすべて読み取りで、
    いずれかが失敗しても残りのカードは表示する。
    """
    now = now or get_now_jst()
    empty = pd.DataFrame()

    df_sensor = _cached("sensor", lambda: analysis_service.load_sensor_data(limit=MOBILE_SENSOR_ROW_LIMIT))
    df_car = _cached("car", lambda: analysis_service.load_generic_data(config.SQLITE_TABLE_CAR))
    nas_data = _cached("nas", analysis_service.load_nas_status)
    memory = _cached("memory", analysis_service.get_memory_usage)
    monthly_cost = _cached("cost", analysis_service.calculate_monthly_cost_cumulative)
    last_month_cost = _cached("cost_last_month", analysis_service.calculate_last_month_cost_same_point)
    disk = _cached("disk", analysis_service.get_disk_usage)
    pending_quests = _cached("quest", analysis_service.load_pending_quest_approvals)

    cards = build_status_cards(
        now,
        df_sensor if df_sensor is not None else empty,
        df_car if df_car is not None else empty,
        nas_data,
        memory,
        monthly_cost or 0,
        last_month_cost=last_month_cost,
        disk=disk,
        pending_quests=pending_quests,
    )
    return cards, now


# === 軽量ページのHTML ===
# Streamlit を介さない読み取り専用ページ(`/dashboard/m`)。
# 「スマホで見るのは結局このカードだけ」という用途に対して、Streamlit の初期化・
# WebSocket 接続・React の読み込みを丸ごと省く。サーバーが1回のリクエストで
# HTMLを返して終わりなので、回線が細い場所でも開く。
# ダッシュボード本体(Streamlit)は、グラフ・ログ・メンテナンス操作を持つ
# 「詳しく見る側」として残す。

MOBILE_PAGE_TITLE = "おうちの様子"

# ページの自動更新間隔(秒)。表示側のキャッシュTTLと同じにしてある。
MOBILE_PAGE_REFRESH_SEC = STATUS_CACHE_TTL_SEC

_MOBILE_PAGE_BASE_CSS = """
    /* 端末がダークモードなら下の @media 側の配色になることをブラウザに伝える
       (スクロールバー等、こちらで指定しない部分もダーク側に揃う)。 */
    :root { color-scheme: light dark; }
    body {
        margin: 0;
        padding: 12px 12px 32px;
        background: #ffffff;
        color: #222;
        font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif;
    }
    h1 { font-size: 1.25rem; margin: 0 0 2px; }
    .meta { font-size: 0.8rem; color: #666; margin: 0 0 12px; }

    /* 「気になること」の要約行。カードの並びは固定のままにして、赤・黄のカードだけを
       名前で拾って先頭に出す(後ろのほうのカードは、異常でも埋もれていた)。
       異常が無いときも同じ位置に1行出すので、更新のたびに下の内容が跳ねない。 */
    .alerts {
        margin: 0 0 10px;
        padding: 8px 10px;
        border-radius: 10px;
        font-size: 0.85rem;
        line-height: 1.5;
    }
    .alerts-warn { background: #fff3e0; color: #e65100; border: 1px solid #ffe0b2; }
    .alerts-ok { background: #f1f8e9; color: #558b2f; border: 1px solid #dcedc8; }
    .alerts a { color: inherit; font-weight: bold; }

    nav { display: flex; gap: 8px; margin-top: 16px; }
    nav a {
        flex: 1 1 0;
        /* iOS/Android の推奨タップターゲット(44px) */
        min-height: 44px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 12px;
        border: 1px solid #bbdefb;
        background: #e3f2fd;
        color: #1565c0;
        font-weight: bold;
        text-decoration: none;
        font-size: 0.9rem;
    }
"""

# 軽量ページだけのダークモード。
#
# なぜ Streamlit 側(共有の STATUS_CARD_CSS)に入れないか:
#     カードのCSSはダッシュボード本体と共有しているが、本体には Streamlit 自身の
#     テーマ(ハンバーガーメニューで Light を選べる)がある。OSがダークでも本体を
#     Light に固定している場合、共有CSSへ `prefers-color-scheme` を入れると
#     「周りは白いのにカードだけ黒い」状態になる。夜にスマホで見るのは軽量ページ
#     なので、ここだけをダークに対応させ、本体は Streamlit のテーマに任せる。
# 差し替えが失敗したことが分かるようにするCSS(下記スクリプトが付け外しする)。
_MOBILE_PAGE_STALE_CSS = """
    #status.stale { opacity: 0.55; }
    #status.stale::after {
        content: "⚠️ 更新できていません(表示は最後に取得できた内容です)";
        display: block;
        margin-top: 8px;
        font-size: 0.8rem;
        color: #b71c1c;
    }
"""

# 自動更新。ページ全体を読み込み直さず、カードのブロックだけを差し替える。
#
# 以前は `<meta http-equiv="refresh">` による全ページ再読み込みだった。60秒ごとに
# 画面が白く瞬き、スクロール位置も先頭へ戻るため、下のカードを見ている最中に
# 読めなくなることがあった。JS が動く環境ではこちらを使い、動かない環境のために
# `<noscript>` の中に従来の meta refresh を残す(どちらか一方だけが働く)。
#
# `__STATUS_URL__` と `__REFRESH_MS__` は `render_mobile_status_page_html` が
# 差し込む(URLは `json.dumps` を通すのでJS文字列として安全)。
_MOBILE_PAGE_REFRESH_JS = """
(function () {
    var url = __STATUS_URL__;
    var intervalMs = __REFRESH_MS__;
    var inFlight = false;

    function apply(html) {
        var section = document.getElementById("status");
        if (!section) { return; }
        section.outerHTML = html;
    }

    function update() {
        if (inFlight || document.hidden) { return; }
        inFlight = true;
        // Cloudflare Access の内側にあるため Cookie を送る必要がある。
        fetch(url, { credentials: "same-origin", cache: "no-store" })
            .then(function (res) {
                if (!res.ok) { throw new Error("status " + res.status); }
                return res.text();
            })
            .then(function (html) { apply(html); })
            .catch(function () {
                // 取れなかったときは古い表示を消さずに残し、古いことだけを示す
                // (圏外・サーバー再起動の最中に画面が空になると、かえって困る)。
                var section = document.getElementById("status");
                if (section) { section.classList.add("stale"); }
            })
            .then(function () { inFlight = false; });
    }

    setInterval(update, intervalMs);
    // 画面を消している間は更新しないぶん、戻ってきたら即座に取り直す。
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) { update(); }
    });
})();
"""

_MOBILE_PAGE_DARK_CSS = """
    @media (prefers-color-scheme: dark) {
        body { background: #121212; color: #e8e8e8; }
        .meta { color: #9e9e9e; }
        .status-title { color: #cfcfcf; }
        .alerts-warn { background: #3a2a14; color: #ffb74d; border-color: #5c4322; }
        .alerts-ok { background: #1e2a17; color: #aed581; border-color: #33482a; }
        .theme-green { background-color: #1b3a24; color: #a5d6a7; border-color: #2e5c39; }
        .theme-yellow { background-color: #3a3420; color: #ffe082; border-color: #5c5227; }
        .theme-red { background-color: #3d1f22; color: #ef9a9a; border-color: #6b2f35; }
        .theme-blue { background-color: #16304a; color: #90caf9; border-color: #24507a; }
        .theme-gray { background-color: #262626; color: #bdbdbd; border-color: #3a3a3a; }
        nav a { background: #16304a; color: #90caf9; border-color: #24507a; }
        #status.stale::after { color: #ef9a9a; }
    }
"""


# 自動更新で差し替える範囲を囲む要素のid。
STATUS_SECTION_ID = "status"


def render_status_section_html(
    cards,
    fetched_at: datetime,
    *,
    dashboard_path: str | None = None,
    refresh_sec: int = MOBILE_PAGE_REFRESH_SEC,
) -> str:
    """取得時刻・要約行・カードのグリッドをまとめた1ブロック。

    自動更新でここだけを差し替えられるよう、`id` を付けて切り出してある。
    """
    return (
        f'<div id="{STATUS_SECTION_ID}">'
        f'<p class="meta">{fetched_at.strftime("%m/%d %H:%M:%S")} 時点'
        f"・{int(refresh_sec)}秒ごとに自動更新</p>"
        f"{render_alerts_html(cards, dashboard_path=dashboard_path)}"
        f"{render_status_grid_html(cards, dashboard_path=dashboard_path)}"
        "</div>"
    )


def render_mobile_status_page_html(
    cards,
    fetched_at: datetime,
    *,
    manifest_path: str,
    icon_path: str,
    dashboard_path: str,
    quest_path: str,
    status_path: str | None = None,
    refresh_sec: int = MOBILE_PAGE_REFRESH_SEC,
) -> str:
    """軽量ページのHTML全体を組み立てる。

    パス類を引数で受けるのは、このモジュールを配信層(ルーター・中継)から
    独立させておくため(テストもここだけで完結する)。

    `status_path` はカードのブロックだけを返すURL。渡すと自動更新が
    「そこだけ差し替える」方式になり、渡さないと従来どおりページ全体を
    読み込み直す。
    """
    if status_path is None:
        # JS を使わない場合は従来どおり全体を再読み込みする。
        refresh_head = f'<meta http-equiv="refresh" content="{int(refresh_sec)}">'
        refresh_script = ""
    else:
        # JS が動かない環境だけが meta refresh を見る(二重に更新されない)。
        refresh_head = f'<noscript><meta http-equiv="refresh" content="{int(refresh_sec)}"></noscript>'
        refresh_script = (
            "<script>"
            + _MOBILE_PAGE_REFRESH_JS
            .replace("__STATUS_URL__", json.dumps(status_path))
            .replace("__REFRESH_MS__", str(int(refresh_sec) * 1000))
            + "</script>"
        )

    return (
        "<!DOCTYPE html>"
        '<html lang="ja"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{refresh_head}"
        f"<title>{html.escape(MOBILE_PAGE_TITLE)}</title>"
        # マニフェストの取得は既定で認証情報を送らない。このパスは Cloudflare Access の
        # 内側にあるため `use-credentials` が要る(`dashboard_proxy_service` と同じ理由)。
        f'<link rel="manifest" href="{html.escape(manifest_path)}" crossorigin="use-credentials">'
        f'<link rel="apple-touch-icon" href="{html.escape(icon_path)}">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="mobile-web-app-capable" content="yes">'
        '<meta name="theme-color" content="#0d47a1">'
        f"<style>{_MOBILE_PAGE_BASE_CSS}{STATUS_CARD_CSS}"
        f"{_MOBILE_PAGE_STALE_CSS}{_MOBILE_PAGE_DARK_CSS}</style>"
        "</head><body>"
        f"<h1>{html.escape(MOBILE_PAGE_TITLE)}</h1>"
        f"{render_status_section_html(cards, fetched_at, dashboard_path=dashboard_path, refresh_sec=refresh_sec)}"
        "<nav>"
        f'<a href="{html.escape(dashboard_path)}">📊 詳しく見る</a>'
        f'<a href="{html.escape(quest_path)}">⚔️ ファミクエ</a>'
        "</nav>"
        f"{refresh_script}"
        "</body></html>"
    )
