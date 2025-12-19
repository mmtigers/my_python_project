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
import logging
import sys

# 自作モジュール
import config
import common
import train_service

# === ロガー設定 ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === ページ設定 ===
st.set_page_config(
    page_title="My Home Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 設定リロード
importlib.reload(config)

# === 定数・設定 ===
FRIENDLY_NAME_FIXES = {
    "リビング": "高砂のリビング",
    "１Fの洗面所": "高砂の洗面所",
    "居間": "伊丹のリビング",
    "仕事部屋": "伊丹の書斎",
    "人感センサー": "高砂のトイレ(人感)" 
}

# === ヘルパー関数: データ処理 ===

def get_db_connection():
    """データベース接続を取得 (読み取り専用)"""
    return sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)

def process_dataframe(df):
    """DataFrameのタイムスタンプを日本時間に変換し、表示名を適用する共通処理"""
    if df.empty or 'timestamp' not in df.columns:
        return df

    # タイムゾーン変換
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    if df['timestamp'].dt.tz is None:
        df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Tokyo')
    else:
        df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Tokyo')
    
    return df

def apply_friendly_names(df):
    """デバイスIDから表示名への変換と、特定の名称置換を行う"""
    if df.empty: return df
    
    # config定義からのマッピング
    id_map = {d['id']: d.get('name', d['id']) for d in config.MONITOR_DEVICES}
    loc_map = {d['id']: d.get('location', 'その他') for d in config.MONITOR_DEVICES}
    
    df['friendly_name'] = df['device_id'].map(id_map).fillna(df['device_name'])
    df['location'] = df['device_id'].map(loc_map).fillna('その他')
    
    # 強制置換
    df['friendly_name'] = df['friendly_name'].replace(FRIENDLY_NAME_FIXES)
    
    return df

@st.cache_data(ttl=60)
def load_data_from_db(query, date_column='timestamp'):
    """汎用データロード関数"""
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        # timestampカラムがある場合は日付処理を行う
        if date_column in df.columns:
            # カラム名を一時的にtimestampにして処理
            if date_column != 'timestamp':
                df.rename(columns={date_column: 'timestamp'}, inplace=True)
            
            df = process_dataframe(df)
            
            # 元に戻す（必要なら）
            if date_column != 'timestamp':
                df.rename(columns={'timestamp': date_column}, inplace=True)
                
        return df
    except Exception as e:
        logger.error(f"Data Load Error (Query: {query[:30]}...): {e}")
        return pd.DataFrame()

# 個別のデータロード関数群
def load_generic_data(table_name, limit=500):
    query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
    return load_data_from_db(query)

def load_sensor_data(limit=5000):
    query = f"SELECT * FROM {config.SQLITE_TABLE_SENSOR} ORDER BY timestamp DESC LIMIT {limit}"
    df = load_data_from_db(query)
    return apply_friendly_names(df)

@st.cache_data(ttl=300)
def load_calendar_sensor_data(days=35):
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    query = f"""
        SELECT * FROM {config.SQLITE_TABLE_SENSOR} 
        WHERE timestamp >= '{start_date}' 
        AND (contact_state IN ('open', 'detected') OR movement_state = 'detected')
    """
    df = load_data_from_db(query)
    return apply_friendly_names(df)

