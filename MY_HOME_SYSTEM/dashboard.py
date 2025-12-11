import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import plotly.graph_objects as go
from streamlit_calendar import calendar
import os
import glob
from datetime import datetime, timedelta
import pytz
import config
import common

# === ページ設定 ===
st.set_page_config(
    page_title="我が家の司令塔 Pro",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# === CSSカスタマイズ ===
st.markdown("""
<style>
    .big-font { font-size:24px !important; font-weight:bold; }
    .stMetric { background-color: #f0f2f6; padding: 15px; border-radius: 10px; border: 1px solid #dcdcdc; }
    div.fc-event-main { color: #000 !important; font-weight: bold; }
    .reportview-container .main .block-container { max_width: 1200px; }
</style>
""", unsafe_allow_html=True)

st.title("🏠 我が家の司令塔 Pro (Season 3)")

# === データ取得関数 ===
@st.cache_data(ttl=60)
def load_data(table_name, limit=2000):
    try:
        conn = sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)
        query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            if df['timestamp'].dt.tz is None:
                df['timestamp'] = df['timestamp'].dt.tz_localize('Asia/Tokyo')
        return df
    except Exception:
        return pd.DataFrame()

# === サイドバー ===
st.sidebar.header("⚙️ 表示設定")
days_to_show = st.sidebar.slider("表示期間 (日)", 1, 30, 7)
if st.sidebar.button("🔄 データを更新"):
    st.cache_data.clear()
    st.rerun()

# データのロード
df_sensor = load_data(config.SQLITE_TABLE_SENSOR, limit=5000)
df_poop = load_data(config.SQLITE_TABLE_DEFECATION, limit=500)
df_child = load_data(config.SQLITE_TABLE_CHILD, limit=500)
df_food = load_data(config.SQLITE_TABLE_FOOD, limit=100)
df_car = load_data(config.SQLITE_TABLE_CAR, limit=100)

# デバイスIDと場所のマッピングを作成
device_map = {d['id']: d for d in config.MONITOR_DEVICES}

# センサーデータに場所情報を付与
if not df_sensor.empty:
    df_sensor['location'] = df_sensor['device_id'].map(lambda x: device_map.get(x, {}).get('location', 'Unknown'))

# === トップサマリー (高砂情報も追加) ===
now = datetime.now(pytz.timezone('Asia/Tokyo'))
start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

# 1. 今日の電気代 (伊丹)
elec_cost_today = 0
if not df_sensor.empty:
    df_power = df_sensor[(df_sensor['device_type'] == 'Nature Remo E Lite')]
    df_today = df_power[df_power['timestamp'] >= start_of_today]
    if not df_today.empty:
        avg_w = df_today['power_watts'].mean()
        hours = (now - start_of_today).total_seconds() / 3600
        elec_cost_today = int((avg_w * hours / 1000) * 31)

# 2. 車の状態
car_status = "🏠 在宅"
if not df_car.empty and df_car.iloc[0]['action'] == "LEAVE":
    car_status = "🚗 外出中"

# 3. 高砂の最終活動時間
last_active_str = "不明"
if not df_sensor.empty:
    # 高砂のセンサー (人感:detected, 開閉:open/close)
    df_takasago_act = df_sensor[
        (df_sensor['location'] == '高砂') & 
        (df_sensor['contact_state'].isin(['detected', 'open', 'close']))
    ]
    if not df_takasago_act.empty:
        last_ts = df_takasago_act.iloc[0]['timestamp']
        diff = now - last_ts
        minutes = int(diff.total_seconds() / 60)
        
        if minutes < 60:
            last_active_str = f"{minutes}分前"
        else:
            last_active_str = f"{int(minutes/60)}時間前"

# サマリー表示
col1, col2, col3, col4 = st.columns(4)
col1.metric("⚡ 今日の電気代", f"{elec_cost_today} 円")
col2.metric("🚗 車の状態", car_status)
col3.metric("👴👵 高砂の活動", last_active_str, help="最後のセンサー反応からの時間")
col4.metric("🚨 侵入検知", f"{len(df_sensor[(df_sensor['contact_state']=='intrusion') & (df_sensor['timestamp']>=start_of_today)])} 回")

st.markdown("---")

# === メインタブ ===
tabs = st.tabs([
    "📅 カレンダー", "👴👵 高砂の実家", "💩 健康・お腹", "⚡ 電気・家電", "🛡️ 防犯・車", "🍽️ 食事", "🖼️ ギャラリー"
])

# --- Tab: カレンダー ---
with tabs[0]:
    st.subheader("📅 生活リズムカレンダー")
    events = []
    if not df_poop.empty:
        for _, row in df_poop.iterrows():
            title = f"💩 {row['condition']}"
            color = "#FFD700" if "バナナ" in row['condition'] else "#FF6347"
            events.append({"title": title, "start": row['timestamp'].isoformat(), "backgroundColor": color})
    if not df_child.empty:
        for _, row in df_child.iterrows():
            if "元気" not in row['condition']:
                events.append({"title": f"🏥 {row['child_name']}", "start": row['timestamp'].isoformat(), "backgroundColor": "#FF69B4"})
    
    calendar(events=events, options={"initialView": "dayGridMonth", "height": 600}, key='cal')

