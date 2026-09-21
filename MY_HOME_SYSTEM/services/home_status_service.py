# MY_HOME_SYSTEM/services/home_status_service.py
"""「家のいまの状況」(サマリーカード)の算出と、その最小表示。

このモジュールが1箇所に集めているもの:
    - 各カードの判定ロジック(実家の動き・在宅・炊飯器・駐輪場・電車・サーバー等)
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
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, NamedTuple

import config
import pandas as pd
from core.utils import get_now_jst

from services import analysis_service, train_service

logger = logging.getLogger(__name__)

# 軽量ページ側のキャッシュTTL(秒)。Streamlit 側(`views/dashboard/common.py` の
# DASHBOARD_CACHE_TTL_SEC)と揃えてある。センサーの書き込み間隔(5〜10分)より
# 十分短いので表示の鮮度は実質劣化しない。
STATUS_CACHE_TTL_SEC = 60

# 軽量ページで読む行数。Streamlit 側(10,000行)より小さいのは、カードの判定に
# 必要なのが「直近」と「今日」と「前日同時刻」だけのため。
MOBILE_SENSOR_ROW_LIMIT = 3000
MOBILE_BICYCLE_ROW_LIMIT = 3000

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
        /* 固定heightだと値が2〜3行になるカード(駐輪場など)で文字が溢れるため min-height にする */
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
        color: inherit;
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
    .theme-green { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
    .theme-yellow { background-color: #fffde7; color: #f9a825; border: 1px solid #fff9c4; }
    .theme-red { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
    .theme-blue { background-color: #e3f2fd; color: #1565c0; border: 1px solid #bbdefb; }
    .theme-gray { background-color: #f5f5f5; color: #757575; border: 1px solid #e0e0e0; }

    /* 駐輪場カードの前日比。以前は `style='color:#...'` を値のHTMLに直接
       埋めていたが、それだとダークモードで色を差し替えられない(軽量ページの
       ダーク対応は下記 `_MOBILE_PAGE_DARK_CSS`)。クラスにして色はCSS側に置く。 */
    .diff-up { color: #d32f2f; }
    .diff-down { color: #388e3c; }
    .diff-flat { color: #757575; }
    .diff-none { color: #999; }
"""


# === ダッシュボードのタブ定義 ===
# ここに置いてある理由:
#     各カードは「どのタブに詳細があるか」(`StatusCard.tab`)を持つ。軽量ページは
#     その情報からカードを `?tab=...` のリンクにするので、タブのキーを知る必要が
#     ある。定義が `dashboard.py` 側にあると軽量ページ(Streamlit を持たない
#     `unified_server` 側)からは読めないため、Streamlit を import しないここに置き、
#     `dashboard.py` はこれを読み込んで使う。
#
# スマホ対応の再設計: 以前はサマリー9枚を常時最上部に出したうえでタブが10個
# (クエスト/電車遅延/防犯カメラ/電力・環境/気温詳細/健康管理/高砂実家/
#  ログ分析/システム管理/駐輪場)あり、スマートフォンでは
#   - どのタブを開いてもサマリーを越えるスクロールが必要
#   - タブ列が画面幅の数倍になり、目的のタブを探せない
# という状態だった。用途で5つに束ね直し、サマリーも「ホーム」タブに入れてある。
#
# キーは `?tab=` のクエリパラメータに入る値でもある
# (`dashboard.py` の `_render_tab_selector`)。
DASHBOARD_TABS: tuple[tuple[str, str], ...] = (
    ("home", "🏠 ホーム"),
    ("out", "🚃 おでかけ"),
    ("watch", "👀 見守り"),
    ("life", "💡 くらし"),
    ("sys", "🔧 システム"),
)
DASHBOARD_TAB_KEYS: tuple[str, ...] = tuple(key for key, _ in DASHBOARD_TABS)


class StatusCard(NamedTuple):
    """サマリーに並べる1枚のステータスカード。

    `value_is_html`: `value` に意図的なHTML断片(色付けの`<span>`・改行の`<br>`等)を
    含める呼び出し元だけ True にする。詳細は `render_status_card_html` を参照。
    `tab`: このカードの詳細が載っているタブのキー(`DASHBOARD_TABS`)。軽量ページは
    これを使ってカード自体を `?tab=...` へのリンクにする。
    """
    title: str
    value: str
    theme: str
    value_is_html: bool = False
    tab: str | None = None