@st.cache_data(ttl=300)
def load_weather_history(days=40, location='伊丹'):
    # weather_historyテーブルの存在確認は省略（エラー時は空DFが返るため）
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    query = f"""
        SELECT date, min_temp, max_temp, weather_desc, umbrella_level 
        FROM weather_history 
        WHERE location = '{location}' AND date >= '{start_date}'
    """
    # weather_historyにはtimestampカラムがないため、process_dataframeは通さない
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        logger.error(f"Weather Load Error: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def load_app_rankings(date_str=None):
    """アプリランキングを取得"""
    conn = None
    try:
        conn = get_db_connection()
        # テーブル存在確認
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_rankings'")
        if not cur.fetchone():
            return pd.DataFrame()

        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
        
        # 指定日のデータ
        query = f"SELECT * FROM app_rankings WHERE date = '{date_str}' ORDER BY rank ASC"
        df = pd.read_sql_query(query, conn)
        
        # なければ最新日を取得
        if df.empty:
            q_latest = "SELECT date FROM app_rankings ORDER BY date DESC LIMIT 1"
            latest_df = pd.read_sql_query(q_latest, conn)
            if not latest_df.empty:
                latest_date = latest_df.iloc[0]['date']
                query = f"SELECT * FROM app_rankings WHERE date = '{latest_date}' ORDER BY rank ASC"
                df = pd.read_sql_query(query, conn)
        
        return df
    except Exception as e:
        logger.error(f"App Ranking Load Error: {e}")
        return pd.DataFrame()
    finally:
        if conn: conn.close()



def load_ai_report():
    query = f"SELECT * FROM {config.SQLITE_TABLE_AI_REPORT} ORDER BY id DESC LIMIT 1"
    df = load_data_from_db(query)
    return df.iloc[0] if not df.empty else None

def calculate_monthly_cost_cumulative():
    """今月の電気代概算"""
    try:
        now = datetime.now(pytz.timezone('Asia/Tokyo'))
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
        
        query = f"""
            SELECT timestamp, power_watts FROM {config.SQLITE_TABLE_SENSOR} 
            WHERE device_type = 'Nature Remo E Lite' AND timestamp >= '{start_of_month}'
            ORDER BY timestamp ASC
        """
        df = load_data_from_db(query)
        
        if df.empty: return 0
        
        df['time_diff'] = df['timestamp'].diff().dt.total_seconds() / 3600
        df = df.dropna(subset=['time_diff'])
        # 異常値除外 (1時間以上の欠落は無視)
        df = df[df['time_diff'] <= 1.0]
        
        df['kwh'] = (df['power_watts'] / 1000) * df['time_diff']
        # 概算単価 31円/kWh
        return int(df['kwh'].sum() * 31)
    except Exception as e:
        logger.error(f"Cost Calc Error: {e}")
        return 0

# === ロジック層: ステータス判定 ===

def get_takasago_status(df_sensor, now):
    """高砂の実家のステータス判定"""
    val = "⚪ データなし"
    theme = "theme-gray"
    
    if df_sensor.empty: return val, theme

    df_taka = df_sensor[
        (df_sensor['location'] == '高砂') & 
        (df_sensor['contact_state'].isin(['open', 'detected']))
    ]
    
    if not df_taka.empty:
        last_active = df_taka.iloc[0]['timestamp']
        diff_min = (now - last_active).total_seconds() / 60
        
        if diff_min < 60:
            val = "🟢 元気 (1h以内)"
            theme = "theme-green"
        elif diff_min < 180:
            val = "🟡 静か (3h以内)"
            theme = "theme-yellow"
        else:
            val = f"🔴 {int(diff_min/60)}時間 動きなし"
            theme = "theme-red"
            
    return val, theme

def get_itami_status(df_sensor, now):
    """伊丹（自宅）のステータス判定"""
    val = "⚪ データなし"
    theme = "theme-gray"
    
    if df_sensor.empty: return val, theme

    # 人感センサー優先
    df_motion = df_sensor[
        (df_sensor['location'] == '伊丹') & 
        (df_sensor['device_type'].str.contains('Motion')) & 
        (df_sensor['movement_state'] == 'detected')
    ].sort_values('timestamp', ascending=False)
    
    if not df_motion.empty:
        last_mov = df_motion.iloc[0]['timestamp']
        diff_m = (now - last_mov).total_seconds() / 60
        
        if diff_m < 10:
            val = "🟢 活動中 (今)"
            theme = "theme-green"
        elif diff_m < 60:
            val = f"🟢 活動中 ({int(diff_m)}分前)"
            theme = "theme-green"
        else:
            val = f"🟡 静か ({int(diff_m/60)}h前)"
            theme = "theme-yellow"
    else:
        # 開閉センサー
        df_contact = df_sensor[
            (df_sensor['location'] == '伊丹') & 
            (df_sensor['contact_state'] == 'open')
        ].sort_values('timestamp', ascending=False)
        
        if not df_contact.empty:
            last_c = df_contact.iloc[0]['timestamp']
            diff_c = (now - last_c).total_seconds() / 60
            if diff_c < 60:
                val = f"🟢 活動中 ({int(diff_c)}分前)"
                theme = "theme-green"
                
    return val, theme

def get_rice_status(df_sensor, now):
    """炊飯器ステータス判定: その日の最大電力が500W超かで判定"""
    # デフォルトは「ご飯なし」
    val = "🍚 炊いてない"
    theme = "theme-red"
    
    # 今日の日付文字列 (YYYY-MM-DD)
    today_str = now.strftime('%Y-%m-%d')
    
    # DBから今日の炊飯器の最大電力を取得するクエリ
    # device_name に '炊飯器' が含まれるレコードを対象
    query = f"""
        SELECT MAX(power_watts) as max_power 
        FROM {config.SQLITE_TABLE_SENSOR} 
        WHERE device_name LIKE '%炊飯器%' 
        AND timestamp >= '{today_str}'
    """
    
    # データを取得 (dashboard.py内のヘルパー関数を使用)
    df_rice = load_data_from_db(query, date_column=None)
    
    if not df_rice.empty:
        max_watts = df_rice.iloc[0]['max_power']
        # max_watts はデータがない場合 None になるのでチェック
        if max_watts is not None and max_watts >= 500:
            val = "🍚 ご飯あり"
            theme = "theme-green"
            
    return val, theme

def get_traffic_status():
    """交通情報ステータス"""
    jr_status = train_service.get_jr_traffic_status()
    line_g = jr_status["宝塚線"]
    line_a = jr_status["神戸線"]
    
    if line_g.get("is_suspended") or line_a.get("is_suspended"):
        return "⛔ 運休発生 詳細を確認", "theme-red", line_g, line_a
    elif line_g["is_delay"] or line_a["is_delay"]:
        return "⚠️ 遅延あり 詳細を確認", "theme-yellow", line_g, line_a
    else:
        return "🟢 平常運転 (遅れなし)", "theme-green", line_g, line_a

def get_car_status(df_car):
    """車ステータス"""
    val = "🏠 在宅"
    theme = "theme-green"
    if not df_car.empty and df_car.iloc[0]['action'] == 'LEAVE':
        val = "🚗 外出中"
        theme = "theme-yellow"
    return val, theme

# === UI層: 描画コンポーネント ===

def get_custom_css():
    return """
    <style>
        html, body, [class*="css"] { 
            font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif; 
        }
        .status-card {
            padding: 15px 10px;
            border-radius: 12px;
            text-align: center;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
            margin-bottom: 10px;
            height: 100%;
        }
        .status-title {
            font-size: 0.85rem; color: #555; margin-bottom: 8px; font-weight: bold; opacity: 0.8;
        }
        .status-value {
            font-size: 1.2rem; font-weight: bold; line-height: 1.3; white-space: normal; 
        }
        .theme-green { background-color: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
        .theme-yellow { background-color: #fffde7; color: #f9a825; border: 1px solid #fff9c4; }
        .theme-red { background-color: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
        .theme-blue { background-color: #e3f2fd; color: #1565c0; border: 1px solid #bbdefb; }
        .theme-gray { background-color: #f5f5f5; color: #757575; border: 1px solid #e0e0e0; }
        
        .route-card {
            background-color: #fff; padding: 15px; border-radius: 10px; 
            border: 1px solid #ddd; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .route-path {
            margin-top: 15px; padding-top: 10px; border-top: 1px dashed #ccc; font-size: 0.95rem; color: #333;
        }
        .station-node { font-weight: bold; color: #000; }
        .line-node { color: #666; font-size: 0.85rem; margin: 0 5px; }
        .transfer-mark { color: #f57f17; font-weight:bold; margin: 0 5px; }
        
        .streamlit-expanderHeader {
            font-weight: bold; color: #0d47a1; background-color: #f0f8ff; border-radius: 5px;
        }
    </style>
    """

def render_status_card_html(title, value, theme):
    return f"""
    <div class="status-card {theme}">
        <div class="status-title">{title}</div>
        <div class="status-value">{value}</div>
    </div>
    """

def render_metrics_section(now, df_sensor, df_car):
    """トップ画面のメトリクス（ステータスカード）を描画"""
    # 各ステータス計算
    taka_val, taka_theme = get_takasago_status(df_sensor, now)
    itami_val, itami_theme = get_itami_status(df_sensor, now)
    rice_val, rice_theme = get_rice_status(df_sensor, now)
    traffic_val, traffic_theme, _, _ = get_traffic_status()
    current_cost = calculate_monthly_cost_cumulative()
    car_val, car_theme = get_car_status(df_car)
    
    # 描画
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    
    with col1: st.markdown(render_status_card_html("👵 高砂 (実家)", taka_val, taka_theme), unsafe_allow_html=True)
    with col2: st.markdown(render_status_card_html("🏠 伊丹 (自宅)", itami_val, itami_theme), unsafe_allow_html=True)
    with col3: st.markdown(render_status_card_html("🍚 炊飯器", rice_val, rice_theme), unsafe_allow_html=True)
    with col4: st.markdown(render_status_card_html("🚃 JR宝塚・神戸", traffic_val, traffic_theme), unsafe_allow_html=True)
    with col5: st.markdown(render_status_card_html("💰 電気代", f"⚡ {current_cost:,} 円", "theme-blue"), unsafe_allow_html=True)
    with col6: st.markdown(render_status_card_html("🚗 車 (伊丹)", car_val, car_theme), unsafe_allow_html=True)

    st.markdown("---")

def render_calendar_tab(df_calendar_sensor, df_child, df_weather):
    """カレンダータブの描画"""
    calendar_events = []
    
    # 1. センサーイベント
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
    
    # 3. 天気履歴
    if not df_weather.empty:
        for _, row in df_weather.iterrows():
            desc = row['weather_desc']
            w_icon = "🌤"
            bg_color = "#f5f5f5"
            
            if "雨" in desc: 
                w_icon = "☔"; bg_color = "#e3f2fd"
            elif "晴" in desc:
                w_icon = "☀"; bg_color = "#fff3e0"
            elif "曇" in desc:
                w_icon = "☁"
            elif "雪" in desc:
                w_icon = "⛄"
            
            w_title = f"{w_icon}{desc} {int(row['max_temp'])}/{int(row['min_temp'])}℃"
            calendar_events.append({
                "title": w_title, "start": row['date'], 
                "backgroundColor": bg_color, "borderColor": "transparent", 
                "textColor": "#444", "allDay": True
            })

    calendar(events=calendar_events, options={"initialView": "dayGridMonth", "height": 600}, key="cal_main")

def render_traffic_tab():
    """交通情報タブの描画"""
    st.subheader("🚃 JR宝塚線・神戸線 運行状況")
    _, _, line_g, line_a = get_traffic_status()
    
    c_t1, c_t2 = st.columns(2)
    
    for col, line, name in [(c_t1, line_g, "JR 宝塚線"), (c_t2, line_a, "JR 神戸線")]:
        bg_color = "#ffebee" if line["is_delay"] else "#e8f5e9"
        status_color = "#d32f2f" if line["is_delay"] else "#2e7d32"
        with col:
            st.markdown(f"""
            <div style="background-color:{bg_color}; padding:15px; border-radius:10px; border:1px solid #ccc;">
                <h3 style="margin:0; color:#333;">{name}</h3>
                <h2 style="margin:5px 0; color:{status_color};">{line['status']}</h2>
                <p style="margin:0;">{line['detail']}</p>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    dep_time = (datetime.now() + timedelta(minutes=20)).strftime('%H:%M')
    st.subheader(f"📍 ルート検索 ({dep_time} 出発想定)")
    
    col_out, col_in = st.columns(2)
    _render_route_search(col_out, "伊丹(兵庫県)", "長岡京", "📤")
    _render_route_search(col_in, "長岡京", "伊丹(兵庫県)", "📥")

def _render_route_search(col, from_st, to_st, label_icon):
    with col:
        st.markdown(f"##### {label_icon} {from_st} → {to_st}")
        data = train_service.get_route_info(from_st, to_st)
        
        if data["summary"] == "取得成功":
            details_html = ""
            if data.get("details"):
                steps = []
                for d in data["details"]:
                    if "⬇️" in d: steps.append(f"<div class='line-node'>{d}</div>")
                    elif "🔄" in d: steps.append(f"<div class='transfer-mark'>{d}</div>")
                    else: steps.append(f"<div class='station-node'>{d}</div>")
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

def render_photos_tab(df_security_log):
    """写真・防犯タブ"""
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
    else:
        st.info("不審な検知はありません")

def render_electricity_tab(df_sensor, now):
    """電気・家電タブ"""
    if df_sensor.empty:
        st.info("データがありません")
        return

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

def render_temperature_tab(df_sensor, now):
    """室温・環境タブ"""
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

def render_health_tab(df_child, df_poop, df_food):
    """健康・食事タブ"""
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 🏥 子供")
        if not df_child.empty: st.dataframe(df_child[['timestamp', 'child_name', 'condition']], use_container_width=True)
    with c2:
        st.markdown("##### 💩 排便")
        if not df_poop.empty: st.dataframe(df_poop[['timestamp', 'user_name', 'condition']], use_container_width=True)
    st.markdown("##### 🍽️ 食事")
    if not df_food.empty: st.dataframe(df_food[['timestamp', 'menu_category']], use_container_width=True)

def render_takasago_tab(df_sensor):
    """高砂詳細タブ"""
    if not df_sensor.empty:
        st.subheader("👵 実家ログ")
        st.dataframe(df_sensor[df_sensor['location']=='高砂'][['timestamp', 'friendly_name', 'contact_state']].head(50), use_container_width=True)

def render_logs_tab(df_sensor):
    """全ログタブ"""
    if not df_sensor.empty:
        locs = df_sensor['location'].unique()
        sel = st.multiselect("場所", locs, default=locs)
        st.dataframe(df_sensor[df_sensor['location'].isin(sel)][['timestamp', 'friendly_name', 'location', 'contact_state', 'power_watts']].head(200), use_container_width=True)

def render_trends_tab():
    """最近の流行タブ"""
    st.title("🌟 最近の流行・トレンド")
    st.caption("Google Playストアのランキング情報を表示します")

    # セクション: アプリ
    st.subheader("📱 スマホアプリ (人気/売上)")
    df_apps = load_app_rankings()
    
    if df_apps.empty:
        st.info("データがありません。ランキング取得を実行してください。")
        return

    # 日付表示
    recorded_date = df_apps.iloc[0]['date']
    st.write(f"取得日: **{recorded_date}**")

    col_free, col_gross = st.columns(2)
    
    def render_rank_list(col, title, r_type):
        with col:
            st.markdown(f"#### {title}")
            target_df = df_apps[df_apps['ranking_type'] == r_type].sort_values('rank')
            if target_df.empty:
                st.warning("データなし")
                return
            
            for _, row in target_df.iterrows():
                # Score表示 (0.0の場合は非表示)
                score_html = f'<div class="app-score">★{row["score"]:.1f}</div>' if row['score'] > 0 else ''
                
                # HTMLでリスト表示
                html = f"""
                <div class="app-rank-item">
                    <div class="app-rank-num">{row['rank']}</div>
                    <img src="{row['icon_url']}" class="app-icon">
                    <div class="app-info">
                        <div class="app-title">{row['title']}</div>
                        <div class="app-dev">{row['developer']}</div>
                    </div>
                    {score_html}
                </div>
                """
                st.markdown(html, unsafe_allow_html=True)

    render_rank_list(col_free, "🆓 無料トップ (流行)", "free")
    render_rank_list(col_gross, "💰 売上トップ (人気)", "grossing")


# === メイン処理 ===

def main():

    # ★追加: サイドバーで手動更新可能にする
    with st.sidebar:
        st.header("設定")
        if st.button("🔄 データを更新"):
            st.cache_data.clear()
            st.rerun()
        st.markdown(get_custom_css(), unsafe_allow_html=True)
        now = datetime.now(pytz.timezone('Asia/Tokyo'))
        print(f"🔄 [Dashboard] Rendering... ({now.strftime('%H:%M:%S')})")

    try:
        # CSS適用
        st.markdown(get_custom_css(), unsafe_allow_html=True)
        now = datetime.now(pytz.timezone('Asia/Tokyo'))
        print(f"🔄 [Dashboard] Rendering... ({now.strftime('%H:%M:%S')})")

        # データ読み込み
        df_sensor = load_sensor_data(limit=10000)
        df_calendar_sensor = load_calendar_sensor_data(days=35)
        df_weather = load_weather_history(days=40, location='伊丹')
        df_poop = load_generic_data(config.SQLITE_TABLE_DEFECATION)
        df_child = load_generic_data(config.SQLITE_TABLE_CHILD)
        df_food = load_generic_data(config.SQLITE_TABLE_FOOD)
        df_car = load_generic_data(config.SQLITE_TABLE_CAR)
        df_security_log = load_generic_data("security_logs", limit=100)

        # AIレポート表示
        report = load_ai_report()
        if report is not None:
            report_time = pd.to_datetime(report['timestamp']).tz_convert('Asia/Tokyo')
            time_str = report_time.strftime('%H:%M')
            hour = report_time.hour
            icon = "☀️" if 5 <= hour < 11 else ("🕛" if 11 <= hour < 17 else "🌙")
            with st.expander(f"{icon} セバスチャンからの報告 ({time_str}) - タップして読む", expanded=False):
                st.markdown(report['message'].replace('\n', '  \n'))

        # メトリクス（ステータスカード）表示
        render_metrics_section(now, df_sensor, df_car)

        # タブ切り替え
        tab_cal, tab_train, tab_photo, tab_elec, tab_temp, tab_health, tab_taka, tab_log, tab_trends = st.tabs([
            "📅 カレンダー", "🚃 交通", "🖼️ 写真・防犯", "💰 電気・家電", 
            "🌡️ 室温・環境", "🏥 健康・食事", "👵 高砂詳細", "📜 全ログ", "🌟 最近の流行"
        ])

        with tab_cal: render_calendar_tab(df_calendar_sensor, df_child, df_weather)
        with tab_train: render_traffic_tab()
        with tab_photo: render_photos_tab(df_security_log)
        with tab_elec: render_electricity_tab(df_sensor, now)
        with tab_temp: render_temperature_tab(df_sensor, now)
        with tab_health: render_health_tab(df_child, df_poop, df_food)
        with tab_taka: render_takasago_tab(df_sensor)
        with tab_log: render_logs_tab(df_sensor)
        with tab_trends: render_trends_tab()

    except Exception as e:
        err_msg = f"📉 Dashboard Error: {e}"
        logger.error(err_msg)
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": err_msg}], target="discord", channel="error")
        st.error("システムエラーが発生しました。ログを確認してください。")
        st.code(traceback.format_exc())

if __name__ == "__main__":
    main()