# --- Tab: 高砂の実家 (NEW!) ---
with tabs[1]:
    st.subheader("👴👵 高砂の実家 見守り")
    
    if not df_sensor.empty:
        df_taka = df_sensor[df_sensor['location'] == '高砂']
        
        if not df_taka.empty:
            # 1. 環境 (最新の温湿度)
            df_env = df_taka[df_taka['device_type'] == 'MeterPlus'].sort_values('timestamp')
            if not df_env.empty:
                latest_env = df_env.groupby('device_name').tail(1)
                avg_temp = latest_env['temperature_celsius'].mean()
                avg_hum = latest_env['humidity_percent'].mean()
                
                c1, c2, c3 = st.columns(3)
                c1.metric("🌡️ 平均室温", f"{avg_temp:.1f} °C")
                c2.metric("💧 平均湿度", f"{avg_hum:.0f} %")
                
                # グラフ
                fig_env = px.line(df_env, x="timestamp", y="temperature_celsius", color="device_name", title="室温の推移")
                st.plotly_chart(fig_env, use_container_width=True)
            
            # 2. 活動履歴
            st.markdown("##### 👣 最近の活動ログ")
            df_act = df_taka[
                (df_taka['contact_state'].notnull()) & 
                (df_taka['contact_state'] != 'None')
            ].sort_values('timestamp', ascending=False)
            
            if not df_act.empty:
                st.dataframe(df_act[['timestamp', 'device_name', 'contact_state']].head(20), use_container_width=True)
            else:
                st.info("最近のセンサー反応はありません")
        else:
            st.warning("高砂のデバイスデータが見つかりません")

# --- Tab: 健康 ---
with tabs[2]:
    st.subheader("💩 お腹と体調")
    if not df_poop.empty:
        fig_poop = px.scatter(df_poop, x="timestamp", y="condition", color="record_type", title="体調ログ")
        st.plotly_chart(fig_poop, use_container_width=True)
    if not df_child.empty:
        st.dataframe(df_child[["timestamp", "child_name", "condition"]].head(10), use_container_width=True)

# --- Tab: 電気 ---
with tabs[3]:
    st.subheader("⚡ 電力消費")
    if not df_sensor.empty:
        df_home = df_sensor[(df_sensor['location'] == '伊丹') & (df_sensor['device_type'] == 'Nature Remo E Lite')]
        if not df_home.empty:
            last_24h = now - timedelta(hours=24)
            fig_elec = px.line(df_home[df_home['timestamp'] >= last_24h], x="timestamp", y="power_watts", title="消費電力 (24h)", line_shape="spline")
            fig_elec.update_traces(line_color="orange")
            st.plotly_chart(fig_elec, use_container_width=True)
        
        # プラグ別
        df_plugs = df_sensor[df_sensor['device_type'].str.contains('Plug')]
        if not df_plugs.empty:
            latest = df_plugs.sort_values('timestamp').groupby('device_name').tail(1)
            st.plotly_chart(px.bar(latest, x="power_watts", y="device_name", orientation='h', title="家電の稼働状況"), use_container_width=True)

# --- Tab: 防犯 ---
with tabs[4]:
    st.subheader("🛡️ 防犯・車")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 🚗 車の出入り")
        if not df_car.empty: st.dataframe(df_car[["timestamp", "action", "rule_name"]], use_container_width=True)
    with c2:
        st.markdown("##### 🚨 侵入検知")
        if not df_sensor.empty:
            df_intr = df_sensor[df_sensor['contact_state'] == 'intrusion']
            if not df_intr.empty: st.dataframe(df_intr[["timestamp", "device_name"]], use_container_width=True)
            else: st.success("異常なし")

# --- Tab: 食事 ---
with tabs[5]:
    st.subheader("🍽️ 食事ログ")
    if not df_food.empty:
        st.dataframe(df_food[["timestamp", "menu_category"]], use_container_width=True)

# --- Tab: ギャラリー ---
with tabs[6]:
    st.subheader("📷 ギャラリー")
    image_dir = os.path.join(config.BASE_DIR, "..", "assets", "snapshots")
    images = sorted(glob.glob(os.path.join(image_dir, "*.jpg")), reverse=True)
    if images:
        cols = st.columns(4)
        for i, img in enumerate(images[:12]):
            cols[i%4].image(img, caption=os.path.basename(img), use_container_width=True)
    else:
        st.info("画像なし")

st.markdown("---")
st.caption(f"Last Update: {now.strftime('%H:%M:%S')}")