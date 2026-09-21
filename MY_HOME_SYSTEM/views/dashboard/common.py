# MY_HOME_SYSTEM/views/dashboard/common.py
import html
import logging
import traceback
from contextlib import contextmanager
from typing import Iterable, NamedTuple

import pandas as pd
import streamlit as st
from services import analysis_service

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

    /* --- ステータスカード --- */
    /* スマホでは横3枚固定だと1枚あたりが潰れて値が読めなくなるため、
       st.columns ではなく CSS Grid で「入るだけ並べて自動で折り返す」方式にする
       (auto-fit + minmax: スマホ幅では2列、タブレット〜PCでは3〜5列になる)。 */
    .status-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 8px;
        margin-bottom: 8px;
    }}
    .status-card {{
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
    }}
    .status-title {{
        font-size: 0.8rem; color: #555; margin-bottom: 5px; font-weight: bold; opacity: 0.8;
    }}
    .status-value {{
        font-size: 1.1rem; font-weight: bold; line-height: 1.25; white-space: normal;
        word-break: break-word;
    }}
    .theme-green {{ background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }}
    .theme-yellow {{ background-color: #fffde7; color: #f9a825; border: 1px solid #fff9c4; }}
    .theme-red {{ background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }}
    .theme-blue {{ background-color: #e3f2fd; color: #1565c0; border: 1px solid #bbdefb; }}
    .theme-gray {{ background-color: #f5f5f5; color: #757575; border: 1px solid #e0e0e0; }}

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
           `_render_tab_selector` のdocstring参照)。5つ並ぶと390px幅には
           収まらないので、旧タブ列と同じく横スクロールできるようにする。 */
        [data-testid="stSegmentedControl"],
        [data-testid="stButtonGroup"] {{
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
        }}
        [data-testid="stSegmentedControl"]::-webkit-scrollbar,
        [data-testid="stButtonGroup"]::-webkit-scrollbar {{ display: none; }}
        [data-testid="stSegmentedControl"] button,
        [data-testid="stButtonGroup"] button {{
            white-space: nowrap;
            padding-left: 0.55rem;
            padding-right: 0.55rem;
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


class StatusCard(NamedTuple):
    """サマリーに並べる1枚のステータスカード。

    `value_is_html`: `value` に意図的なHTML断片(色付けの`<span>`・改行の`<br>`等)を
    含める呼び出し元だけ True にする。詳細は `render_status_card_html` を参照。
    """
    title: str
    value: str
    theme: str
    value_is_html: bool = False


def render_status_card_html(title: str, value: str, theme: str, *, value_is_html: bool = False) -> str:
    """
    ステータスカードのHTMLを生成。

    Issue #378: `title`/`value`はunsafe_allow_html=True経由でそのまま描画されるため、
    以前はスクレイピング由来・DB由来の文字列(quest_title/reward_title等)を
    そのまま埋め込むと格納型XSSになりえた。`title`は常にHTMLエスケープする。
    `value`も既定でエスケープするが、`views/dashboard/summary.py`の
    `get_bicycle_status`のように前日比の色付け等で意図的にHTML断片
    (`<span>`/`<br>`等)を組み立てて渡す呼び出し元は、`value_is_html=True`を
    指定してエスケープをスキップできる(その場合、`value`の構築元に外部/DB由来の
    生文字列を含めないこと)。
    """
    safe_title = html.escape(title)
    safe_value = value if value_is_html else html.escape(value)
    # 改行・インデントを入れずに1行で返すこと。`st.markdown` は本文に
    # `textwrap.dedent()` をかけてからMarkdownとして解釈するが、`render_status_grid`
    # が組み立てる文字列は先頭行(`<div class="status-grid">`)がインデント0のため
    # 共通インデントが0になり、dedentが何も削らない。結果、カードのHTMLに
    # 「空白だけの行」と「4スペース字下げ」が残り、
    #   - 空白だけの行がHTMLブロックを終端する
    #   - 続く4スペース字下げの行がMarkdownのインデントコードブロックになる
    # ため、2枚目以降のカードが `<div class="status-card...` という生のタグ文字列
    # としてスマホ画面に出ていた(横幅も溢れる)。整形用の空白を一切持たせない。
    return (
        f'<div class="status-card {theme}">'
        f'<div class="status-title">{safe_title}</div>'
        f'<div class="status-value">{safe_value}</div>'
        '</div>'
    )


def render_status_grid(cards: Iterable[StatusCard]) -> None:
    """ステータスカードを自動折り返しのグリッドで1ブロックにまとめて描画する。

    以前は `st.columns(3)` を3段重ねて9枚を並べていたが、Streamlitの列は画面幅が
    足りなくても横並びを維持するため、スマートフォンでは1枚あたり約100pxまで潰れて
    値が読めなかった。列数をCSS(`.status-grid`のauto-fit)側に委ねることで、
    スマホでは2列・PCでは3〜5列に自動で切り替わる。
    """
    cards_html = "".join(
        render_status_card_html(card.title, card.value, card.theme, value_is_html=card.value_is_html)
        for card in cards
    )
    st.markdown(f'<div class="status-grid">{cards_html}</div>', unsafe_allow_html=True)


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
