# MY_HOME_SYSTEM/dashboard.py
import logging
import traceback
from datetime import datetime
from typing import Callable, Dict, Tuple

import pytz
import streamlit as st

# 自作モジュール
from services.notification_service import send_push
import config
from services import analysis_service

# Viewコンポーネント
from views.dashboard import (
    common as view_common,
    summary,
    sensor_tab,
    health_tab,
    misc_tab,
    log_tab
)


# === ロガー設定 ===
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# === 基本設定 ===
st.set_page_config(
    page_title="My Home Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# family-quest(PWA)への導線。`unified_server.py` が `/quest` にSPAをマウントしている。
QUEST_APP_PATH = "/quest"

# === タブ定義 ===
# スマホ対応の再設計: 以前はサマリー9枚を常時最上部に出したうえでタブが10個
# (クエスト/電車遅延/防犯カメラ/電力・環境/気温詳細/健康管理/高砂実家/
#  ログ分析/システム管理/駐輪場)あり、スマートフォンでは
#   - どのタブを開いてもサマリーを越えるスクロールが必要
#   - タブ列が画面幅の数倍になり、目的のタブを探せない
# という状態だった。用途で5つに束ね直し、サマリーも「ホーム」タブに入れる。
#
# クエストタブ(EXPランキング等)は、同じ内容をスマホ最適化済みのPWA
# (family-quest, /quest)が持っており二重管理だったため、ダッシュボードからは
# 撤去して導線(ヘッダーのリンクボタン)だけ残した。
#
# Issue #507: 「📊 トレンド」タブ(app_rankingsテーブル参照)は、書き込み側の
# 収集コードが存在せず(収集用のgoogle-play-scraperもIssue #496で未使用
# パッケージとして削除済み)、migrations/にもテーブル定義が無いため新規構築
# したDBでは永久に「データがありません」としか出ない死んだ機能だった。
# オーナー判断によりUIごと削除した。
#
# キーは `?tab=` のクエリパラメータに入る値でもある(下記 `_render_tab_selector`)。
TABS: Tuple[Tuple[str, str], ...] = (
    ("home", "🏠 ホーム"),
    ("out", "🚃 おでかけ"),
    ("watch", "👀 見守り"),
    ("life", "💡 くらし"),
    ("sys", "🔧 システム"),
)
TAB_LABELS: Dict[str, str] = dict(TABS)
TAB_KEYS: Tuple[str, ...] = tuple(TAB_LABELS)
DEFAULT_TAB_KEY = TAB_KEYS[0]

# タブ選択ウィジェットの `key`。`_jump_to_tab` が session_state 経由で
# 「次の実行で選択されるタブ」を差し替えるため、文字列を1箇所に固定する。
TAB_SELECTOR_STATE_KEY = "dashboard_tab_selector"
# 選択中のタブを保持するクエリパラメータ名。
# これがあることで、
#   - 「🔄 データを更新」(st.rerun)や再接続でホームタブに戻されない
#   - `/dashboard?tab=watch` のようなURLをスマホのホーム画面に置ける
TAB_QUERY_PARAM = "tab"

# === データ読み込みの上限行数 ===
# 「今の状態を見る」用途に必要な直近ぶんだけを読む。ここを増やすとスマホでの
# 初回表示時間とWebSocketの転送量に直結する。
SENSOR_ROW_LIMIT = 10000
BICYCLE_ROW_LIMIT = 3000
SECURITY_LOG_ROW_LIMIT = 100


def _render_header_actions() -> None:
    """画面最上部の操作列(更新 / ファミクエへの導線)。

    スマホ対応: 以前「データを更新」ボタンはサイドバーにしか無かったが、
    `initial_sidebar_state="collapsed"` のためスマートフォンでは
    ハンバーガーメニューを開かないと押せず、最も使う操作が最も遠かった。
    メイン画面の先頭に常設する。
    """
    # Issue: スマホ幅では `views/dashboard/common.py` のモバイルCSSが全ての
    # stHorizontalBlock を 100% 幅へ強制するため、この2ボタンが縦に積まれて
    # ヘッダーが2段になり、タブと本文が画面外へ押し下げられていた。
    # `key` を付けた container は `.st-key-header_actions` クラスを持つので、
    # CSS 側でこの行だけ横並びに戻している(グラフ・表の縦積みは維持する)。
    with st.container(key="header_actions"):
        col_refresh, col_quest = st.columns(2)
        with col_refresh:
            if st.button("🔄 データを更新", width="stretch"):
                # Issue #741: 以前はこの `clear()` の時点で `@st.cache_data` が
                # 1つも存在せず、実際には何も消していなかった(押しても押さなくても
                # 毎回DBを読み直していた)。`view_common` のキャッシュ付きローダを
                # 使うようになり、TTL(60秒)を待たずに捨てる操作として機能する。
                st.cache_data.clear()
                st.rerun()
        with col_quest:
            # クエストの詳細はスマホ最適化済みのPWA(family-quest)側が正で、
            # ダッシュボードに同じ内容を二重に持たない(上記「タブ定義」のコメント参照)。
            #
            # ルート相対のリンクにしているのは、このダッシュボードが
            # unified_server.py(8000番)の config.DASHBOARD_BASE_PATH 配下に中継されて
            # 配信されるため。閲覧しているオリジン(LANのIP:8000でも Cloudflare 経由の
            # 公開ドメインでも)の /quest に解決され、config.FRONTEND_URL のような
            # 固定URLを埋めるとLAN外から開いたときに繋がらない。
            st.link_button("⚔️ ファミクエを開く", QUEST_APP_PATH, width="stretch")

    # スマホでは「いま見ている数字がいつのものか」が分からないと判断に使えない。
    # 表示は最大60秒キャッシュされるため、描画時刻ではなくキャッシュ世代の
    # 取得時刻を出す(`view_common.cache_generation_started_at` のdocstring参照)。
    fetched_at = view_common.cache_generation_started_at()
    st.caption(
        f"データ取得 {fetched_at.strftime('%H:%M:%S')} "
        f"({view_common.format_relative_time(fetched_at)}) "
        f"・最大{view_common.DASHBOARD_CACHE_TTL_SEC}秒キャッシュ"
    )


def _requested_tab() -> str:
    """URLの `?tab=` から表示すべきタブを決める(未指定・不正値はホーム)。"""
    requested = st.query_params.get(TAB_QUERY_PARAM)
    return requested if requested in TAB_LABELS else DEFAULT_TAB_KEY


def _jump_to_tab(tab_key: str) -> None:
    """サマリーの「詳細へ」ボタン等から別タブへ移動するコールバック。

    `st.button(on_click=...)` から呼ばれる前提。コールバックはウィジェットが
    作られる**前**に走るため、ここで選択ウィジェットの session_state を
    書き換えると、同じ再実行の中で移動先タブが描画される(リンクと違って
    ページ全体の再読み込みが起きないので、スマホでも一瞬で切り替わる)。
    """
    st.session_state[TAB_SELECTOR_STATE_KEY] = tab_key
    st.query_params[TAB_QUERY_PARAM] = tab_key


def _render_tab_selector() -> str:
    """タブ選択UIを描画し、選択中のタブキーを返す。

    `st.tabs` を使わない理由(スマホでの体感速度):
        Streamlit の `st.tabs` は**選択されていないタブの中身もすべて実行**する
        (描画結果をクライアント側で隠しているだけ)。そのため「🏠 ホーム」を
        開いただけの1回の描画で、JR運行情報のスクレイピング(HTTP)・
        Yahoo!路線情報のスクレイピング(HTTP)・`journalctl` のサブプロセス起動・
        年間気温の集計SQL・plotlyのグラフ6枚ぶんの生成まで毎回走っていた。
        タブを10個から5個に束ね直した再設計(上記「タブ定義」)は「探しやすさ」を
        直したが、実行される処理量は減っていなかった。
        選択状態をPython側で持てる `st.segmented_control` に置き換え、
        選択中のタブの中身だけを描画する。
    """
    # 初期値は `default=` ではなく session_state 経由で与える。`default=` と
    # session_state(= `_jump_to_tab` が書き込む)が両方あると、Streamlit が
    # スタックトレース付きの警告をログに出すため。
    if TAB_SELECTOR_STATE_KEY not in st.session_state:
        st.session_state[TAB_SELECTOR_STATE_KEY] = _requested_tab()

    selected = st.segmented_control(
        "表示するタブ",
        options=TAB_KEYS,
        format_func=lambda key: TAB_LABELS[key],
        key=TAB_SELECTOR_STATE_KEY,
        label_visibility="collapsed",
    )

    # `st.segmented_control` は選択中の項目をもう一度押すと未選択(None)になる。
    # タブとしては「常にどれか1つが選ばれている」のが正しいので、直前の選択
    # (= クエリパラメータに残っている値)へ戻す。
    active_tab = selected if selected in TAB_LABELS else _requested_tab()

    if st.query_params.get(TAB_QUERY_PARAM) != active_tab:
        st.query_params[TAB_QUERY_PARAM] = active_tab
    return active_tab


def _render_home_tab(now: datetime) -> None:
    """🏠 ホーム: サマリーカードと、各タブへの導線。"""
    with view_common.safe_section("サマリー"):
        summary.render_summary(
            now,
            view_common.load_sensor_data_cached(limit=SENSOR_ROW_LIMIT),
            view_common.load_generic_data_cached(config.SQLITE_TABLE_CAR),
            view_common.load_bicycle_data_cached(limit=BICYCLE_ROW_LIMIT),
            view_common.load_nas_status_cached(),
        )

    # スマホ対応: サマリーで異常に気づいた後、詳細を見るには自分でタブを
    # 探し直す必要があった。カード自体をリンクにするとページ全体が再読み込みに
    # なり(Streamlitのセッションが作り直される)スマホでは数秒かかるため、
    # 再実行だけで切り替わるボタンをカードの直下に置く。
    st.caption("詳しく見る")
    with st.container(key="summary_jump"):
        jump_targets = [key for key in TAB_KEYS if key != DEFAULT_TAB_KEY]
        for col, tab_key in zip(st.columns(len(jump_targets)), jump_targets):
            with col:
                st.button(
                    TAB_LABELS[tab_key],
                    key=f"jump_to_{tab_key}",
                    on_click=_jump_to_tab,
                    args=(tab_key,),
                    width="stretch",
                )


def _render_out_tab(now: datetime) -> None:
    """🚃 おでかけ: 電車の運行状況・ルート、駐輪場。"""
    with view_common.safe_section("電車遅延"):
        misc_tab.render_traffic()

    if view_common.lazy_section("🚲 駐輪場の待機数", key="bicycle"):
        with view_common.safe_section("駐輪場"):
            misc_tab.render_bicycle(view_common.load_bicycle_data_cached(limit=BICYCLE_ROW_LIMIT))


def _render_watch_tab(now: datetime) -> None:
    """👀 見守り: 防犯カメラ、高砂実家、健康管理。"""
    with view_common.safe_section("防犯カメラ"):
        df_security_log = view_common.load_generic_data_cached(
            "security_logs", limit=SECURITY_LOG_ROW_LIMIT
        )
        # 表示名の付与は100行に対する純粋なCPU処理なのでキャッシュに含めない
        # (キャッシュ対象を「DBの読み取り」だけに閉じておく)。
        misc_tab.render_photos(analysis_service.apply_friendly_names(df_security_log))

    if view_common.lazy_section("👵 高砂実家のセンサーログ", key="takasago"):
        with view_common.safe_section("高砂実家"):
            sensor_tab.render_takasago(view_common.load_sensor_data_cached(limit=SENSOR_ROW_LIMIT))

    if view_common.lazy_section("🏥 健康管理の記録", key="health"):
        with view_common.safe_section("健康管理"):
            health_tab.render(
                view_common.load_generic_data_cached(config.SQLITE_TABLE_CHILD),
                view_common.load_generic_data_cached(config.SQLITE_TABLE_DEFECATION),
                view_common.load_generic_data_cached(config.SQLITE_TABLE_FOOD),
            )


def _render_life_tab(now: datetime) -> None:
    """💡 くらし: 電力・環境と、気温・湿度の詳細。"""
    with view_common.safe_section("電力・環境"):
        sensor_tab.render_electricity(view_common.load_sensor_data_cached(limit=SENSOR_ROW_LIMIT), now)

    if view_common.lazy_section("🌡️ 気温・湿度の詳細", key="temperature"):
        with view_common.safe_section("気温詳細"):
            sensor_tab.render_temperature(view_common.load_sensor_data_cached(limit=SENSOR_ROW_LIMIT), now)


def _render_sys_tab(now: datetime) -> None:
    """🔧 システム: リソース・NAS・ログ・メンテナンス操作。"""
    with view_common.safe_section("リソース状況"):
        log_tab.render_resources()
    st.markdown("---")
    with view_common.safe_section("NAS状態"):
        log_tab.render_nas_status()

    if view_common.lazy_section("📜 サーバーログ", key="server_logs"):
        with view_common.safe_section("サーバーログ"):
            log_tab.render_server_logs()

    if view_common.lazy_section("📝 センサーログ分析", key="sensor_logs"):
        with view_common.safe_section("ログ分析"):
            log_tab.render_logs(view_common.load_sensor_data_cached(limit=SENSOR_ROW_LIMIT))

    if view_common.lazy_section("🛠️ メンテナンス操作 (再起動・バックアップ)", key="maintenance"):
        with view_common.safe_section("メンテナンス操作"):
            log_tab.render_maintenance()


# タブキー -> 描画関数。`_render_tab_selector` が返したキーのものだけを呼ぶ。
TAB_RENDERERS: Dict[str, Callable[[datetime], None]] = {
    "home": _render_home_tab,
    "out": _render_out_tab,
    "watch": _render_watch_tab,
    "life": _render_life_tab,
    "sys": _render_sys_tab,
}


def main():
    # --- サイドバー設定 ---
    # スマホではサイドバーが畳まれているため、ここには「PCで細かく見るとき用」の
    # 補助操作だけを置き、主要な操作はメイン画面側(_render_header_actions)に出す。
    with st.sidebar:
        st.header("設定")
        st.caption(
            "主要な操作(データ更新)はメイン画面の先頭にあります。"
            "スマートフォンからは 8000番の /dashboard 経由でアクセスしてください。"
        )

        # 共通CSSの適用
        st.markdown(view_common.CUSTOM_CSS, unsafe_allow_html=True)

        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        logger.info(f"Dashboard Rendering... ({now.strftime('%H:%M:%S')})")

    try:
        # メイン画面にもCSS適用
        st.markdown(view_common.CUSTOM_CSS, unsafe_allow_html=True)
        now = datetime.now(pytz.timezone("Asia/Tokyo"))

        _render_header_actions()
        active_tab = _render_tab_selector()

        # --- 選択中のタブだけをレンダリング (View層へ委譲) ---
        # データの読み込みも各タブの描画関数の中で行う。以前は5タブ分のデータを
        # main() の先頭でまとめて読んでいたが、`st.tabs` をやめて選択中のタブしか
        # 描画しなくなったため、そのタブが使わないテーブルまで読む必要がない。
        #
        # Issue #438: 以前はmain()全体を1つのtry/exceptで囲んでおり、いずれか1つの
        # タブの描画で例外が起きるとダッシュボード全体がエラー画面になり、無関係な
        # 他のタブまで巻き込んでいた。セクション単位でview_common.safe_sectionを使い
        # 例外を隔離し、失敗したセクションだけがエラー表示になるようにする。
        TAB_RENDERERS[active_tab](now)

        # Issue #701 (2026-09-19): 以前はここで「セバスチャンからの報告」
        # (ai_report_records の最新1件)を表示していたが、書込側が 2026-07-16 以降
        # 停止しており2か月前の内容を出し続けていたため、オーナー判断で機能ごと退役した。
        # テーブル自体は履歴として残している(マイグレーションでの削除はしない)。

    except Exception as e:
        err_msg = f"📉 Dashboard Error: {e}"
        logger.error(err_msg)
        try:
            # Discordへエラー通知
            send_push(
                [{"type": "text", "text": err_msg}],
                target="discord",
                channel="error",
            )
        except Exception as notify_err:
            # 通知自体の失敗を握りつぶさず、少なくともログには残す
            # (本体のエラー(logger.error(err_msg))とは別に記録する)。
            logger.warning(f"Discordへのエラー通知にも失敗しました: {notify_err}")
        st.error("システムエラーが発生しました。ログを確認してください。")
        # L-L5 (#410): traceback.format_exc()を画面表示していると、内部の
        # ファイルパスや設定値がLAN内の閲覧者に露出する。画面には汎用メッセージ
        # のみを表示し、詳細はログにのみ残す。
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    main()
