# MY_HOME_SYSTEM/dashboard.py
import logging
import traceback
from datetime import datetime
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


def _render_header_actions() -> None:
    """画面最上部の操作列(更新 / ファミクエへの導線)。

    スマホ対応: 以前「データを更新」ボタンはサイドバーにしか無かったが、
    `initial_sidebar_state="collapsed"` のためスマートフォンでは
    ハンバーガーメニューを開かないと押せず、最も使う操作が最も遠かった。
    メイン画面の先頭に常設する。
    """
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
        # ダッシュボードに同じ内容を二重に持たない(下記「タブ構成」のコメント参照)。
        #
        # ルート相対のリンクにしているのは、このダッシュボードが
        # unified_server.py(8000番)の config.DASHBOARD_BASE_PATH 配下に中継されて
        # 配信されるため。閲覧しているオリジン(LANのIP:8000でも Cloudflare 経由の
        # 公開ドメインでも)の /quest に解決され、config.FRONTEND_URL のような
        # 固定URLを埋めるとLAN外から開いたときに繋がらない。
        st.link_button("⚔️ ファミクエを開く", QUEST_APP_PATH, width="stretch")


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

        # --- データ読み込み (Service層へ委譲) ---
        # Issue #741: Streamlit はウィジェット操作・タブ切替・ページ読み込みの
        # たびにスクリプト全体を再実行するため、ここは「タブを1回切り替える」
        # たびに走る。素の analysis_service を直接呼ぶと毎回 約33,000行を
        # SQLite から読み直し、unified_server 側の書き込みとも競合していた。
        # View層のキャッシュ付きラッパー(TTL 60秒)を経由する。
        df_sensor = view_common.load_sensor_data_cached(limit=10000)
        df_child = view_common.load_generic_data_cached(config.SQLITE_TABLE_CHILD)
        df_poop = view_common.load_generic_data_cached(config.SQLITE_TABLE_DEFECATION)
        df_food = view_common.load_generic_data_cached(config.SQLITE_TABLE_FOOD)
        df_car = view_common.load_generic_data_cached(config.SQLITE_TABLE_CAR)
        df_security_log = view_common.load_generic_data_cached("security_logs", limit=100)
        # 表示名の付与は100行に対する純粋なCPU処理なのでキャッシュに含めない
        # (キャッシュ対象を「DBの読み取り」だけに閉じておく)。
        df_security_log = analysis_service.apply_friendly_names(df_security_log)
        df_bicycle = view_common.load_bicycle_data_cached(limit=3000)
        nas_data = view_common.load_nas_status_cached()

        # Issue #701 (2026-09-19): 以前はここで「セバスチャンからの報告」
        # (ai_report_records の最新1件)を表示していたが、書込側が 2026-07-16 以降
        # 停止しており2か月前の内容を出し続けていたため、オーナー判断で機能ごと退役した。
        # テーブル自体は履歴として残している(マイグレーションでの削除はしない)。

        # --- タブ構成 ---
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
        tab_home, tab_out, tab_watch, tab_life, tab_sys = st.tabs([
            "🏠 ホーム",
            "🚃 おでかけ",
            "👀 見守り",
            "💡 くらし",
            "🔧 システム",
        ])

        # --- 各タブのレンダリング (View層へ委譲) ---
        # Issue #438: 以前はmain()全体を1つのtry/exceptで囲んでおり、いずれか1つの
        # タブの描画で例外が起きるとダッシュボード全体がエラー画面になり、無関係な
        # 他のタブまで巻き込んでいた。セクション単位でview_common.safe_sectionを使い
        # 例外を隔離し、失敗したセクションだけがエラー表示になるようにする。
        with tab_home:
            with view_common.safe_section("サマリー"):
                summary.render_summary(now, df_sensor, df_car, df_bicycle, nas_data)

        with tab_out:
            with view_common.safe_section("電車遅延"):
                misc_tab.render_traffic()
            with st.expander("🚲 駐輪場の待機数", expanded=False):
                with view_common.safe_section("駐輪場"):
                    misc_tab.render_bicycle(df_bicycle)

        with tab_watch:
            with view_common.safe_section("防犯カメラ"):
                misc_tab.render_photos(df_security_log)
            with st.expander("👵 高砂実家のセンサーログ", expanded=False):
                with view_common.safe_section("高砂実家"):
                    sensor_tab.render_takasago(df_sensor)
            with st.expander("🏥 健康管理の記録", expanded=False):
                with view_common.safe_section("健康管理"):
                    health_tab.render(df_child, df_poop, df_food)

        with tab_life:
            with view_common.safe_section("電力・環境"):
                sensor_tab.render_electricity(df_sensor, now)
            with st.expander("🌡️ 気温・湿度の詳細", expanded=False):
                with view_common.safe_section("気温詳細"):
                    sensor_tab.render_temperature(df_sensor, now)

        with tab_sys:
            with view_common.safe_section("リソース状況"):
                log_tab.render_resources()
            st.markdown("---")
            with view_common.safe_section("NAS状態"):
                log_tab.render_nas_status()
            with st.expander("📜 サーバーログ", expanded=False):
                with view_common.safe_section("サーバーログ"):
                    log_tab.render_server_logs()
            with st.expander("📝 センサーログ分析", expanded=False):
                with view_common.safe_section("ログ分析"):
                    log_tab.render_logs(df_sensor)
            with st.expander("🛠️ メンテナンス操作 (再起動・バックアップ)", expanded=False):
                with view_common.safe_section("メンテナンス操作"):
                    log_tab.render_maintenance()

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
