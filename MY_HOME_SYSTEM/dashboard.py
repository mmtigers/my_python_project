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

# 設定リロード（開発中の変更反映用）
importlib.reload(config)

# === 🎨 デザイン・CSS定義 ===
def get_custom_css():
    """主婦向けの見やすく優しいデザイン定義"""
    return """
    <style>
        /* 全体フォント: 読みやすさ重視 */
        html, body, [class*="css"] { 
            font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif; 
        }
        
        /* メトリックカード: カード風のデザインで区切りを明確に */
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
        
        /* AIレポートボックス: 目立つが優しい色合い */
        .ai-report-box {
            background-color: #e3f2fd; 
            border-left: 6px solid #2196f3;
            padding: 16px; 
            border-radius: 8px; 
            margin-bottom: 24px; 
            color: #0d47a1;
            font-size: 1.05rem;
            line-height: 1.6;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .ai-icon { font-size: 1.8rem; margin-right: 12px; vertical-align: middle; }
        .ai-title { font-weight: bold; font-size: 1.1rem; vertical-align: middle; }

        /* 画像ギャラリー */
        .photo-caption { font-size: 0.8rem; color: #555; text-align: center; }
    </style>
    """

# === 🛠️ データ処理ロジック ===

def get_db_connection():
    """DB接続を取得（読み取り専用）"""
    return sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)

def apply_friendly_names(df):
    """データフレームに日本語名と場所をマッピングする"""
    if df.empty: return df
    
    # マッピング辞書の作成
    id_map = {d['id']: d.get('name', d['id']) for d in config.MONITOR_DEVICES}
    loc_map = {d['id']: d.get('location', 'その他') for d in config.MONITOR_DEVICES}
    
    # マッピング適用（見つからない場合は既存のdevice_nameを使用）
    df['friendly_name'] = df['device_id'].map(id_map).fillna(df['device_name'])
    df['location'] = df['device_id'].map(loc_map).fillna('その他')
    return df

@st.cache_data(ttl=60)
def load_generic_data(table_name, limit=500):
    """汎用テーブル読み込み（キャッシュ付き）"""
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
    """センサーデータ読み込み＆名前解決（キャッシュ付き）"""
    print(f"📥 [Dashboard] Loading sensors (limit={limit})...")
    conn = None
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(f"SELECT * FROM {config.SQLITE_TABLE_SENSOR} ORDER BY timestamp DESC LIMIT {limit}", conn)
        
        if df.empty: return df

        # タイムゾーン処理
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
    """最新のAIレポートを取得"""
    conn = None
    try:
        conn = get_db_connection()
        # テーブル存在確認
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (config.SQLITE_TABLE_AI_REPORT,))
        if not cur.fetchone(): return None
        
        df = pd.read_sql_query(f"SELECT * FROM {config.SQLITE_TABLE_AI_REPORT} ORDER BY id DESC LIMIT 1", conn)
        return df.iloc[0] if not df.empty else None
    except Exception:
        return None
    finally:
        if conn: conn.close()