def render_status_card_html(
    title: str,
    value: str,
    theme: str,
    *,
    value_is_html: bool = False,
    href: str | None = None,
) -> str:
    """
    ステータスカードのHTMLを生成。

    Issue #378: `title`/`value`はそのまま描画されるため、以前はスクレイピング由来・
    DB由来の文字列(quest_title/reward_title等)をそのまま埋め込むと格納型XSSに
    なりえた。`title`は常にHTMLエスケープする。`value`も既定でエスケープするが、
    `get_bicycle_status`のように前日比の色付け等で意図的にHTML断片
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
    return (
        f"{open_tag}"
        f'<div class="status-title">{safe_title}</div>'
        f'<div class="status-value">{safe_value}</div>'
        f"{close_tag}"
    )


def card_detail_href(card: StatusCard, dashboard_path: str) -> str | None:
    """カードの詳細が載っているタブへのURLを返す(タブが無いカードは None)。

    `dashboard_path` は閲覧中のオリジンからのルート相対パス(例: `/dashboard/`)。
    固定URLを埋めると、LAN内のIP・Cloudflare 経由の公開ドメインのどちらか一方でしか
    繋がらなくなる。
    """
    if card.tab is None:
        return None
    return f"{dashboard_path}?tab={card.tab}"


# 「気になること」として拾うテーマ。赤(異常)を先に、黄(注意)を後に並べる。
ALERT_THEMES: tuple[str, ...] = ("theme-red", "theme-yellow")


def summarize_alerts(cards) -> list[StatusCard]:
    """いま気にすべきカードだけを、赤 → 黄 の順で返す。

    9枚の並び自体は動かさない。「左上が高砂」と位置で覚えている画面で順番が
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


def render_status_grid_html(cards, *, dashboard_path: str | None = None) -> str:
    """カードを自動折り返しのグリッド1ブロックにまとめたHTMLを返す。

    `dashboard_path` を渡すと、詳細タブを持つカードがそのタブへのリンクになる
    (軽量ページ専用。理由は `render_status_card_html` の docstring を参照)。
    """
    cards_html = "".join(
        render_status_card_html(
            card.title,
            card.value,
            card.theme,
            value_is_html=card.value_is_html,
            href=None if dashboard_path is None else card_detail_href(card, dashboard_path),
        )
        for card in cards
    )
    return f'<div class="status-grid">{cards_html}</div>'


# === 各カードの判定 ===
# いずれも副作用を持たない純粋関数。入力(DataFrame・取得済みの値)は呼び出し側が渡す
# (Streamlit 側はキャッシュ付きローダから、サーバー側は下記の collect_status_cards から)。

def get_takasago_status(df_sensor: pd.DataFrame, now: datetime) -> tuple[str, str]:
    val = "⚪ データなし"
    theme = "theme-gray"
    if df_sensor.empty or "location" not in df_sensor.columns or "contact_state" not in df_sensor.columns:
        return val, theme

    df_taka = df_sensor[
        (df_sensor["location"] == "高砂") & (df_sensor["contact_state"].isin(["open", "detected"]))
    ]
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


def get_itami_status(df_sensor: pd.DataFrame, now: datetime) -> tuple[str, str]:
    """伊丹（自宅）のステータス判定（修正版）"""
    val = "⚪ データなし"
    theme = "theme-gray"
    required_cols = ["location", "device_type", "movement_state", "contact_state"]
    if df_sensor.empty or not all(col in df_sensor.columns for col in required_cols):
        return val, theme

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

    df_motion = df_sensor[
        (df_sensor["location"] == "伊丹") &
        is_motion_device &
        is_detected
    ].sort_values("timestamp", ascending=False)

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
        df_contact = df_sensor[
            (df_sensor["location"] == "伊丹") & (df_sensor["contact_state"] == "open")
        ].sort_values("timestamp", ascending=False)
        if not df_contact.empty:
            diff_c = (now - df_contact.iloc[0]["timestamp"]).total_seconds() / 60
            if diff_c < 60:
                val = f"🟢 活動中 ({int(diff_c)}分前)"
                theme = "theme-green"
    return val, theme


