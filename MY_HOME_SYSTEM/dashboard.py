# MY_HOME_SYSTEM/dashboard.py
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
import traceback
import importlib
import sys

# 自作モジュール
import config
import common

# === ページ設定 ===
st.set_page_config(
    page_title="My Home Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 設定リロード
importlib.reload(config)

# === 🎨 デザイン・CSS定義 ===
def get_custom_css():
    return """
    <style>
        html, body, [class*="css"] { 
            font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif; 
        }
        div[data-testid="stMetric"] {
            background-color: #ffffff; padding: 15px; border-radius: 12px;
            border: 1px solid #e0e0e0; box-shadow: 0 2px 4px rgba(0,0,0,0.05); text-align: center;
        }
        div[data-testid="stMetricLabel"] { font-size: 0.9rem; color: #666; }
        div[data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: bold; color: #2c3e50; }
        
        /* AIレポート (Expanderヘッダーの強調) */
        .streamlit-expanderHeader {
            font-weight: bold;
            color: #0d47a1;
            background-color: #f0f8ff;
            border-radius: 5px;
        }
    </style>
    """

# === 🛠️ データ処理ロジック ===

def get_db_connection():
    return sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)

def apply_friendly_names(df):
    if df.empty: return df
    id_map = {d['id']: d.get('name', d['id']) for d in config.MONITOR_DEVICES}
    loc_map = {d['id']: d.get('location', 'その他') for d in config.MONITOR_DEVICES}
    df['friendly_name'] = df['device_id'].map(id_map).fillna(df['device_name'])
    df['location'] = df['device_id'].map(loc_map).fillna('その他')
    return df

@st.cache_data(ttl=60)
def load_generic_data(table_name, limit=500):
    print(f"📥 [Dashboard] Loading {table_name}...")
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}", conn)
        conn.close()
        if not df.empty and 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Tokyo')
        return df
    except Exception as e:
        print(f"❌ Error loading {table_name}: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def load_sensor_data(limit=5000):
    print(f"📥 [Dashboard] Loading sensors (limit={limit})...")
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(f"SELECT * FROM {config.SQLITE_TABLE_SENSOR} ORDER BY timestamp DESC LIMIT {limit}", conn)
        conn.close()
        if df.empty: return df

        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Tokyo')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Tokyo')
        return apply_friendly_names(df)
    except Exception as e:
        print(f"❌ Error loading sensors: {e}")
        return pd.DataFrame()

def load_ai_report():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (config.SQLITE_TABLE_AI_REPORT,))
        if not cur.fetchone(): return None
        df = pd.read_sql_query(f"SELECT * FROM {config.SQLITE_TABLE_AI_REPORT} ORDER BY id DESC LIMIT 1", conn)
        conn.close()
        return df.iloc[0] if not df.empty else None
    except: return None

