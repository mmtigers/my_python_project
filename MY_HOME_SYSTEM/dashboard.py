# MY_HOME_SYSTEM/dashboard.py
import logging
import traceback
from datetime import datetime
import pytz
import streamlit as st

# 自作モジュール
import common
import config
from services import analysis_service

# Viewコンポーネント
from views.dashboard import (
    common as view_common,
    summary,
    quest_tab,
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

def main():
    # --- サイドバー設定 ---
    with st.sidebar:
        st.header("設定")
        if st.button("🔄 データを更新"):
            st.cache_data.clear()
            st.rerun()
        
        # 共通CSSの適用
        st.markdown(view_common.CUSTOM_CSS, unsafe_allow_html=True)
        
        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        logger.info(f"Dashboard Rendering... ({now.strftime('%H:%M:%S')})")

    try:
        # メイン画面にもCSS適用
        st.markdown(view_common.CUSTOM_CSS, unsafe_allow_html=True)
        now = datetime.now(pytz.timezone("Asia/Tokyo"))

        # --- データ読み込み (Service層へ委譲) ---
        df_sensor = analysis_service.load_sensor_data(limit=10000)
        df_child = analysis_service.load_generic_data(config.SQLITE_TABLE_CHILD)
        df_poop = analysis_service.load_generic_data(config.SQLITE_TABLE_DEFECATION)
        df_food = analysis_service.load_generic_data(config.SQLITE_TABLE_FOOD)
        df_car = analysis_service.load_generic_data(config.SQLITE_TABLE_CAR)
        df_security_log = analysis_service.load_generic_data("security_logs", limit=100)
        df_security_log = analysis_service.apply_friendly_names(df_security_log)
        df_bicycle = analysis_service.load_bicycle_data(limit=3000)
        nas_data = analysis_service.load_nas_status()

        # --- AIレポート表示 ---
        report = analysis_service.load_ai_report()
        if report is not None:
            # タイムゾーン処理は Service/Pandas で行われている前提だが念のため変換
            ts = report["timestamp"]
            if isinstance(ts, str):
                tz_jst = pytz.timezone("Asia/Tokyo")
                if "T" in ts:
                    report_time = datetime.fromisoformat(ts).astimezone(tz_jst)
                else:
                    # L-L2 (#410): 以前はこの分岐で常にdatetime.now()(現在時刻)に
                    # フォールバックしており、レポートの実際の生成時刻に関わらず
                    # 「たった今」の報告であるかのように表示されていた。
                    # 保存規約(core.utils.get_now_iso)導入前の旧フォーマット
                    # ("YYYY-MM-DD HH:MM:SS")として明示的にパースし、JSTとして
                    # localizeする。
                    report_time = tz_jst.localize(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            else:
                report_time = ts
            
            time_str = report_time.strftime("%H:%M")
            hour = report_time.hour
            icon = "☀️" if 5 <= hour < 11 else ("🕛" if 11 <= hour < 17 else "🌙")
            
            with st.expander(f"{icon} セバスチャンからの報告 ({time_str}) - タップして読む", expanded=False):
                st.markdown(report["message"].replace("\n", "  \n"))

        # --- サマリー (トップ) 表示 ---
        summary.render_summary(now, df_sensor, df_car, df_bicycle, nas_data)

        # --- タブ切り替え ---
        tabs = st.tabs([
            "⚔️ クエスト",
            "🚃 電車遅延",
            "📸 防犯カメラ",
            "💡 電力・環境",
            "🌡️ 気温詳細",
            "🏥 健康管理",
            "👵 高砂実家",
            "📝 ログ分析",
            "📊 トレンド",
            "🔧 システム管理",
            "🚲 駐輪場",
        ])

        (
            tab_quest, tab_train, tab_photo, tab_elec, tab_temp, 
            tab_health, tab_taka, tab_log, tab_trends, tab_sys, tab_bicycle
        ) = tabs

        # --- 各タブのレンダリング (View層へ委譲) ---
        with tab_quest:
            quest_tab.render()
        with tab_train:
            misc_tab.render_traffic()
        with tab_photo:
            misc_tab.render_photos(df_security_log)
        with tab_elec:
            sensor_tab.render_electricity(df_sensor, now)
        with tab_temp:
            sensor_tab.render_temperature(df_sensor, now)
        with tab_health:
            health_tab.render(df_child, df_poop, df_food)
        with tab_taka:
            sensor_tab.render_takasago(df_sensor)
        with tab_log:
            log_tab.render_logs(df_sensor)
        with tab_trends:
            log_tab.render_trends()
        with tab_sys:
            log_tab.render_system()
        with tab_bicycle:
            misc_tab.render_bicycle(df_bicycle)

    except Exception as e:
        err_msg = f"📉 Dashboard Error: {e}"
        logger.error(err_msg)
        try:
            # Discordへエラー通知
            common.send_push(
                [{"type": "text", "text": err_msg}],
                target="discord",
                channel="error",
            )
        except Exception:
            pass
        st.error("システムエラーが発生しました。ログを確認してください。")
        # L-L5 (#410): traceback.format_exc()を画面表示していると、内部の
        # ファイルパスや設定値がLAN内の閲覧者に露出する。画面には汎用メッセージ
        # のみを表示し、詳細はログにのみ残す。
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    main()