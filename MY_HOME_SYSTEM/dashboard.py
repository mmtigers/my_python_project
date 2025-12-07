import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import config
import common

# ページ設定
st.set_page_config(page_title="我が家のダッシュボード", layout="wide")

# CSSで少しおしゃれに
st.markdown("""
<style>
    .big-font { font-size:24px !important; font-weight:bold; }
    .stMetric { background-color: #f0f2f6; padding: 10px; border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

st.title("🏠 我が家の司令塔 (Home Dashboard)")

# === データ取得関数 ===
def load_data(table_name, limit=500):
    """DBから指定テーブルのデータを読み込んでDataFrameにする"""
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # timestampを日時型に変換
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

# === タブの作成 ===
tab1, tab2, tab3, tab4 = st.tabs(["💩 お腹・健康", "🌡️ 環境・センサー", "⚡ 電力・家電", "🍽️ 食事ログ"])

# --- タブ1: お腹・健康 ---
with tab1:
    st.header("💩 お腹と排便の記録")
    
    # データを読み込み
    try:
        df_poop = load_data("defecation_records", limit=100)
        
        if not df_poop.empty:
            # 最新の状態を表示
            latest = df_poop.iloc[0]
            col1, col2 = st.columns(2)
            col1.metric("最終記録", latest['timestamp'].strftime('%m/%d %H:%M'))
            col2.metric("状態", f"{latest['condition']}")

            # タイムラインチャート (散布図)
            fig = px.scatter(df_poop, x="timestamp", y="condition", color="record_type",
                             title="排便・症状のタイムライン", height=300)
            fig.update_traces(marker_size=15)
            st.plotly_chart(fig, use_container_width=True)

            # 詳細テーブル
            st.dataframe(df_poop[["timestamp", "user_name", "record_type", "condition"]], use_container_width=True)
        else:
            st.info("まだ記録がありません。LINEで「うんち」と送ってみてね！")
            
    except Exception as e:
        st.error(f"データ読み込みエラー: {e}")
        st.caption("※まだテーブルが作成されていないか、データがありません。")

# --- タブ2: 環境・センサー ---
with tab2:
    st.header("🌡️ 温湿度の推移")
    
    # センサーデータ読み込み
    df_sensor = load_data(config.SQLITE_TABLE_SENSOR, limit=1000)
    
    if not df_sensor.empty:
        # デバイス一覧を取得
        devices = df_sensor['device_name'].unique()
        selected_device = st.selectbox("デバイスを選択", devices, index=0)
        
        # 選択されたデバイスでフィルタリング
        df_target = df_sensor[df_sensor['device_name'] == selected_device]
        
        # 温度と湿度のグラフ
        if 'temperature_celsius' in df_target.columns and df_target['temperature_celsius'].notnull().any():
            fig_temp = px.line(df_target, x="timestamp", y=["temperature_celsius", "humidity_percent"],
                               title=f"{selected_device} の温湿度", markers=True)
            st.plotly_chart(fig_temp, use_container_width=True)
            
            # 最新値
            latest_sensor = df_target.iloc[0]
            c1, c2, c3 = st.columns(3)
            c1.metric("温度", f"{latest_sensor['temperature_celsius']} °C")
            c2.metric("湿度", f"{latest_sensor['humidity_percent']} %")
            c3.metric("更新", latest_sensor['timestamp'].strftime('%H:%M'))
        else:
            st.warning("このデバイスには温湿度データがありません。")

# --- タブ3: 電力・家電 ---
with tab3:
    st.header("⚡ 電力使用状況")
    
    if not df_sensor.empty:
        # 電力データを持つデバイスのみ抽出
        df_power = df_sensor[df_sensor['power_watts'].notnull()]
        
        if not df_power.empty:
            # 直近の電力消費ランキング
            latest_power = df_power.groupby('device_name').first().reset_index()
            fig_bar = px.bar(latest_power, x='device_name', y='power_watts', color='device_name', title="現在の消費電力 (W)")
            st.plotly_chart(fig_bar, use_container_width=True)
            
            # 時系列
            fig_line = px.line(df_power, x="timestamp", y="power_watts", color="device_name", title="電力消費トレンド")
            st.plotly_chart(fig_line, use_container_width=True)

# --- タブ4: 食事ログ ---
with tab4:
    st.header("🍽️ 最近のごはん")
    try:
        df_food = load_data(config.SQLITE_TABLE_FOOD, limit=50)
        if not df_food.empty:
            st.dataframe(df_food[["meal_date", "menu_category", "meal_time_category"]], use_container_width=True)
    except:
        st.write("データなし")

# 更新ボタン
if st.button('🔄 データを更新'):
    st.rerun()