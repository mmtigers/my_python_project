# MY_HOME_SYSTEM/views/dashboard/common.py
import html
import logging
import traceback
from contextlib import contextmanager
from typing import Iterable, NamedTuple

import streamlit as st

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
        /* 左右の既定パディングが広く、グラフ・表の実効幅が削られるため詰める */
        .block-container {{
            padding-top: 1.2rem;
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

        /* 見出しが2〜3行に折り返してスクロール量が増えるのを抑える */
        h1 {{ font-size: 1.5rem !important; }}
        h2 {{ font-size: 1.25rem !important; }}
        h3 {{ font-size: 1.1rem !important; }}

        /* 横方向にページ全体がはみ出すと、縦スクロール時に左右へ揺れて操作しづらい */
        section[data-testid="stMain"] {{ overflow-x: hidden; }}
    }}
</style>
"""


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
    return f"""
    <div class="status-card {theme}">
        <div class="status-title">{safe_title}</div>
        <div class="status-value">{safe_value}</div>
    </div>
    """


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