def get_traffic_status(jr_status: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """取得済みのJR運行情報からカードの表示を決める。

    取得(スクレイピング)は呼び出し側が行う。Streamlit 側はキャッシュ付き
    ラッパー経由、サーバー側は `collect_status_cards` 経由。
    """
    line_g = jr_status["宝塚線"]
    line_a = jr_status["神戸線"]
    # Issue #438: 同一関数内で is_suspended/is_unavailable は .get() を使う一方、
    # is_delay だけ直接インデックスアクセスになっており方針が不統一だった。
    # キーが欠落した応答でも例外にならないよう .get() へ統一する。
    if line_g.get("is_suspended") or line_a.get("is_suspended"):
        return "⛔ 運休発生", "theme-red"
    elif line_g.get("is_delay") or line_a.get("is_delay"):
        return "⚠️ 遅延あり", "theme-yellow"
    elif line_g.get("is_unavailable") or line_a.get("is_unavailable"):
        # Low修正: 取得不可を「平常運転」と偽らず区別する(遅延見逃し防止)
        return "⚪ 情報取得不可", "theme-gray"
    else:
        return "🟢 平常運転", "theme-green"


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


def get_car_status(df_car: pd.DataFrame) -> tuple[str, str]:
    if not df_car.empty and df_car.iloc[0]["action"] == "LEAVE":
        return "🚗 外出中", "theme-yellow"
    return "🏠 在宅", "theme-green"


def get_rice_status(df_sensor: pd.DataFrame, now: datetime) -> tuple[str, str]:
    val = "🍚 炊いてない"
    theme = "theme-red"
    # カラム存在チェック
    if "device_name" not in df_sensor.columns or "power_watts" not in df_sensor.columns:
        return val, theme

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 炊飯器の電力データを検索
    df_rice = df_sensor[
        (df_sensor["device_name"].astype(str).str.contains("炊飯器")) &
        (df_sensor["timestamp"] >= today_start)
    ]

    if not df_rice.empty:
        max_watts = df_rice["power_watts"].max()
        # 500W以上で稼働していれば「ご飯あり」とみなす
        if max_watts is not None and max_watts >= 500:
            val = "🍚 ご飯あり"
            theme = "theme-green"
    return val, theme


def get_bicycle_status(df_bicycle: pd.DataFrame) -> tuple[str, str]:
    if df_bicycle.empty:
        return "⚪ データなし", "theme-gray"

    targets = {
        "JR伊丹駅前(第1)自転車駐車場 (A)": "第1A",
        "JR伊丹駅前(第3)自転車駐車場 (A)": "第3A",
        "JR伊丹駅前(第3)自転車駐車場 (E)": "第3E",
    }

    # タイムゾーン処理
    if not pd.api.types.is_datetime64_any_dtype(df_bicycle["timestamp"]):
        df_bicycle = df_bicycle.copy()
        df_bicycle["timestamp"] = pd.to_datetime(df_bicycle["timestamp"]).dt.tz_convert("Asia/Tokyo")

    latest_df = df_bicycle.sort_values("timestamp", ascending=False).drop_duplicates("area_name")
    details = []
    total_wait = 0
    has_data = False

    for full_name, short_name in targets.items():
        row = latest_df[latest_df["area_name"] == full_name]
        if not row.empty:
            current_val = int(row.iloc[0]["waiting_count"])
            current_time = row.iloc[0]["timestamp"]

            # 前日比計算
            target_time = current_time - timedelta(days=1)
            df_area = df_bicycle[df_bicycle["area_name"] == full_name]
            df_near = df_area[
                (df_area["timestamp"] >= target_time - timedelta(hours=2)) &
                (df_area["timestamp"] <= target_time + timedelta(hours=2))
            ]

            diff_str = ""
            if not df_near.empty:
                nearest_idx = (df_near["timestamp"] - target_time).abs().idxmin()
                past_val = int(df_near.loc[nearest_idx]["waiting_count"])
                diff = current_val - past_val
                if diff > 0:
                    diff_str = f" <span class='diff-up'>(🔺{diff})</span>"
                elif diff < 0:
                    diff_str = f" <span class='diff-down'>(🔻{abs(diff)})</span>"
                else:
                    diff_str = " <span class='diff-flat'>(➡️0)</span>"
            else:
                diff_str = " <span class='diff-none'>(--)</span>"

            details.append(f"{short_name}: <b>{current_val}</b>台{diff_str}")
            total_wait += current_val
            has_data = True
        else:
            details.append(f"{short_name}: -")

    if not has_data:
        return "⚪ データなし", "theme-gray"

    val = (
        "<div style='font-size:0.85rem; line-height:1.4; text-align:left; display:inline-block;'>"
        f"{'<br>'.join(details)}</div>"
    )
    theme = "theme-green" if total_wait == 0 else ("theme-yellow" if total_wait < 10 else "theme-red")
    return val, theme


def build_status_cards(
    now: datetime,
    df_sensor: pd.DataFrame,
    df_car: pd.DataFrame,
    df_bicycle: pd.DataFrame,
    nas_data: pd.Series | None,
    jr_status: dict[str, dict[str, Any]],
    memory: dict[str, float] | None,
    monthly_cost: int,
) -> list[StatusCard]:
    """渡された材料から、並べる順にカードを組み立てる(取得は行わない)。"""
    taka_val, taka_theme = get_takasago_status(df_sensor, now)
    itami_val, itami_theme = get_itami_status(df_sensor, now)
    car_val, car_theme = get_car_status(df_car)
    rice_val, rice_theme = get_rice_status(df_sensor, now)
    bicycle_val, bicycle_theme = get_bicycle_status(df_bicycle)
    traffic_val, traffic_theme = get_traffic_status(jr_status)
    server_val, server_theme = get_server_status(memory)
    nas_val, nas_theme = get_nas_status_simple(nas_data)

    # Issue #378: get_bicycle_status は前日比の色付け(<span>)等を意図的に組み立てて
    # 返すため、HTMLエスケープをスキップする(value_is_html=True)。
    # `tab` は「このカードの詳細が載っているタブ」。軽量ページはこれを使って
    # カード自体をリンクにする(異常に気づいてから詳細を開くまでを1タップにする)。
    return [
        StatusCard("👵 高砂 (実家)", taka_val, taka_theme, tab="watch"),
        StatusCard("🏠 伊丹 (自宅)", itami_val, itami_theme, tab="watch"),
        StatusCard("🚗 車 (伊丹)", car_val, car_theme, tab="watch"),
        StatusCard("🍚 炊飯器", rice_val, rice_theme, tab="life"),
        StatusCard("💰 今月の電気代", f"⚡ {monthly_cost:,} 円", "theme-blue", tab="life"),
        StatusCard("🚲 駐輪場待機", bicycle_val, bicycle_theme, value_is_html=True, tab="out"),
        StatusCard("🚃 JR運行情報", traffic_val, traffic_theme, tab="out"),
        StatusCard("🖥️ サーバー", server_val, server_theme, tab="sys"),
        StatusCard("🗄️ NAS", nas_val, nas_theme, tab="sys"),
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
    df_bicycle = _cached("bicycle", lambda: analysis_service.load_bicycle_data(limit=MOBILE_BICYCLE_ROW_LIMIT))
    nas_data = _cached("nas", analysis_service.load_nas_status)
    jr_status = _cached("jr", train_service.get_jr_traffic_status)
    memory = _cached("memory", analysis_service.get_memory_usage)
    monthly_cost = _cached("cost", analysis_service.calculate_monthly_cost_cumulative)

    cards = build_status_cards(
        now,
        df_sensor if df_sensor is not None else empty,
        df_car if df_car is not None else empty,
        df_bicycle if df_bicycle is not None else empty,
        nas_data,
        jr_status or {"宝塚線": {"is_unavailable": True}, "神戸線": {"is_unavailable": True}},
        memory,
        monthly_cost or 0,
    )
    return cards, now


# === 軽量ページのHTML ===
# Streamlit を介さない読み取り専用ページ(`/dashboard/m`)。
# 「スマホで見るのは結局この9枚」という用途に対して、Streamlit の初期化・
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

    /* 「気になること」の要約行。9枚の並びは固定のままにして、赤・黄のカードだけを
       名前で拾って先頭に出す(JR運行情報は7枚目にあり、異常でも埋もれていた)。
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
        .diff-up { color: #ef9a9a; }
        .diff-down { color: #a5d6a7; }
        .diff-flat { color: #bdbdbd; }
        .diff-none { color: #9e9e9e; }
        nav a { background: #16304a; color: #90caf9; border-color: #24507a; }
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
    refresh_sec: int = MOBILE_PAGE_REFRESH_SEC,
) -> str:
    """軽量ページのHTML全体を組み立てる。

    パス類を引数で受けるのは、このモジュールを配信層(ルーター・中継)から
    独立させておくため(テストもここだけで完結する)。
    """
    return (
        "<!DOCTYPE html>"
        '<html lang="ja"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="refresh" content="{int(refresh_sec)}">'
        f"<title>{html.escape(MOBILE_PAGE_TITLE)}</title>"
        # マニフェストの取得は既定で認証情報を送らない。このパスは Cloudflare Access の
        # 内側にあるため `use-credentials` が要る(`dashboard_proxy_service` と同じ理由)。
        f'<link rel="manifest" href="{html.escape(manifest_path)}" crossorigin="use-credentials">'
        f'<link rel="apple-touch-icon" href="{html.escape(icon_path)}">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="mobile-web-app-capable" content="yes">'
        '<meta name="theme-color" content="#0d47a1">'
        f"<style>{_MOBILE_PAGE_BASE_CSS}{STATUS_CARD_CSS}{_MOBILE_PAGE_DARK_CSS}</style>"
        "</head><body>"
        f"<h1>{html.escape(MOBILE_PAGE_TITLE)}</h1>"
        f"{render_status_section_html(cards, fetched_at, dashboard_path=dashboard_path, refresh_sec=refresh_sec)}"
        "<nav>"
        f'<a href="{html.escape(dashboard_path)}">📊 詳しく見る</a>'
        f'<a href="{html.escape(quest_path)}">⚔️ ファミクエ</a>'
        "</nav>"
        "</body></html>"
    )
