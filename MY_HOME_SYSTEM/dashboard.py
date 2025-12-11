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

# 自作モジュール
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
    html, body, [class*="css"] { font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif; }
    .stMetric { background-color: #ffffff; padding: 10px; border-radius: 12px; border: 1px solid #e0e0e0; box-shadow: 0 2px 4px rgba(0,0,0,0.05); text-align: center; }
    div.fc-event-main { color: #333 !important; font-weight: bold; font-size: 0.9em; padding: 2px; }
    h3 { color: #2c3e50; border-bottom: 2px solid #a0c4ff; padding-bottom: 5px; margin-top: 30px; }
</style>
""", unsafe_allow_html=True)

# === 設定ファイルの強制リロード ===
importlib.reload(config)

# === データ取得関数 ===
@st.cache_data(ttl=60)
def load_data(table_name, limit=3000):
    try:
        conn = sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)
        query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            if df['timestamp'].dt.tz is None:
                df['timestamp'] = df['timestamp'].dt.tz_localize('Asia/Tokyo')
            else:
                df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Tokyo')
        return df
    except Exception:
        return pd.DataFrame()

# === 名前解決ロジック ===
def apply_friendly_names(df):
    if df.empty:
        df['friendly_name'] = []
        return df

    id_to_name = {d['id']: d.get('name', d['id']) for d in config.MONITOR_DEVICES}
    id_to_loc = {d['id']: d.get('location', 'Unknown') for d in config.MONITOR_DEVICES}

    def get_name(row):
        dev_id = row.get('device_id')
        if dev_id in id_to_name: return id_to_name[dev_id]
        db_name = row.get('device_name')
        if db_name and db_name != "Unknown": return db_name
        return dev_id or "不明"

    df['friendly_name'] = df.apply(get_name, axis=1)
    if 'location' not in df.columns:
        df['location'] = df['device_id'].map(lambda x: id_to_loc.get(x, 'Unknown'))
    return df

# === メイン処理 ===
def main():
    st.title("🏠 我が家の司令塔 Pro")
    st.caption(f"System Mk-V | 最終更新: {datetime.now().strftime('%H:%M:%S')}")

    # サイドバー
    if st.sidebar.button("🔄 データを最新にする", type="primary"):
        st.cache_data.clear()
        importlib.reload(config)
        st.rerun()

    # データロード
    df_sensor = load_data(config.SQLITE_TABLE_SENSOR, limit=5000)
    df_poop = load_data(config.SQLITE_TABLE_DEFECATION, limit=500)
    df_child = load_data(config.SQLITE_TABLE_CHILD, limit=500)
    df_food = load_data(config.SQLITE_TABLE_FOOD, limit=100)
    df_car = load_data(config.SQLITE_TABLE_CAR, limit=100)

    # 名前適用
    df_sensor = apply_friendly_names(df_sensor)

    # 日付
    now = datetime.now(pytz.timezone('Asia/Tokyo'))
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ==========================================
    # 1. トップサマリー (5列構成)
    # ==========================================
    st.markdown("### 🌞 本日の状況")
    
    # --- 集計 ---
    last_active_str = "データなし"
    count_fridge = 0
    count_toilet = 0
    elec_cost = 0
    car_status = "🏠 在宅"

    if not df_sensor.empty:
        # 高砂データ
        df_taka = df_sensor[df_sensor['location'] == '高砂']
        
        # 1. 最終活動時間の計算 (復活機能)
        # 活動とみなす条件: 開いた(open) か 動いた(detected) のみ。定期的なcloseは無視。
        mask_active = (
            (df_taka['contact_state'].isin(['open', 'detected', 'timeOutNotClose'])) |
            (df_taka['movement_state'] == 'detected')
        )
        df_active = df_taka[mask_active].sort_values('timestamp', ascending=False)
        
        if not df_active.empty:
            last_ts = df_active.iloc[0]['timestamp']
            diff = now - last_ts
            mins = int(diff.total_seconds() / 60)
            if mins < 60: last_active_str = f"{mins}分前"
            elif mins < 1440: last_active_str = f"{int(mins/60)}時間前"
            else: last_active_str = f"{int(mins/1440)}日前"

        # 2. 回数カウント (今日)
        df_today_taka = df_taka[df_taka['timestamp'] >= start_of_today]
        
        # 冷蔵庫
        count_fridge = len(df_today_taka[
            (df_today_taka['friendly_name'].str.contains('冷蔵庫')) & 
            (df_today_taka['contact_state'].isin(['open', 'detected']))
        ])
        # トイレ (高砂)
        count_toilet = len(df_today_taka[
            (df_today_taka['friendly_name'].str.contains('トイレ')) & 
            (df_today_taka['contact_state'].isin(['open', 'detected']))
        ])

    # 3. 電気代 (伊丹)
    if not df_sensor.empty:
        df_power = df_sensor[(df_sensor['device_type'] == 'Nature Remo E Lite') & (df_sensor['timestamp'] >= start_of_today)]
        if not df_power.empty:
            avg_w = df_power['power_watts'].mean()
            elec_cost = int((avg_w * (now - start_of_today).total_seconds() / 3600 / 1000) * 31)

    # 4. 車
    if not df_car.empty and df_car.iloc[0]['action'] == "LEAVE":
        car_status = "🚗 外出中"

    # --- 表示 ---
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("👴 高砂の活動", last_active_str, help="最後にセンサーが反応してからの時間")
    c2.metric("🧊 高砂の冷蔵庫", f"{count_fridge} 回", help="今日開いた回数")
    c3.metric("🚽 高砂のトイレ", f"{count_toilet} 回", help="今日使用された回数")
    c4.metric("⚡ 今日の電気代", f"{elec_cost} 円", help="伊丹の電気代目安")
    c5.metric("🚗 車 (伊丹)", car_status)

    st.markdown("---")

    # ==========================================
    # 2. タブコンテンツ
    # ==========================================
    tabs = st.tabs(["📅 カレンダー", "💰 家計・電気", "👵 高砂詳細", "💩 体調", "🛡️ 防犯", "🍽️ 食事", "🖼️ 写真"])

    # --- Tab 1: 総合カレンダー (回数集計) ---
    with tabs[0]:
        st.subheader("📅 生活カレンダー")
        events = []
        
        if not df_sensor.empty:
            df_sensor['date_str'] = df_sensor['timestamp'].dt.strftime('%Y-%m-%d')
            targets = [
                {"key": "冷蔵庫", "label": "🧊 冷蔵庫", "color": "#87CEFA"},
                {"key": "トイレ", "label": "🚽 トイレ", "color": "#DDA0DD"},
                {"key": "玄関", "label": "🚪 玄関", "color": "#90EE90"},
                {"key": "人感", "label": "👀 動き", "color": "#FFDAB9"}
            ]
            for t in targets:
                # フレンドリーネームで検索
                mask = (df_sensor['friendly_name'].str.contains(t['key'])) & \
                       (df_sensor['contact_state'].isin(['open', 'detected']))
                df_target = df_sensor[mask]
                
                if not df_target.empty:
                    counts = df_target.groupby('date_str').size()
                    for date_str, count in counts.items():
                        events.append({
                            "title": f"{t['label']} ({count}回)",
                            "start": date_str, "allDay": True,
                            "backgroundColor": t['color'], "borderColor": t['color'], "textColor": "#333"
                        })

        if not df_poop.empty:
            for _, row in df_poop.iterrows():
                events.append({"title": f"💩 {row['condition']}", "start": row['timestamp'].isoformat(), "backgroundColor": "#FFD700"})
        if not df_child.empty:
            for _, row in df_child.iterrows():
                if "元気" not in row['condition']:
                    events.append({"title": f"🏥 {row['child_name']}", "start": row['timestamp'].isoformat(), "backgroundColor": "#FF69B4"})

        calendar(events=events, options={
            "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,listWeek"},
            "initialView": "dayGridMonth", "height": 750, "locale": "ja"
        }, key='cal_v5')

    # --- Tab 2: 電気代 (名前表示) ---
    with tabs[1]:
        st.subheader("💰 電気代と金食い虫ランキング")
        if not df_sensor.empty:
            df_plugs = df_sensor[df_sensor['device_type'].str.contains('Plug')]
            if not df_plugs.empty:
                latest = df_plugs.sort_values('timestamp').groupby('device_id').tail(1)
                active = latest[latest['power_watts'] > 3.0].copy()
                
                c1, c2 = st.columns([1, 1])
                with c1:
                    if not active.empty:
                        fig = px.pie(active, values='power_watts', names='friendly_name', title='今のシェア')
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("大きな電力消費なし")
                with c2:
                    st.markdown("##### 🏆 消費ランキング")
                    if not active.empty:
                        active = active.sort_values('power_watts', ascending=False)
                        for _, row in active.iterrows():
                            yen = (row['power_watts'] / 1000) * 31
                            st.write(f"**{row['friendly_name']}**: {row['power_watts']}W (約{yen:.1f}円/時)")
                    else:
                        st.write("静かです。")

    # --- Tab 3: 高砂詳細 (名前表示) ---
    with tabs[2]:
        st.subheader("👵 高砂の実家 見守りボード")
        if not df_sensor.empty:
            df_taka = df_sensor[df_sensor['location'] == '高砂']
            if not df_taka.empty:
                st.markdown("##### 📝 最近の活動 (直近20件)")
                cond = (
                    (df_taka['contact_state'].isin(['detected', 'open', 'timeOutNotClose'])) | 
                    ((df_taka['contact_state'] == 'close') & (df_taka['device_type'] == 'Webhook Device'))
                )
                df_act = df_taka[cond].sort_values('timestamp', ascending=False).head(20)
                if not df_act.empty:
                    show = df_act[['timestamp', 'friendly_name', 'contact_state']].copy()
                    show.columns = ['日時', '場所', '状態']
                    st.dataframe(show, use_container_width=True)
                else:
                    st.info("データなし")

    # --- Tab 4~6 ---
    with tabs[3]: # 体調
        if not df_poop.empty: st.dataframe(df_poop[['timestamp', 'user_name', 'condition']], use_container_width=True)
    with tabs[4]: # 防犯
        if not df_sensor.empty:
            df_intr = df_sensor[df_sensor['contact_state'] == 'intrusion']
            if not df_intr.empty:
                df_intr = apply_friendly_names(df_intr)
                st.error("侵入検知ログ")
                st.dataframe(df_intr[['timestamp', 'friendly_name']], use_container_width=True)
            else:
                st.success("異常なし")
    with tabs[5]: # 食事
        if not df_food.empty: st.dataframe(df_food, use_container_width=True)
    with tabs[6]: # 写真
        img_dir = os.path.join(config.BASE_DIR, "..", "assets", "snapshots")
        imgs = sorted(glob.glob(os.path.join(img_dir, "*.jpg")), reverse=True)
        if imgs:
            cols = st.columns(4)
            for i, p in enumerate(imgs[:12]):
                cols[i%4].image(p, caption=os.path.basename(p), use_container_width=True)
        else:
            st.info("写真なし")

if __name__ == "__main__":
    try: main()
    except Exception:
        st.error("エラーが発生しました")
        st.code(traceback.format_exc())