# MY_HOME_SYSTEM/dashboard.py
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from streamlit_calendar import calendar
import os
import glob
from datetime import datetime, timedelta
import pytz
import traceback
import importlib
import sys
import numpy as np

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
    """主婦向けの見やすく優しいデザイン定義"""
    return """
    <style>
        html, body, [class*="css"] { 
            font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif; 
        }
        
        div[data-testid="stMetric"] {
            background-color: #ffffff; 
            padding: 15px; 
            border-radius: 12px;
            border: 1px solid #e0e0e0; 
            box-shadow: 0 2px 4px rgba(0,0,0,0.05); 
            text-align: center;
        }
        div[data-testid="stMetricLabel"] { font-size: 0.9rem; color: #666; }
        div[data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: bold; color: #2c3e50; }
        
        .ai-report-box {
            background-color: #e3f2fd; 
            border-left: 6px solid #2196f3;
            padding: 16px; 
            border-radius: 8px; 
            margin-bottom: 24px; 
            color: #0d47a1;
            font-size: 1.0rem;
            line-height: 1.5; /* 行間を詰める */
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .ai-icon { font-size: 1.8rem; margin-right: 12px; vertical-align: middle; }
        .ai-title { font-weight: bold; font-size: 1.1rem; vertical-align: middle; }
    </style>
    """

# === 🛠️ データ処理ロジック ===

def get_db_connection():
    """DB接続を取得（読み取り専用）"""
    return sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)

def apply_friendly_names(df):
    """データフレームに日本語名と場所をマッピングする"""
    if df.empty: return df
    
    id_map = {d['id']: d.get('name', d['id']) for d in config.MONITOR_DEVICES}
    loc_map = {d['id']: d.get('location', 'その他') for d in config.MONITOR_DEVICES}
    
    df['friendly_name'] = df['device_id'].map(id_map).fillna(df['device_name'])
    df['location'] = df['device_id'].map(loc_map).fillna('その他')
    return df

@st.cache_data(ttl=60)
def load_generic_data(table_name, limit=500):
    print(f"📥 [Dashboard] Loading {table_name}...")
    conn = None
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}", conn)
        if not df.empty and 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Tokyo')
        return df
    except Exception as e:
        print(f"❌ Error loading {table_name}: {e}")
        return pd.DataFrame()
    finally:
        if conn: conn.close()

@st.cache_data(ttl=60)
def load_sensor_data(limit=5000):
    print(f"📥 [Dashboard] Loading sensors (limit={limit})...")
    conn = None
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(f"SELECT * FROM {config.SQLITE_TABLE_SENSOR} ORDER BY timestamp DESC LIMIT {limit}", conn)
        
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
    finally:
        if conn: conn.close()

def load_ai_report():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (config.SQLITE_TABLE_AI_REPORT,))
        if not cur.fetchone(): return None
        
        df = pd.read_sql_query(f"SELECT * FROM {config.SQLITE_TABLE_AI_REPORT} ORDER BY id DESC LIMIT 1", conn)
        return df.iloc[0] if not df.empty else None
    except Exception:
        return None
    finally:
        if conn: conn.close()

def calculate_monthly_cost_cumulative():
    """今月の電気代累積値を計算 (積分法)"""
    conn = None
    try:
        conn = get_db_connection()
        now = datetime.now(pytz.timezone('Asia/Tokyo'))
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
        
        # 今月のNature Remoデータを全取得
        query = f"""
            SELECT timestamp, power_watts
            FROM {config.SQLITE_TABLE_SENSOR} 
            WHERE device_type = 'Nature Remo E Lite' AND timestamp >= '{start_of_month}'
            ORDER BY timestamp ASC
        """
        df = pd.read_sql_query(query, conn)
        
        if df.empty: return 0
        
        # タイムスタンプ処理
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('Asia/Tokyo')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Tokyo')

        # 積分計算 (台形公式に近い形で: 電力 × 時間差)
        # 時間差(h)を計算
        df['time_diff'] = df['timestamp'].diff().dt.total_seconds() / 3600
        df = df.dropna(subset=['time_diff']) # 先頭行は差分なし
        
        # 異常値除外 (接続切れなどで長時間あいた場合、その間ずっと高出力だったことにしないよう、例えば1時間以上は除外)
        df = df[df['time_diff'] <= 1.0]
        
        # 前回の電力値を使って計算 (簡易矩形近似)
        # kWh = kW * h = (W / 1000) * h
        df['kwh'] = (df['power_watts'] / 1000) * df['time_diff']
        
        total_kwh = df['kwh'].sum()
        return int(total_kwh * 31)
        
    except Exception as e:
        print(f"❌ Cost calculation error: {e}")
        return 0
    finally:
        if conn: conn.close()

