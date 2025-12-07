import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import plotly.graph_objects as go
from streamlit_calendar import calendar
import os
import glob
from datetime import datetime
import config
import common

# ページ設定
st.set_page_config(page_title="我が家の司令塔 Pro", layout="wide")

# CSSでカスタマイズ
st.markdown("""
<style>
    .big-font { font-size:24px !important; font-weight:bold; }
    .stMetric { background-color: #f8f9fa; padding: 15px; border-radius: 10px; border: 1px solid #ddd; }
    div.fc-event-main { color: #000 !important; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

st.title("🏠 我が家の司令塔 Pro (Home Dashboard)")

# === データ取得関数 ===
def load_data(table_name, limit=1000):
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
    df = pd.read_sql_query(query, conn)
    conn.close()
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

# === タブ作成 ===
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📅 カレンダー", "💩 健康・お腹", "⚡ 電気・予算", "🍽️ 食事", "🖼️ ギャラリー"])

# --- タブ1: カレンダー (NEW!) ---
with tab1:
    st.header("📅 生活リズムカレンダー")
    
    # イベントデータの作成
    events = []
    
    # 1. 排便記録
    df_poop = load_data("defecation_records", limit=500)
    for _, row in df_poop.iterrows():
        title = "💩 " + row['condition']
        color = "#FFD700" if "バナナ" in row['condition'] else ("#FF6347" if "下痢" in row['condition'] or "腹痛" in row['condition'] else "#87CEEB")
        events.append({
            "title": title,
            "start": row['timestamp'].isoformat(),
            "backgroundColor": color,
            "borderColor": color
        })

    # 2. 子供の体調
    try:
        df_child = load_data(config.SQLITE_TABLE_CHILD, limit=500)
        for _, row in df_child.iterrows():
            if "元気" not in row['condition']:
                events.append({
                    "title": f"🏥 {row['child_name']}: {row['condition']}",
                    "start": row['timestamp'].isoformat(),
                    "backgroundColor": "#FF69B4",
                    "borderColor": "#FF69B4"
                })
    except: pass

    # カレンダー表示
    calendar_options = {
        "headerToolbar": {
            "left": "today prev,next",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,listWeek"
        },
        "initialView": "dayGridMonth",
    }
    calendar(events=events, options=calendar_options, key='calendar')

# --- タブ2: お腹・健康 ---
with tab2:
    st.header("💩 排便・体調ログ")
    if not df_poop.empty:
        col1, col2 = st.columns([2, 1])
        with col1:
            fig = px.scatter(df_poop, x="timestamp", y="condition", color="record_type", title="体調タイムライン")
            fig.update_traces(marker_size=15)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            st.dataframe(df_poop[["timestamp", "condition"]], use_container_width=True)

# --- タブ3: 電気・予算 (NEW!) ---
with tab3:
    st.header("⚡ 電気代と予算管理")
    
    df_sensor = load_data(config.SQLITE_TABLE_SENSOR, limit=2000)
    df_power = df_sensor[df_sensor['device_type'] == 'Nature Remo E Lite']
    
    if not df_power.empty:
        # 今月の電気代予測（簡易計算）
        now = datetime.now()
        current_month_df = df_power[df_power['timestamp'].dt.month == now.month]
        
        if not current_month_df.empty:
            avg_watts = current_month_df['power_watts'].mean()
            hours_passed = (now - now.replace(day=1)).total_seconds() / 3600
            current_bill = (avg_watts * hours_passed / 1000) * 31 # 概算
            
            # 月末までの予測
            days_in_month = 31 # 簡易
            total_hours = days_in_month * 24
            forecast_bill = (avg_watts * total_hours / 1000) * 31
            
            # ゲージチャート
            fig_gauge = go.Figure(go.Indicator(
                mode = "gauge+number+delta",
                value = current_bill,
                domain = {'x': [0, 1], 'y': [0, 1]},
                title = {'text': f"今月の電気代 (予測: {int(forecast_bill):,}円)"},
                delta = {'reference': 10000, 'increasing': {'color': "red"}}, # 予算1万円
                gauge = {
                    'axis': {'range': [None, 15000]},
                    'bar': {'color': "darkblue"},
                    'steps': [
                        {'range': [0, 8000], 'color': "lightgreen"},
                        {'range': [8000, 10000], 'color': "yellow"},
                        {'range': [10000, 15000], 'color': "red"}],
                    'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 10000}
                }
            ))
            st.plotly_chart(fig_gauge, use_container_width=True)
            
            # グラフ
            fig_line = px.line(df_power, x="timestamp", y="power_watts", title="消費電力の推移 (W)")
            st.plotly_chart(fig_line, use_container_width=True)
        else:
            st.info("今月のデータがまだ足りません")

# --- タブ4: 食事 ---
with tab4:
    st.header("🍽️ 食事ログ")
    try:
        df_food = load_data(config.SQLITE_TABLE_FOOD, limit=100)
        st.dataframe(df_food, use_container_width=True)
    except: st.write("データなし")

# --- タブ5: ギャラリー (NEW!) ---
with tab5:
    st.header("📷 防犯カメラ ギャラリー")
    
    # assets/snapshots フォルダ内の画像を取得
    image_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "snapshots")
    images = sorted(glob.glob(os.path.join(image_dir, "*.jpg")), reverse=True)
    
    if images:
        # グリッド表示
        cols = st.columns(3)
        for i, img_path in enumerate(images[:12]): # 最新12枚
            with cols[i % 3]:
                filename = os.path.basename(img_path)
                timestamp_str = filename.replace("snapshot_", "").replace(".jpg", "")
                st.image(img_path, caption=timestamp_str, use_container_width=True)
    else:
        st.info("まだ写真が保存されていません。カメラが検知するとここに表示されます。")

if st.button('🔄 更新'):
    st.rerun()