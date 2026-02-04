# MY_HOME_SYSTEM/views/dashboard/misc_tab.py
import streamlit as st
import pandas as pd
import plotly.express as px
import os
import glob
from datetime import datetime, timedelta
import pytz

import config
import train_service
from .common import render_status_card_html

def render_traffic():
    st.subheader("🚃 JR宝塚線・神戸線 運行状況")
    jr_status = train_service.get_jr_traffic_status()
    line_g = jr_status["宝塚線"]
    line_a = jr_status["神戸線"]

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
    now_jst = datetime.now(pytz.timezone("Asia/Tokyo"))
    dep_time = (now_jst + timedelta(minutes=20)).strftime("%H:%M")
    st.subheader(f"📍 ルート検索 ({dep_time} 出発想定)")
    
    current_hour = now_jst.hour
    container = st.container()
    if 4 <= current_hour < 12:
        _render_route_search(container, "伊丹(兵庫県)", "長岡京", "📤 出勤ルート")
    elif 12 <= current_hour <= 23:
        _render_route_search(container, "長岡京", "伊丹(兵庫県)", "📥 帰宅ルート")
    else:
        st.caption("※深夜帯のため帰宅ルートを表示します")
        _render_route_search(container, "長岡京", "伊丹(兵庫県)", "📥 帰宅ルート")

def _render_route_search(col, from_st: str, to_st: str, label_icon: str):
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

def render_photos(df_security_log: pd.DataFrame):
    st.subheader("🖼️ カメラ・ギャラリー")
    img_dir = os.path.join(config.ASSETS_DIR, "snapshots")
    images = sorted(glob.glob(os.path.join(img_dir, "*.jpg")), reverse=True)
    if images:
        cols_img = st.columns(4)
        for i, p in enumerate(images[:4]):
            cols_img[i].image(p, caption=os.path.basename(p), width="stretch")
        with st.expander("📂 過去の写真"):
            cols_past = st.columns(4)
            for i, p in enumerate(images[4:20]):
                cols_past[i % 4].image(p, caption=os.path.basename(p), width="stretch")
    else:
        st.info("写真なし")

    st.subheader("🛡️ 防犯ログ (検知分類)")
    if not df_security_log.empty:
        cols = ["timestamp", "friendly_name"]
        if "classification" in df_security_log.columns: cols.append("classification")
        if "image_path" in df_security_log.columns: cols.append("image_path")
        df_disp = df_security_log[cols].copy()
        df_disp.columns = [c.replace("timestamp", "検知時刻").replace("friendly_name", "デバイス").replace("classification", "検知種別").replace("image_path", "画像") for c in df_disp.columns]
        st.dataframe(df_disp, width="stretch")
    else:
        st.info("不審な検知はありません")

def render_bicycle(df_bicycle: pd.DataFrame):
    st.title("🚲 駐輪場待機数推移")
    if df_bicycle.empty:
        st.info("駐輪場データがまだありません。")
        return

    target_areas = [
        "JR伊丹駅前(第1)自転車駐車場 (A)",
        "JR伊丹駅前(第3)自転車駐車場 (A)",
        "JR伊丹駅前(第3)自転車駐車場 (E)",
    ]
    df_target = df_bicycle[df_bicycle["area_name"].isin(target_areas)].copy()

    if df_target.empty:
        st.warning("指定されたエリアのデータが見つかりません。")
        return

    fig = px.line(df_target, x="timestamp", y="waiting_count", color="area_name", title="待機人数の変化", markers=True, symbol="area_name")
    fig.update_layout(xaxis_title="日時", yaxis_title="待機数 (人/台)", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    st.plotly_chart(fig, width="stretch")

    st.subheader("📊 最新の状況")
    latest_df = df_target.sort_values("timestamp", ascending=False).drop_duplicates("area_name")
    st.dataframe(latest_df[["timestamp", "area_name", "waiting_count", "status_text"]].sort_values("area_name"), width="stretch")