# === 🖥️ メイン表示ロジック ===
def main():
    st.markdown(get_custom_css(), unsafe_allow_html=True)
    now = datetime.now(pytz.timezone('Asia/Tokyo'))
    print(f"🔄 [Dashboard] Rendering... ({now.strftime('%H:%M:%S')})")

    # 1. AI執事メッセージ
    report = load_ai_report()
    if report is not None:
        report_time = pd.to_datetime(report['timestamp']).tz_convert('Asia/Tokyo').strftime('%m/%d %H:%M')
        # 改行を最小限に: ダブル改行は<br>、シングルはスペースに
        clean_msg = report['message'].replace('\n\n', '<br>').replace('\n', ' ')
        
        st.markdown(f"""
        <div class="ai-report-box">
            <span class="ai-icon">🎩</span>
            <span class="ai-title">執事からの報告 ({report_time})</span><br>
            {clean_msg}
        </div>
        """, unsafe_allow_html=True)

    # データロード
    df_sensor = load_sensor_data()
    df_poop = load_generic_data(config.SQLITE_TABLE_DEFECATION)
    df_child = load_generic_data(config.SQLITE_TABLE_CHILD)
    df_food = load_generic_data(config.SQLITE_TABLE_FOOD)
    df_car = load_generic_data(config.SQLITE_TABLE_CAR)

    # 2. ステータスメトリクス
    # 実家の様子
    taka_msg = "⚪ データなし"
    if not df_sensor.empty:
        df_taka = df_sensor[(df_sensor['location']=='高砂') & (df_sensor['contact_state'].isin(['open','detected']))]
        if not df_taka.empty:
            last_active = df_taka.iloc[0]['timestamp']
            diff_min = (now - last_active).total_seconds() / 60
            if diff_min < 60: taka_msg = "🟢 元気 (1時間以内)"
            elif diff_min < 180: taka_msg = "🟡 静か (3時間以内)"
            else: taka_msg = f"🔴 {int(diff_min/60)}時間動きなし"

    # 電気代 (累積)
    current_cost = calculate_monthly_cost_cumulative()

    # 車の状況
    car_msg = "🏠 在宅"
    if not df_car.empty:
        if df_car.iloc[0]['action'] == 'LEAVE': car_msg = "🚗 外出中"

    # 今日のトイレ回数 (伊丹のみであることを明記)
    toilet_count = 0
    toilet_label = "🚽 トイレ"
    if not df_sensor.empty:
        today_start = now.replace(hour=0, minute=0, second=0)
        
        # 場所ごとにカウント
        df_toilet = df_sensor[
            (df_sensor['friendly_name'].str.contains('トイレ')) & 
            (df_sensor['contact_state'].isin(['open','detected'])) &
            (df_sensor['timestamp'] >= today_start)
        ]
        
        # 高砂のトイレがあるか確認
        taka_toilet = df_toilet[df_toilet['location'] == '高砂']
        if taka_toilet.empty:
            toilet_label = "🚽 トイレ (伊丹)"
            
        toilet_count = len(df_toilet)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("👵 高砂 (実家)", taka_msg)
    col2.metric("⚡ 今月の電気代 (累積)", f"{current_cost:,} 円")
    col3.metric("🚗 車 (伊丹)", car_msg)
    col4.metric(toilet_label, f"{toilet_count} 回")

    st.markdown("---")

    # ==========================================
    # 3. 機能別タブ
    # ==========================================
    tabs = st.tabs([
        "📅 カレンダー", "🖼️ 写真・防犯", "💰 電気・家電", 
        "🏥 健康・食事", "👵 高砂詳細", "📜 全ログ"
    ])

    # Tab 1: カレンダー
    with tabs[0]:
        calendar_events = []
        if not df_sensor.empty:
            df_sensor['date_str'] = df_sensor['timestamp'].dt.strftime('%Y-%m-%d')
            
            for key, label, color in [('冷蔵庫', '🧊冷蔵庫', '#a8dadc'), ('トイレ', '🚽トイレ', '#ffccd5')]:
                df_target = df_sensor[
                    (df_sensor['friendly_name'].str.contains(key)) & 
                    (df_sensor['contact_state'].isin(['open','detected']))
                ]
                if not df_target.empty:
                    counts = df_target.groupby('date_str').size()
                    for date_val, count in counts.items():
                        calendar_events.append({
                            "title": f"{label}: {count}回", "start": date_val, 
                            "color": color, "textColor": "#333", "allDay": True
                        })
        if not df_child.empty:
            for _, row in df_child.iterrows():
                if "元気" not in row['condition']:
                    calendar_events.append({"title": f"🏥{row['child_name']}", "start": row['timestamp'].isoformat(), "color": "#ffb703", "textColor": "#333"})

        calendar(events=calendar_events, options={"initialView": "dayGridMonth", "height": 600}, key="cal_main")

    # Tab 2: 写真・防犯
    with tabs[1]:
        st.subheader("🖼️ カメラ・ギャラリー")
        img_dir = os.path.join(config.BASE_DIR, "..", "assets", "snapshots")
        images = sorted(glob.glob(os.path.join(img_dir, "*.jpg")), reverse=True)
        if images:
            cols_img = st.columns(4)
            for i, img_path in enumerate(images[:4]):
                cols_img[i].image(img_path, caption=os.path.basename(img_path), use_container_width=True)
            with st.expander("📂 過去の写真"):
                cols_past = st.columns(4)
                for i, img_path in enumerate(images[4:20]):
                    cols_past[i%4].image(img_path, caption=os.path.basename(img_path), use_container_width=True)
        else:
            st.info("写真なし")

        st.subheader("🛡️ 防犯ログ")
        if not df_sensor.empty:
            df_sec = df_sensor[df_sensor['contact_state'] == 'intrusion']
            if not df_sec.empty:
                st.error("⚠️ 侵入検知あり")
                st.dataframe(df_sec[['timestamp', 'friendly_name', 'location']], use_container_width=True)

    # Tab 3: 電気・家電 (修正: 分離)
    with tabs[2]:
        if not df_sensor.empty:
            col_left, col_right = st.columns([1, 1])
            
            # 家全体（スマートメーター）
            with col_left:
                st.subheader("⚡ 家全体の消費電力 (24h)")
                df_total = df_sensor[
                    (df_sensor['device_type'] == 'Nature Remo E Lite') & 
                    (df_sensor['timestamp'] >= now - timedelta(hours=24))
                ]
                if not df_total.empty:
                    fig_total = px.line(df_total, x='timestamp', y='power_watts', 
                                      title="スマートメーター計測値", labels={'timestamp': '時間', 'power_watts': '電力(W)'})
                    st.plotly_chart(fig_total, use_container_width=True)
                else:
                    st.info("スマートメーターデータなし")

            # 個別家電
            with col_right:
                st.subheader("🔌 個別家電の推移 (24h)")
                df_app = df_sensor[
                    (df_sensor['device_type'].str.contains('Plug')) & 
                    (df_sensor['timestamp'] >= now - timedelta(hours=24))
                ]
                if not df_app.empty:
                    fig_app = px.line(df_app, x='timestamp', y='power_watts', color='friendly_name',
                                    title="各プラグの計測値", labels={'timestamp': '時間', 'power_watts': '電力(W)'})
                    st.plotly_chart(fig_app, use_container_width=True)
                else:
                    st.info("プラグデータなし")
            
            st.markdown("---")
            
            # 電力シェア (スマートメーター除外)
            st.subheader("🏆 家電別・電力シェア (現在の稼働状況)")
            if not df_sensor.empty:
                # Nature Remoを除外して最新取得
                df_latest = df_sensor[df_sensor['device_type'] != 'Nature Remo E Lite'].sort_values('timestamp').groupby('device_id').tail(1)
                # プラグ系のみ、かつ1W以上
                df_pie = df_latest[
                    (df_latest['device_type'].str.contains('Plug')) & 
                    (df_latest['power_watts'] > 1)
                ]
                if not df_pie.empty:
                    fig_pie = px.pie(df_pie, values='power_watts', names='friendly_name', title='内訳 (スマートメーター除く)')
                    st.plotly_chart(fig_pie, use_container_width=True)
                else:
                    st.info("稼働中の個別家電はありません")

    # Tab 4: 健康・食事
    with tabs[3]:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### 🏥 子供の体調")
            if not df_child.empty: st.dataframe(df_child[['timestamp', 'child_name', 'condition']], use_container_width=True)
        with c2:
            st.markdown("##### 💩 お腹・排便")
            if not df_poop.empty: st.dataframe(df_poop[['timestamp', 'user_name', 'condition']], use_container_width=True)
        st.markdown("##### 🍽️ 食事ログ")
        if not df_food.empty: st.dataframe(df_food[['timestamp', 'menu_category']], use_container_width=True)

    # Tab 5: 高砂詳細
    with tabs[4]:
        if not df_sensor.empty:
            st.subheader("👵 実家のログ")
            df_taka_log = df_sensor[df_sensor['location']=='高砂']
            st.dataframe(df_taka_log[['timestamp', 'friendly_name', 'contact_state']].head(50), use_container_width=True)

    # Tab 6: 全ログ
    with tabs[5]:
        if not df_sensor.empty:
            locs = df_sensor['location'].unique()
            sel = st.multiselect("場所", locs, default=locs)
            st.dataframe(df_sensor[df_sensor['location'].isin(sel)][['timestamp', 'friendly_name', 'location', 'contact_state', 'power_watts']].head(200), use_container_width=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"📉 Dashboard Error: {e}"}], target="discord", channel="error")
        st.error("エラーが発生しました")
        st.code(traceback.format_exc())