# === 🖥️ メイン表示ロジック ===
def main():
    # CSS適用
    st.markdown(get_custom_css(), unsafe_allow_html=True)
    
    now = datetime.now(pytz.timezone('Asia/Tokyo'))
    print(f"🔄 [Dashboard] Rendering... ({now.strftime('%H:%M:%S')})")

    # 1. AI執事メッセージ (最優先表示)
    report = load_ai_report()
    if report is not None:
        report_time = pd.to_datetime(report['timestamp']).tz_convert('Asia/Tokyo').strftime('%m/%d %H:%M')
        st.markdown(f"""
        <div class="ai-report-box">
            <span class="ai-icon">🎩</span>
            <span class="ai-title">執事からの報告 ({report_time})</span><br>
            {report['message'].replace(chr(10), '<br>')}
        </div>
        """, unsafe_allow_html=True)

    # データロード
    df_sensor = load_sensor_data()
    df_poop = load_generic_data(config.SQLITE_TABLE_DEFECATION)
    df_child = load_generic_data(config.SQLITE_TABLE_CHILD)
    df_food = load_generic_data(config.SQLITE_TABLE_FOOD)
    df_car = load_generic_data(config.SQLITE_TABLE_CAR)

    # 2. ステータスメトリクス (トップ表示)
    # 実家の様子
    taka_msg = "⚪ データなし"
    if not df_sensor.empty:
        # 高砂の接触センサー(open/detected)
        df_taka = df_sensor[(df_sensor['location']=='高砂') & (df_sensor['contact_state'].isin(['open','detected']))]
        if not df_taka.empty:
            last_active = df_taka.iloc[0]['timestamp']
            diff_min = (now - last_active).total_seconds() / 60
            
            if diff_min < 60: taka_msg = "🟢 元気 (1時間以内)"
            elif diff_min < 180: taka_msg = "🟡 静か (3時間以内)"
            else: taka_msg = f"🔴 {int(diff_min/60)}時間動きなし"

    # 電気代予測
    pred_cost = 0
    if not df_sensor.empty:
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0)
        df_elec = df_sensor[(df_sensor['device_type']=='Nature Remo E Lite') & (df_sensor['timestamp'] >= start_of_month)]
        if not df_elec.empty:
            avg_watts = df_elec['power_watts'].mean()
            # 予測計算: 平均W * 24h * 30日 * 31円 / 1000
            pred_cost = int((avg_watts * 24 * 30 / 1000) * 31)

    # 車の状況
    car_msg = "🏠 在宅"
    if not df_car.empty:
        last_action = df_car.iloc[0]['action']
        if last_action == 'LEAVE':
            car_msg = "🚗 外出中"

    # 今日のトイレ回数
    toilet_count = 0
    if not df_sensor.empty:
        today_start = now.replace(hour=0, minute=0, second=0)
        df_toilet = df_sensor[
            (df_sensor['friendly_name'].str.contains('トイレ')) & 
            (df_sensor['contact_state'].isin(['open','detected'])) &
            (df_sensor['timestamp'] >= today_start)
        ]
        toilet_count = len(df_toilet)

    # カラム表示
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("👵 高砂 (実家)", taka_msg)
    col2.metric("⚡ 電気予報(月)", f"約 {pred_cost:,} 円")
    col3.metric("🚗 車 (伊丹)", car_msg)
    col4.metric("🚽 今日のトイレ", f"{toilet_count} 回")

    st.markdown("---")

    # ==========================================
    # 3. 機能別タブコンテンツ
    # ==========================================
    tabs = st.tabs([
        "📅 カレンダー", "🖼️ 写真・防犯", "💰 電気・家電", 
        "🏥 健康・食事", "👵 高砂詳細", "📜 全ログ"
    ])

    # Tab 1: カレンダー (主要イベントのみ)
    with tabs[0]:
        calendar_events = []
        if not df_sensor.empty:
            df_sensor['date_str'] = df_sensor['timestamp'].dt.strftime('%Y-%m-%d')
            
            # 冷蔵庫・トイレの回数
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
        
        # 健康ログ
        if not df_child.empty:
            for _, row in df_child.iterrows():
                if "元気" not in row['condition']:
                    calendar_events.append({
                        "title": f"🏥{row['child_name']}", "start": row['timestamp'].isoformat(), 
                        "color": "#ffb703", "textColor": "#333"
                    })

        calendar(events=calendar_events, options={"initialView": "dayGridMonth", "height": 600}, key="main_calendar")

    # Tab 2: 写真・防犯
    with tabs[1]:
        st.subheader("🖼️ カメラ・ギャラリー")
        img_dir = os.path.join(config.BASE_DIR, "..", "assets", "snapshots")
        images = sorted(glob.glob(os.path.join(img_dir, "*.jpg")), reverse=True)
        
        if images:
            st.markdown("##### 最新のスナップショット")
            cols_img = st.columns(4)
            for i, img_path in enumerate(images[:4]):
                cols_img[i].image(img_path, caption=os.path.basename(img_path), use_container_width=True)
            
            with st.expander("📂 過去の写真を見る"):
                cols_past = st.columns(4)
                for i, img_path in enumerate(images[4:20]):
                    cols_past[i % 4].image(img_path, caption=os.path.basename(img_path), use_container_width=True)
        else:
            st.info("保存された写真はありません")

        st.subheader("🛡️ 防犯・侵入検知")
        if not df_sensor.empty:
            df_security = df_sensor[df_sensor['contact_state'] == 'intrusion']
            if not df_security.empty:
                st.error("⚠️ 侵入検知ログがあります")
                st.dataframe(df_security[['timestamp', 'friendly_name', 'location']], use_container_width=True)
            else:
                st.success("✅ 異常なし (侵入検知記録なし)")

    # Tab 3: 電気・家電
    with tabs[2]:
        if not df_sensor.empty:
            col_graph, col_pie = st.columns([2, 1])
            with col_graph:
                st.subheader("⚡ 消費電力推移 (24h)")
                df_power = df_sensor[
                    (df_sensor['device_type'].str.contains('Plug|Nature')) & 
                    (df_sensor['timestamp'] >= now - timedelta(hours=24))
                ]
                if not df_power.empty:
                    fig = px.line(df_power, x='timestamp', y='power_watts', color='friendly_name', 
                                  labels={'timestamp': '時間', 'power_watts': '電力(W)', 'friendly_name': '機器'})
                    st.plotly_chart(fig, use_container_width=True)
            
            with col_pie:
                st.subheader("🏆 電力シェア")
                if not df_power.empty:
                    # 最新の値を取得して円グラフ化
                    latest_power = df_power.sort_values('timestamp').groupby('device_id').tail(1)
                    latest_power = latest_power[latest_power['power_watts'] > 1] # 待機電力などは除外
                    if not latest_power.empty:
                        fig_pie = px.pie(latest_power, values='power_watts', names='friendly_name', title='現在の稼働状況')
                        st.plotly_chart(fig_pie, use_container_width=True)

    # Tab 4: 健康・食事
    with tabs[3]:
        col_health, col_poop = st.columns(2)
        with col_health:
            st.markdown("##### 🏥 子供の体調")
            if not df_child.empty:
                st.dataframe(df_child[['timestamp', 'child_name', 'condition']], use_container_width=True)
            else:
                st.info("記録なし")
        with col_poop:
            st.markdown("##### 💩 お腹・排便")
            if not df_poop.empty:
                st.dataframe(df_poop[['timestamp', 'user_name', 'condition']], use_container_width=True)
            else:
                st.info("記録なし")
        
        st.markdown("##### 🍽️ 食事ログ")
        if not df_food.empty:
            st.dataframe(df_food[['timestamp', 'menu_category']], use_container_width=True)

    # Tab 5: 高砂詳細
    with tabs[4]:
        if not df_sensor.empty:
            st.subheader("👵 実家のセンサーログ")
            df_taka_log = df_sensor[df_sensor['location']=='高砂']
            st.dataframe(df_taka_log[['timestamp', 'friendly_name', 'contact_state']].head(50), use_container_width=True)

    # Tab 6: 全ログ
    with tabs[5]:
        if not df_sensor.empty:
            locations = df_sensor['location'].unique()
            selected_loc = st.multiselect("場所フィルタ", locations, default=locations)
            
            df_filtered = df_sensor[df_sensor['location'].isin(selected_loc)]
            st.dataframe(
                df_filtered[['timestamp', 'friendly_name', 'location', 'contact_state', 'power_watts', 'temperature_celsius']].head(200),
                use_container_width=True
            )

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_msg = traceback.format_exc()
        st.error("システムエラーが発生しました")
        st.code(err_msg)
        
        # エラー発生時はDiscordへ通知 (commonモジュール利用)
        print(f"❌ Critical Dashboard Error: {e}")
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"📉 **Dashboard Error**\n```{str(e)}```"}], target="discord", channel="error")