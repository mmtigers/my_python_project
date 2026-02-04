# MY_HOME_SYSTEM/views/dashboard/log_tab.py
import streamlit as st
import pandas as pd
import subprocess
import os
import glob
from datetime import datetime, date
from services import analysis_service

def render_logs(df_sensor: pd.DataFrame):
    """ログ分析タブ"""
    if not df_sensor.empty:
        locs = df_sensor["location"].unique()
        sel = st.multiselect("場所", locs, default=locs)
        st.dataframe(
            df_sensor[df_sensor["location"].isin(sel)][
                ["timestamp", "friendly_name", "location", "contact_state", "power_watts"]
            ].head(200),
            width="stretch",
        )

def render_trends():
    """トレンドタブ"""
    st.title("🌟 最近の流行・トレンド推移")
    dates = analysis_service.load_ranking_dates(limit=3)
    if not dates:
        st.info("データがありません。")
        return

    def render_history_section(title, ranking_type):
        st.subheader(title)
        cols = st.columns(len(dates))
        for i, date_str in enumerate(dates):
            with cols[i]:
                label = "今週" if i == 0 else ("先週" if i == 1 else "先々週")
                st.markdown(f"**{label} ({date_str[5:]})**")
                df = analysis_service.load_ranking_data(date_str, ranking_type)
                if df.empty:
                    st.write("- データなし -")
                    continue
                for _, row in df.iterrows():
                    url = f"https://play.google.com/store/apps/details?id={row['app_id']}"
                    st.markdown(f"{row['rank']}. [{row['title']}]({url})")

    render_history_section("🆓 無料トップ (流行)", "free")
    st.markdown("---")
    render_history_section("💰 売上トップ (人気)", "grossing")

def render_system():
    """システム管理タブ"""
    st.title("🔧 システム管理コックピット")

    st.subheader("🌐 外部接続 (ngrok)")
    urls = analysis_service.get_ngrok_url()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**📱 LINE Bot / Server (Port 8000)**")
        if urls.get("server"): st.success(f"接続OK: {urls['server']}")
        else: st.error("取得失敗")
    with c2:
        st.markdown("**📊 Dashboard (Port 8501)**")
        if urls.get("dashboard"): st.success(f"接続OK: {urls['dashboard']}")
        else: st.warning("取得失敗")

    st.markdown("---")
    st.subheader("💻 リソース状況")
    disk = analysis_service.get_disk_usage()
    if disk:
        st.write(f"**💾 ディスク使用率: {disk['percent']:.1f}%**")
        st.progress(int(disk["percent"]))
    
    st.write("")
    mem = analysis_service.get_memory_usage()
    if mem:
        st.write(f"**🧠 メモリ使用率: {mem['percent']:.1f}%**")
        st.progress(int(mem["percent"]))
    
    st.markdown("---")
    st.subheader("🗄️ NAS 状態")
    nas_data = analysis_service.load_nas_status()
    if nas_data is not None:
        c1, c2, c3 = st.columns(3)
        with c1: st.metric("Ping疎通", f"{'✅' if nas_data['status_ping']=='OK' else '❌'} {nas_data['status_ping']}")
        with c2: st.metric("マウント", f"{'✅' if nas_data['status_mount']=='OK' else '❌'} {nas_data['status_mount']}")
        with c3: st.metric("最終確認", str(nas_data["timestamp"]))
    else:
        st.info("データなし")

    st.markdown("---")
    st.subheader("📜 サーバーログ")
    search_mode = st.radio("検索モード", ["直近のログを表示", "日付を指定して検索"], horizontal=True)
    col_opt1, col_opt2, _ = st.columns([1, 1, 2])
    target_date = None
    lines_val = 50

    with col_opt1:
        if search_mode == "日付を指定して検索": target_date = st.date_input("対象日", date.today())
        else: lines_val = st.selectbox("表示行数", [50, 100, 200, 500], index=0)

    with col_opt2:
        level_opts = {"全て": None, "警告": "warning", "エラー": "err"}
        sel = st.selectbox("ログレベル", list(level_opts.keys()))
        priority = level_opts[sel]

    if st.button("🔄 ログを更新"): st.rerun()
    
    logs = analysis_service.get_system_logs(lines=lines_val, priority=priority, target_date=target_date)
    if not logs: st.info("ログなし")
    else: st.code(logs, language="text")

    st.markdown("---")
    col_reboot, _ = st.columns([1, 2])
    with col_reboot:
        if st.button("🔄 システム再起動"):
            try:
                subprocess.run(["sudo", "systemctl", "restart", "home_system"], check=True)
                st.success("再起動コマンド送信完了")
            except Exception as e:
                st.error(f"エラー: {e}")
    
    # バックアップ機能 (簡易実装)
    import config
    from services import backup_service
    st.subheader("📦 バックアップ")
    if st.button("今すぐバックアップを実行"):
        success, res, size = backup_service.perform_backup()
        if success: st.success(f"完了: {size:.1f}MB")
        else: st.error(f"失敗: {res}")