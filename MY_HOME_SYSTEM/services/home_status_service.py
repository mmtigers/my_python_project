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
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple

import pandas as pd
import pytz
from core.utils import get_now_jst

from services import analysis_service

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

# 軽量ページ側のキャッシュTTL(秒)。Streamlit 側(`views/dashboard/common.py` の
# DASHBOARD_CACHE_TTL_SEC)と揃えてある。センサーの書き込み間隔(5〜10分)より
# 十分短いので表示の鮮度は実質劣化しない。
STATUS_CACHE_TTL_SEC = 60

# ダッシュボードで読むセンサー行数。カードの判定・見守りページのログ表示に
# 必要なのが「直近」と「今日」だけのため、多くは読まない。
MOBILE_SENSOR_ROW_LIMIT = 3000

# 見守りページの防犯ログで読む行数。表には先頭50件しか出さないが、
# 「最近の異常」を取りこぼさない程度の余裕を持たせる。
SECURITY_LOG_ROW_LIMIT = 200

# カード1枚のCSS。ホームページと各サブページ(`services/dashboard_page_service.py`)が共有する。
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
    /* カードが1枚だけのグループ(ファミクエ)。auto-fit は1枚だとその1枚を行いっぱいに
       引き伸ばすため、上下のグループの「2列のリズム」から外れて間延びして見える。
       列を2つに固定して、スマホでは上下のカードとちょうど同じ幅・同じ左端にする
       (`minmax(0, ...)` なのは、極端に狭い画面で横にはみ出さないため)。
       画面が広いときは1列ぶんが広くなりすぎるので、カード側で頭打ちにする。 */
    .status-grid-solo {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .status-grid-solo > .status-card {
        max-width: 260px;
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
# 見守り(留守・在宅・駐車場・カメラ)を最初に、毎回は見ないシステム系を最後に置く。
# グループの見出しは、9枚が1つの塊に見えて目的のカードを探しにくくなるのを防ぐ。
#
# #829: ファミクエ(承認待ち件数)のカードは廃止した。ダッシュボード内にファミクエの
# 状態表示を残さず、外部リンクカードのみにするという方針変更のため
# (詳細は routers/dashboard_router.py の新しいページ構成を参照)。
CARD_GROUPS: tuple[tuple[str, str], ...] = (
    ("watch", "👀 見守り"),
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
    href = f"{dashboard_path.rstrip('/')}/{card.tab}"
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
        return "🚗 いま動きあり", "theme-blue"
    if diff_m < 60:
        return f"🚗 {int(diff_m)}分前に検知", "theme-blue"
    if diff_m < 24 * 60:
        return f"🚗 {int(diff_m / 60)}時間前に検知", "theme-blue"
    return "🚗 24時間 検知なし", "theme-gray"


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
    nas_data: pd.Series | None,
    memory: dict[str, float] | None,
    monthly_cost: int,
    last_month_cost: int | None = None,
    disk: dict[str, float] | None = None,
) -> list[StatusCard]:
    """渡された材料から、並べる順にカードを組み立てる(取得は行わない)。

    並び順は「スマホで上から見たい順」= 利用頻度で決めてある(`CARD_GROUPS`)。
    見守り(実家・自宅・駐車場・カメラ)→ くらし → システムの順で、
    毎回は見ないシステム系が最後に来る。順番を変えるとどの位置に何があるかの
    記憶が無効になるので、変えるときは(#829でStreamlit側は廃止済みのため)
    ここが唯一の定義元であることを保ったまま変えること。

    `last_month_cost`・`disk` は補足表示(`sub`)にだけ使う。渡さなければ補足が
    出ないだけで、カードの値と色は変わらない。
    """
    taka_val, taka_theme = get_takasago_status(df_sensor, now)
    itami_val, itami_theme = get_itami_status(df_sensor, now)
    parking_val, parking_theme = get_parking_status(df_sensor, now)
    camera_val, camera_theme = get_camera_status(df_sensor, now)
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
                   sub=describe_camera(df_sensor, now), anchor="#camera-section"),
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


class DashboardMaterials(NamedTuple):
    """ダッシュボードの各ページ(ホーム・見守り・くらし・システム)が共有する材料。

    `get_cached_materials()` が1回のTTL(`STATUS_CACHE_TTL_SEC`)内で使い回すため、
    同じリクエストで複数ページぶんの材料を集めても、DB・スクレイピングの回数は
    増えない(`_cached`のキーはページに関わらず共通)。
    """
    df_sensor: pd.DataFrame
    df_security_log: pd.DataFrame
    nas_data: pd.Series | None
    memory: dict[str, float] | None
    disk: dict[str, float] | None
    monthly_cost: int
    last_month_cost: int | None


def get_cached_materials() -> DashboardMaterials:
    """DB・スクレイピングから材料を集める(TTLキャッシュ付き)。取得はすべて読み取りで、
    いずれかが失敗しても他の材料は使える(`_cached`が個別に None を返すだけ)。
    """
    empty = pd.DataFrame()

    df_sensor = _cached("sensor", lambda: analysis_service.load_sensor_data(limit=MOBILE_SENSOR_ROW_LIMIT))
    df_security_log = _cached(
        "security_log", lambda: analysis_service.load_generic_data("security_logs", limit=SECURITY_LOG_ROW_LIMIT)
    )
    nas_data = _cached("nas", analysis_service.load_nas_status)
    memory = _cached("memory", analysis_service.get_memory_usage)
    monthly_cost = _cached("cost", analysis_service.calculate_monthly_cost_cumulative)
    last_month_cost = _cached("cost_last_month", analysis_service.calculate_last_month_cost_same_point)
    disk = _cached("disk", analysis_service.get_disk_usage)

    return DashboardMaterials(
        df_sensor=df_sensor if df_sensor is not None else empty,
        df_security_log=df_security_log if df_security_log is not None else empty,
        nas_data=nas_data,
        memory=memory,
        disk=disk,
        monthly_cost=monthly_cost or 0,
        last_month_cost=last_month_cost,
    )


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