def calculate_monthly_cost_cumulative():
    """今月の電気代累積値を計算 (積分法)"""
    try:
        conn = get_db_connection()
        now = datetime.now(pytz.timezone('Asia/Tokyo'))
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
        
        query = f"""
            SELECT timestamp, power_watts FROM {config.SQLITE_TABLE_SENSOR} 
            WHERE device_type = 'Nature Remo E Lite' AND timestamp >= '{start_of_month}'
            ORDER BY timestamp ASC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if df.empty: return 0
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Tokyo')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Tokyo')

        df['time_diff'] = df['timestamp'].diff().dt.total_seconds() / 3600
        df = df.dropna(subset=['time_diff'])
        df = df[df['time_diff'] <= 1.0] # 1時間以上の欠測は除外
        
        df['kwh'] = (df['power_watts'] / 1000) * df['time_diff']
        return int(df['kwh'].sum() * 31)
    except: return 0

# === 🖥️ メイン表示ロジック ===
def main():
    st.markdown(get_custom_css(), unsafe_allow_html=True)
    now = datetime.now(pytz.timezone('Asia/Tokyo'))
    print(f"🔄 [Dashboard] Rendering... ({now.strftime('%H:%M:%S')})")

    # 1. AI執事メッセージ (スマホ対策: Expander化)
    report = load_ai_report()
    if report is not None:
        report_time = pd.to_datetime(report['timestamp']).tz_convert('Asia/Tokyo')
        time_str = report_time.strftime('%H:%M')
        
        # 時間帯アイコン
        hour = report_time.hour
        icon = "☀️" if 5 <= hour < 11 else ("🕛" if 11 <= hour < 17 else "🌙")
        
        # Expanderで初期は閉じておく（または数行表示）
        with st.expander(f"{icon} 執事からの報告 ({time_str}) - タップして読む", expanded=False):
            # メッセージ内の改行を整理
            clean_msg = report['message'].replace('\n', '  \n') 
            st.markdown(clean_msg)

    # データロード
    df_sensor = load_sensor_data(limit=10000)
    df_poop = load_generic_data(config.SQLITE_TABLE_DEFECATION)
    df_child = load_generic_data(config.SQLITE_TABLE_CHILD)
    df_food = load_generic_data(config.SQLITE_TABLE_FOOD)
    df_car = load_generic_data(config.SQLITE_TABLE_CAR)

    # 2. ステータスメトリクス
    # 高砂
    taka_msg = "⚪ データなし"
    if not df_sensor.empty:
        df_taka = df_sensor[(df_sensor['location']=='高砂') & (df_sensor['contact_state'].isin(['open','detected']))]
        if not df_taka.empty:
            last_active = df_taka.iloc[0]['timestamp']
            diff_min = (now - last_active).total_seconds() / 60
            if diff_min < 60: taka_msg = "🟢 元気 (1h以内)"
            elif diff_min < 180: taka_msg = "🟡 静か (3h以内)"
            else: taka_msg = f"🔴 {int(diff_min/60)}時間なし"

    # 伊丹 (人感センサー判定)
    itami_msg = "⚪ データなし"
    if not df_sensor.empty:
        # 伊丹の人感センサー(Motion Sensor)の動きを検索
        df_itami_motion = df_sensor[
            (df_sensor['location'] == '伊丹') & 
            (df_sensor['device_type'].str.contains('Motion')) &
            (df_sensor['movement_state'] == 'detected')
        ].sort_values('timestamp', ascending=False)
        
        if not df_itami_motion.empty:
            last_mov = df_itami_motion.iloc[0]['timestamp']
            diff_m = (now - last_mov).total_seconds() / 60
            if diff_m < 10: itami_msg = "🟢 活動中 (今)"
            elif diff_m < 60: itami_msg = f"🟢 {int(diff_m)}分前"
            else: itami_msg = f"🟡 {int(diff_m/60)}時間動きなし"
        else:
            # 動きがない場合は開閉センサーも見てみる
            df_itami_contact = df_sensor[
                (df_sensor['location'] == '伊丹') & 
                (df_sensor['contact_state'] == 'open')
            ].sort_values('timestamp', ascending=False)
            if not df_itami_contact.empty:
                last_c = df_itami_contact.iloc[0]['timestamp']
                diff_c = (now - last_c).total_seconds() / 60
                if diff_c < 60: itami_msg = f"🟢 {int(diff_c)}分前(ドア)"

    # 電気代
    current_cost = calculate_monthly_cost_cumulative()

    # 車
    car_msg = "🏠 在宅"
    if not df_car.empty and df_car.iloc[0]['action'] == 'LEAVE':
        car_msg = "🚗 外出中"

    # カラム表示 (トイレを削除し、伊丹を追加)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("👵 高砂 (実家)", taka_msg)
    col2.metric("🏠 伊丹 (自宅)", itami_msg)
    col3.metric("⚡ 電気代 (今月)", f"{current_cost:,} 円")
    col4.metric("🚗 車 (伊丹)", car_msg)

    st.markdown("---")

    # ==========================================
    # 3. 機能別タブ
    # ==========================================
    tab_cal, tab_photo, tab_elec, tab_temp, tab_health, tab_taka, tab_log = st.tabs([
        "📅 カレンダー", "🖼️ 写真・防犯", "💰 電気・家電", 
        "🌡️ 室温・環境", "🏥 健康・食事", "👵 高砂詳細", "📜 全ログ"
    ])

    # Tab: カレンダー
    with tab_cal:
        calendar_events = []
        if not df_sensor.empty:
            df_sensor['date_str'] = df_sensor['timestamp'].dt.strftime('%Y-%m-%d')
            for key, label, color in [('冷蔵庫', '🧊冷蔵庫', '#a8dadc'), ('トイレ', '🚽トイレ', '#ffccd5')]:
                df_target = df_sensor[(df_sensor['friendly_name'].str.contains(key)) & (df_sensor['contact_state'].isin(['open','detected']))]
                if not df_target.empty:
                    counts = df_target.groupby('date_str').size()
                    for d_val, c_val in counts.items():
                        calendar_events.append({"title": f"{label}: {c_val}回", "start": d_val, "color": color, "textColor": "#333", "allDay": True})
        if not df_child.empty:
            for _, row in df_child.iterrows():
                if "元気" not in row['condition']:
                    calendar_events.append({"title": f"🏥{row['child_name']}", "start": row['timestamp'].isoformat(), "color": "#ffb703", "textColor": "#333"})
        calendar(events=calendar_events, options={"initialView": "dayGridMonth", "height": 600}, key="cal_main")

    # Tab: 写真・防犯
    with tab_photo:
        st.subheader("🖼️ カメラ・ギャラリー")
        img_dir = os.path.join(config.BASE_DIR, "..", "assets", "snapshots")
        images = sorted(glob.glob(os.path.join(img_dir, "*.jpg")), reverse=True)
        if images:
            cols_img = st.columns(4)
            for i, p in enumerate(images[:4]):
                cols_img[i].image(p, caption=os.path.basename(p), use_container_width=True)
            with st.expander("📂 過去の写真"):
                cols_past = st.columns(4)
                for i, p in enumerate(images[4:20]):
                    cols_past[i%4].image(p, caption=os.path.basename(p), use_container_width=True)
        else: st.info("写真なし")
        st.subheader("🛡️ 防犯ログ")
        if not df_sensor.empty:
            df_sec = df_sensor[df_sensor['contact_state'] == 'intrusion']
            if not df_sec.empty:
                st.error("⚠️ 侵入検知あり")
                st.dataframe(df_sec[['timestamp', 'friendly_name', 'location']], use_container_width=True)

    # Tab: 電気・家電 (修正: 前日比較 & 0-24h固定)
    with tab_elec:
        if not df_sensor.empty:
            col_left, col_right = st.columns([1, 1])
            
            # --- スマートメーター (今日 vs 昨日) ---
            with col_left:
                st.subheader("⚡ 消費電力 (今日 vs 昨日)")
                # 今日の0時〜24時 (範囲固定)
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                today_end = today_start + timedelta(days=1)
                yesterday_start = today_start - timedelta(days=1)
                
                # データ抽出
                df_today = df_sensor[
                    (df_sensor['device_type'] == 'Nature Remo E Lite') & 
                    (df_sensor['timestamp'] >= today_start) & (df_sensor['timestamp'] < today_end)
                ].copy()
                
                df_yesterday = df_sensor[
                    (df_sensor['device_type'] == 'Nature Remo E Lite') & 
                    (df_sensor['timestamp'] >= yesterday_start) & (df_sensor['timestamp'] < today_start)
                ].copy()

                if not df_today.empty or not df_yesterday.empty:
                    fig = go.Figure()
                    
                    # 昨日のプロット (グレー) - 時間を今日に合わせてシフト
                    if not df_yesterday.empty:
                        df_yesterday['plot_time'] = df_yesterday['timestamp'] + timedelta(days=1)
                        fig.add_trace(go.Scatter(
                            x=df_yesterday['plot_time'], y=df_yesterday['power_watts'],
                            mode='lines', name='昨日', line=dict(color='#cccccc', width=2)
                        ))

                    # 今日のプロット (メイン色)
                    if not df_today.empty:
                        fig.add_trace(go.Scatter(
                            x=df_today['timestamp'], y=df_today['power_watts'],
                            mode='lines', name='今日', line=dict(color='#3366cc', width=3)
                        ))

                    # X軸を0:00-23:59に固定
                    fig.update_layout(
                        xaxis_range=[today_start, today_end],
                        xaxis_title="時間", yaxis_title="電力(W)",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("データがありません")

            # --- 個別家電 (24h) ---
            with col_right:
                st.subheader("🔌 個別家電 (直近24h)")
                df_app = df_sensor[
                    (df_sensor['device_type'].str.contains('Plug')) & 
                    (df_sensor['timestamp'] >= now - timedelta(hours=24))
                ]
                if not df_app.empty:
                    st.plotly_chart(px.line(df_app, x='timestamp', y='power_watts', color='friendly_name', title="プラグ計測値"), use_container_width=True)
                else:
                    st.info("プラグデータなし")
            
            st.markdown("---")
            st.subheader("🏆 家電別・電力シェア (スマートメーター除外)")
            # Nature Remo以外、かつPlug系、かつ1W以上
            df_pie = df_sensor[df_sensor['device_type'] != 'Nature Remo E Lite'].sort_values('timestamp').groupby('device_id').tail(1)
            df_pie = df_pie[(df_pie['device_type'].str.contains('Plug')) & (df_pie['power_watts'] > 1)]
            if not df_pie.empty:
                st.plotly_chart(px.pie(df_pie, values='power_watts', names='friendly_name'), use_container_width=True)
            else:
                st.info("稼働中の家電はありません")

    # Tab: 室温
    with tab_temp:
        st.subheader("🌡️ 室温 (24h)")
        df_temp = df_sensor[(df_sensor['device_type'].str.contains('Meter')) & (df_sensor['timestamp'] >= now - timedelta(hours=24))]
        if not df_temp.empty:
            st.plotly_chart(px.line(df_temp, x='timestamp', y='temperature_celsius', color='friendly_name'), use_container_width=True)
            st.subheader("💧 湿度")
            st.plotly_chart(px.line(df_temp, x='timestamp', y='humidity_percent', color='friendly_name'), use_container_width=True)

    # Tab: 健康・食事
    with tab_health:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### 🏥 子供")
            if not df_child.empty: st.dataframe(df_child[['timestamp', 'child_name', 'condition']], use_container_width=True)
        with c2:
            st.markdown("##### 💩 排便")
            if not df_poop.empty: st.dataframe(df_poop[['timestamp', 'user_name', 'condition']], use_container_width=True)
        st.markdown("##### 🍽️ 食事")
        if not df_food.empty: st.dataframe(df_food[['timestamp', 'menu_category']], use_container_width=True)

    # Tab: 高砂
    with tab_taka:
        if not df_sensor.empty:
            st.subheader("👵 実家ログ")
            st.dataframe(df_sensor[df_sensor['location']=='高砂'][['timestamp', 'friendly_name', 'contact_state']].head(50), use_container_width=True)

    # Tab: 全ログ
    with tab_log:
        if not df_sensor.empty:
            locs = df_sensor['location'].unique()
            sel = st.multiselect("場所", locs, default=locs)
            st.dataframe(df_sensor[df_sensor['location'].isin(sel)][['timestamp', 'friendly_name', 'location', 'contact_state', 'power_watts']].head(200), use_container_width=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"📉 Dashboard Error: {e}"}], target="discord", channel="error")
        st.error("システムエラーが発生しました")
        st.code(traceback.format_exc())