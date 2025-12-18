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
import train_service

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
        
        /* ステータスカードのスタイル */
        .status-card {
            padding: 15px 10px;
            border-radius: 12px;
            text-align: center;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
            margin-bottom: 10px;
            height: 100%;
        }
        .status-title {
            font-size: 0.85rem;
            color: #555;
            margin-bottom: 8px;
            font-weight: bold;
            opacity: 0.8;
        }
        .status-value {
            font-size: 1.2rem;
            font-weight: bold;
            line-height: 1.3;
            white-space: normal; 
        }
        
        /* カラーテーマ */
        .theme-green { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
        .theme-yellow { background-color: #fffde7; color: #f9a825; border: 1px solid #fff9c4; }
        .theme-red { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
        .theme-blue { background-color: #e3f2fd; color: #1565c0; border: 1px solid #bbdefb; }
        .theme-gray { background-color: #f5f5f5; color: #757575; border: 1px solid #e0e0e0; }

        /* 交通ルートカード */
        .route-card {
            background-color: #fff;
            padding: 15px;
            border-radius: 10px;
            border: 1px solid #ddd;
            margin-bottom: 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .route-path {
            margin-top: 15px;
            padding-top: 10px;
            border-top: 1px dashed #ccc;
            font-size: 0.95rem;
            color: #333;
        }
        .station-node { font-weight: bold; color: #000; }
        .line-node { color: #666; font-size: 0.85rem; margin: 0 5px; }
        .transfer-mark { color: #f57f17; font-weight:bold; margin: 0 5px; }

        /* AIレポート */
        .streamlit-expanderHeader {
            font-weight: bold;
            color: #0d47a1;
            background-color: #f0f8ff;
            border-radius: 5px;
        }
    </style>
    """

# === 🛠️ データ処理ロジック ===

FRIENDLY_NAME_FIXES = {
    "リビング": "高砂のリビング",
    "１Fの洗面所": "高砂の洗面所",
    "居間": "伊丹のリビング",
    "仕事部屋": "伊丹の書斎",
    "人感センサー": "高砂のトイレ(人感)" 
}

def get_db_connection():
    return sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)

def apply_friendly_names(df):
    if df.empty: return df
    id_map = {d['id']: d.get('name', d['id']) for d in config.MONITOR_DEVICES}
    loc_map = {d['id']: d.get('location', 'その他') for d in config.MONITOR_DEVICES}
    df['friendly_name'] = df['device_id'].map(id_map).fillna(df['device_name'])
    df['location'] = df['device_id'].map(loc_map).fillna('その他')
    df['friendly_name'] = df['friendly_name'].replace(FRIENDLY_NAME_FIXES)
    return df

@st.cache_data(ttl=60)
def load_generic_data(table_name, limit=500):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        if not cur.fetchone():
            conn.close()
            return pd.DataFrame()
        df = pd.read_sql_query(f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}", conn)
        conn.close()
        if not df.empty and 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            if df['timestamp'].dt.tz is None:
                df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Tokyo')
            else:
                df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Tokyo')
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=60)
def load_sensor_data(limit=5000):
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
    except: return pd.DataFrame()

@st.cache_data(ttl=300)
def load_calendar_sensor_data(days=35):
    try:
        conn = get_db_connection()
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        query = f"""
            SELECT * FROM {config.SQLITE_TABLE_SENSOR} 
            WHERE timestamp >= '{start_date}' 
            AND (contact_state IN ('open', 'detected') OR movement_state = 'detected')
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        if df.empty: return df
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Tokyo')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Tokyo')
        return apply_friendly_names(df)
    except: return pd.DataFrame()

# ★ 新規追加: 天気履歴ロード関数
@st.cache_data(ttl=300)
def load_weather_history(days=40, location='伊丹'):
    """指定期間・場所の天気履歴を取得"""
    try:
        conn = get_db_connection()
        # weather_historyテーブルがあるか確認
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='weather_history'")
        if not cur.fetchone():
            conn.close()
            return pd.DataFrame()

        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        query = f"""
            SELECT date, min_temp, max_temp, weather_desc, umbrella_level 
            FROM weather_history 
            WHERE location = '{location}' AND date >= '{start_date}'
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"Weather load error: {e}")
        return pd.DataFrame()

def load_ai_report():
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(f"SELECT * FROM {config.SQLITE_TABLE_AI_REPORT} ORDER BY id DESC LIMIT 1", conn)
        conn.close()
        return df.iloc[0] if not df.empty else None
    except: return None

def calculate_monthly_cost_cumulative():
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
        df = df[df['time_diff'] <= 1.0]
        df['kwh'] = (df['power_watts'] / 1000) * df['time_diff']
        return int(df['kwh'].sum() * 31)
    except: return 0

# === 🖥️ メイン表示ロジック ===
def main():
    st.markdown(get_custom_css(), unsafe_allow_html=True)
    now = datetime.now(pytz.timezone('Asia/Tokyo'))
    print(f"🔄 [Dashboard] Rendering... ({now.strftime('%H:%M:%S')})")

    # 1. AI執事メッセージ
    report = load_ai_report()
    if report is not None:
        report_time = pd.to_datetime(report['timestamp']).tz_convert('Asia/Tokyo')
        time_str = report_time.strftime('%H:%M')
        hour = report_time.hour
        icon = "☀️" if 5 <= hour < 11 else ("🕛" if 11 <= hour < 17 else "🌙")
        with st.expander(f"{icon} セバスチャンからの報告 ({time_str}) - タップして読む", expanded=False):
            clean_msg = report['message'].replace('\n', '  \n') 
            st.markdown(clean_msg)

    # データロード
    df_sensor = load_sensor_data(limit=10000)
    df_calendar_sensor = load_calendar_sensor_data(days=35)
    df_weather = load_weather_history(days=40, location='伊丹') # ★天気データ
    df_poop = load_generic_data(config.SQLITE_TABLE_DEFECATION)
    df_child = load_generic_data(config.SQLITE_TABLE_CHILD)
    df_food = load_generic_data(config.SQLITE_TABLE_FOOD)
    df_car = load_generic_data(config.SQLITE_TABLE_CAR)
    df_security_log = load_generic_data("security_logs", limit=100)

    # === 2. ステータスカード ===
    
    # -- 高砂 --
    taka_val = "⚪ データなし"
    taka_theme = "theme-gray"
    if not df_sensor.empty:
        df_taka = df_sensor[(df_sensor['location']=='高砂') & (df_sensor['contact_state'].isin(['open','detected']))]
        if not df_taka.empty:
            last_active = df_taka.iloc[0]['timestamp']
            diff_min = (now - last_active).total_seconds() / 60
            if diff_min < 60: 
                taka_val = "🟢 元気 (1h以内)"
                taka_theme = "theme-green"
            elif diff_min < 180: 
                taka_val = "🟡 静か (3h以内)"
                taka_theme = "theme-yellow"
            else: 
                taka_val = f"🔴 {int(diff_min/60)}時間 動きなし"
                taka_theme = "theme-red"

    # -- 伊丹 --
    itami_val = "⚪ データなし"
    itami_theme = "theme-gray"
    if not df_sensor.empty:
        df_itami_motion = df_sensor[(df_sensor['location'] == '伊丹') & (df_sensor['device_type'].str.contains('Motion')) & (df_sensor['movement_state'] == 'detected')].sort_values('timestamp', ascending=False)
        if not df_itami_motion.empty:
            last_mov = df_itami_motion.iloc[0]['timestamp']
            diff_m = (now - last_mov).total_seconds() / 60
            if diff_m < 10: 
                itami_val = "🟢 活動中 (今)"
                itami_theme = "theme-green"
            elif diff_m < 60: 
                itami_val = f"🟢 活動中 ({int(diff_m)}分前)"
                itami_theme = "theme-green"
            else: 
                itami_val = f"🟡 静か ({int(diff_m/60)}h前)"
                itami_theme = "theme-yellow"
        else:
            df_itami_contact = df_sensor[(df_sensor['location'] == '伊丹') & (df_sensor['contact_state'] == 'open')].sort_values('timestamp', ascending=False)
            if not df_itami_contact.empty:
                last_c = df_itami_contact.iloc[0]['timestamp']
                diff_c = (now - last_c).total_seconds() / 60
                if diff_c < 60: 
                    itami_val = f"🟢 活動中 ({int(diff_c)}分前)"
                    itami_theme = "theme-green"

    # -- 🍚 炊飯器 --
    rice_val = "⚪ データなし"
    rice_theme = "theme-gray"
    if not df_sensor.empty:
        check_time = now - timedelta(minutes=15)
        df_rice = df_sensor[(df_sensor['friendly_name'].str.contains('炊飯器')) & (df_sensor['timestamp'] >= check_time)]
        if not df_rice.empty:
            max_watts = df_rice['power_watts'].max()
            if max_watts > 5:
                rice_val = "🍚 ご飯あり"
                rice_theme = "theme-green"
            else:
                rice_val = "🍚 なし"
                rice_theme = "theme-red"

    # -- 交通 (3番目) --
    jr_status = train_service.get_jr_traffic_status()
    line_g = jr_status["宝塚線"]
    line_a = jr_status["神戸線"]
    
    if line_g.get("is_suspended") or line_a.get("is_suspended"):
        traffic_theme = "theme-red"
        traffic_val = "⛔ 運休発生 詳細を確認"
    elif line_g["is_delay"] or line_a["is_delay"]:
        traffic_theme = "theme-yellow"
        traffic_val = "⚠️ 遅延あり 詳細を確認"
    else:
        traffic_theme = "theme-green"
        traffic_val = "🟢 平常運転 (遅れなし)"

    # -- 電気代 --
    current_cost = calculate_monthly_cost_cumulative()
    elec_val = f"⚡ {current_cost:,} 円 (今月)"
    elec_theme = "theme-blue"

    # -- 車 --
    car_val = "🏠 在宅"
    car_theme = "theme-green"
    if not df_car.empty and df_car.iloc[0]['action'] == 'LEAVE':
        car_val = "🚗 外出中"
        car_theme = "theme-yellow"

    # 描画
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    
    def render_card(col, title, value, theme):
        with col:
            st.markdown(f"""
            <div class="status-card {theme}">
                <div class="status-title">{title}</div>
                <div class="status-value">{value}</div>
            </div>
            """, unsafe_allow_html=True)

    render_card(col1, "👵 高砂 (実家)", taka_val, taka_theme)
    render_card(col2, "🏠 伊丹 (自宅)", itami_val, itami_theme)
    render_card(col3, "🍚 炊飯器", rice_val, rice_theme)
    render_card(col4, "🚃 JR宝塚・神戸", traffic_val, traffic_theme)
    render_card(col5, "💰 電気代", elec_val, elec_theme)
    render_card(col6, "🚗 車 (伊丹)", car_val, car_theme)

    st.markdown("---")

    # 3. 機能別タブ
    tab_cal, tab_train, tab_photo, tab_elec, tab_temp, tab_health, tab_taka, tab_log = st.tabs([
        "📅 カレンダー", "🚃 交通", "🖼️ 写真・防犯", "💰 電気・家電", 
        "🌡️ 室温・環境", "🏥 健康・食事", "👵 高砂詳細", "📜 全ログ"
    ])

    # Tab: カレンダー
    with tab_cal:
        calendar_events = []
        
        # 1. センサーイベント (冷蔵庫・トイレ)
        if not df_calendar_sensor.empty:
            df_calendar_sensor['date_str'] = df_calendar_sensor['timestamp'].dt.strftime('%Y-%m-%d')
            for key, label, color in [('冷蔵庫', '🧊冷蔵庫', '#a8dadc'), ('トイレ', '🚽トイレ', '#ffccd5')]:
                df_device = df_calendar_sensor[df_calendar_sensor['friendly_name'].str.contains(key, na=False)]
                mask_contact = df_device['contact_state'].isin(['open', 'detected'])
                mask_motion = df_device['movement_state'] == 'detected'
                df_target = df_device[mask_contact | mask_motion]
                if not df_target.empty:
                    counts = df_target.groupby('date_str').size()
                    for d_val, c_val in counts.items():
                        calendar_events.append({"title": f"{label}: {c_val}回", "start": d_val, "color": color, "textColor": "#333", "allDay": True})
        
        # 2. 子供の体調
        if not df_child.empty:
            for _, row in df_child.iterrows():
                if "元気" not in row['condition']:
                    calendar_events.append({"title": f"🏥{row['child_name']}", "start": row['timestamp'].isoformat(), "color": "#ffb703", "textColor": "#333"})
        
        # 3. 天気履歴 (新規追加)
        if not df_weather.empty:
            for _, row in df_weather.iterrows():
                desc = row['weather_desc']
                # アイコン判定
                w_icon = "🌤"
                bg_color = "#f5f5f5" # デフォルト: グレー
                
                if "雨" in desc: 
                    w_icon = "☔"
                    bg_color = "#e3f2fd" # 薄い青
                elif "晴" in desc:
                    w_icon = "☀"
                    bg_color = "#fff3e0" # 薄いオレンジ
                elif "曇" in desc:
                    w_icon = "☁"
                elif "雪" in desc:
                    w_icon = "⛄"
                
                # タイトル作成 (例: ☀晴れ 15/8℃)
                w_title = f"{w_icon}{desc} {int(row['max_temp'])}/{int(row['min_temp'])}℃"
                
                calendar_events.append({
                    "title": w_title,
                    "start": row['date'],
                    "backgroundColor": bg_color,
                    "borderColor": "transparent",
                    "textColor": "#444",
                    "allDay": True
                })

        calendar(events=calendar_events, options={"initialView": "dayGridMonth", "height": 600}, key="cal_main")

    # Tab: 交通 (詳細)
    with tab_train:
        st.subheader("🚃 JR宝塚線・神戸線 運行状況")
        c_t1, c_t2 = st.columns(2)
        
        bg_g = "#ffebee" if line_g["is_delay"] else "#e8f5e9"
        with c_t1:
            st.markdown(f"""
            <div style="background-color:{bg_g}; padding:15px; border-radius:10px; border:1px solid #ccc;">
                <h3 style="margin:0; color:#333;">JR 宝塚線</h3>
                <h2 style="margin:5px 0; color:{'#d32f2f' if line_g['is_delay'] else '#2e7d32'};">{line_g['status']}</h2>
                <p style="margin:0;">{line_g['detail']}</p>
            </div>
            """, unsafe_allow_html=True)

        bg_a = "#ffebee" if line_a["is_delay"] else "#e8f5e9"
        with c_t2:
            st.markdown(f"""
            <div style="background-color:{bg_a}; padding:15px; border-radius:10px; border:1px solid #ccc;">
                <h3 style="margin:0; color:#333;">JR 神戸線</h3>
                <h2 style="margin:5px 0; color:{'#d32f2f' if line_a['is_delay'] else '#2e7d32'};">{line_a['status']}</h2>
                <p style="margin:0;">{line_a['detail']}</p>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")
        st.subheader(f"📍 ルート検索 ({(datetime.now() + timedelta(minutes=20)).strftime('%H:%M')} 出発想定)")
        
        col_out, col_in = st.columns(2)
        
        def render_route(col, from_st, to_st, label_icon):
            with col:
                st.markdown(f"##### {label_icon} {from_st} → {to_st}")
                data = train_service.get_route_info(from_st, to_st)
                
                if data["summary"] == "取得成功":
                    details_html = ""
                    if data.get("details"):
                        steps = []
                        for d in data["details"]:
                            if "⬇️" in d: 
                                steps.append(f"<div class='line-node'>{d}</div>")
                            elif "🔄" in d:
                                steps.append(f"<div class='transfer-mark'>{d}</div>")
                            else:
                                steps.append(f"<div class='station-node'>{d}</div>")
                        details_html = f"<div class='route-path'>{''.join(steps)}</div>"

                    st.markdown(f"""
                    <div class="route-card">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                            <span style="font-size:1.3rem; font-weight:bold; color:#0d47a1;">{data['departure']}</span>
                            <span style="color:#777;">➡</span>
                            <span style="font-size:1.3rem; font-weight:bold; color:#0d47a1;">{data['arrival']}</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; color:#555; margin-bottom:5px;">
                            <span>⏱️ <b>{data['duration']}</b></span>
                            <span>💰 {data['cost']}</span>
                        </div>
                        <div style="font-size:0.9rem; color:#666;">
                            <span>🔄 乗換: {data['transfer']}</span>
                        </div>
                        {details_html}
                    </div>
                    """, unsafe_allow_html=True)
                    if data["url"]:
                        st.link_button(f"🔗 Yahoo!路線情報で見る", data["url"])
                else:
                    st.warning("ルート情報を取得できませんでした")

        render_route(col_out, "伊丹(兵庫県)", "長岡京", "📤")
        render_route(col_in, "長岡京", "伊丹(兵庫県)", "📥")

    # Tab: 写真・防犯 (以下既存)
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
        st.subheader("🛡️ 防犯ログ (検知分類)")
        if not df_security_log.empty:
            df_security_log = apply_friendly_names(df_security_log)
            cols = ['timestamp', 'friendly_name']
            if 'classification' in df_security_log.columns: cols.append('classification')
            if 'image_path' in df_security_log.columns: cols.append('image_path')
            df_disp = df_security_log[cols].copy()
            df_disp.columns = [c.replace('timestamp', '検知時刻').replace('friendly_name', 'デバイス').replace('classification', '検知種別').replace('image_path', '画像') for c in df_disp.columns]
            st.dataframe(df_disp, use_container_width=True)
        elif not df_sensor.empty:
            df_sec = df_sensor[df_sensor['contact_state'] == 'intrusion']
            if not df_sec.empty:
                st.error("⚠️ 侵入検知あり (詳細分類なし)")
                st.dataframe(df_sec[['timestamp', 'friendly_name', 'location']], use_container_width=True)
            else:
                st.info("不審な検知はありません")

    # Tab: 電気・家電 (既存)
    with tab_elec:
        if not df_sensor.empty:
            col_left, col_right = st.columns([1, 1])
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            yesterday_start = today_start - timedelta(days=1)
            with col_left:
                st.subheader("⚡ 消費電力 (今日 vs 昨日)")
                df_today = df_sensor[(df_sensor['device_type'] == 'Nature Remo E Lite') & (df_sensor['timestamp'] >= today_start) & (df_sensor['timestamp'] < today_end)].copy()
                df_yesterday = df_sensor[(df_sensor['device_type'] == 'Nature Remo E Lite') & (df_sensor['timestamp'] >= yesterday_start) & (df_sensor['timestamp'] < today_start)].copy()
                if not df_today.empty or not df_yesterday.empty:
                    fig = go.Figure()
                    if not df_yesterday.empty:
                        df_yesterday['plot_time'] = df_yesterday['timestamp'] + timedelta(days=1)
                        fig.add_trace(go.Scatter(x=df_yesterday['plot_time'], y=df_yesterday['power_watts'], mode='lines', name='昨日', line=dict(color='#cccccc', width=2)))
                    if not df_today.empty:
                        fig.add_trace(go.Scatter(x=df_today['timestamp'], y=df_today['power_watts'], mode='lines', name='今日', line=dict(color='#3366cc', width=3)))
                    fig.update_layout(xaxis_range=[today_start, today_end], xaxis_title="時間", yaxis_title="電力(W)", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    st.plotly_chart(fig, use_container_width=True)
                else: st.info("データがありません")
            with col_right:
                st.subheader("🔌 個別家電 (今日)")
                df_app = df_sensor[(df_sensor['device_type'].str.contains('Plug')) & (df_sensor['timestamp'] >= today_start) & (df_sensor['timestamp'] < today_end)]
                if not df_app.empty:
                    fig_app = px.line(df_app, x='timestamp', y='power_watts', color='friendly_name', title="プラグ計測値")
                    fig_app.update_xaxes(range=[today_start, today_end])
                    st.plotly_chart(fig_app, use_container_width=True)
                else: st.info("プラグデータなし")

    # Tab: 室温 (既存)
    with tab_temp:
        st.subheader("🌡️ 室温 (今日の推移)")
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        df_temp = df_sensor[(df_sensor['device_type'].str.contains('Meter')) & (df_sensor['timestamp'] >= today_start) & (df_sensor['timestamp'] < today_end)]
        if not df_temp.empty:
            fig_t = px.line(df_temp, x='timestamp', y='temperature_celsius', color='friendly_name', title="温度 (℃)")
            fig_t.update_xaxes(range=[today_start, today_end]) 
            st.plotly_chart(fig_t, use_container_width=True)
            st.subheader("💧 湿度 (今日の推移)")
            fig_h = px.line(df_temp, x='timestamp', y='humidity_percent', color='friendly_name', title="湿度 (%)")
            fig_h.update_xaxes(range=[today_start, today_end]) 
            st.plotly_chart(fig_h, use_container_width=True)
        else: st.info("本日の温度データがまだありません")

    # Tab: 健康・食事 (既存)
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

    # Tab: 高砂 (既存)
    with tab_taka:
        if not df_sensor.empty:
            st.subheader("👵 実家ログ")
            st.dataframe(df_sensor[df_sensor['location']=='高砂'][['timestamp', 'friendly_name', 'contact_state']].head(50), use_container_width=True)

    # Tab: 全ログ (既存)
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