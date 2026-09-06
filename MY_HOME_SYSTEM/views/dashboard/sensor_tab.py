# MY_HOME_SYSTEM/views/dashboard/sensor_tab.py
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from services import analysis_service

# Issue #451/#453: device_type列に対する判定文字列。デバイスマスタ側の
# 名称が変わると該当データが恒常的に0件になり、グラフが黙って空になる
# (下記のst.warningで気づけるようにしている)。
DEVICE_TYPE_NATURE_REMO_E_LITE = "Nature Remo E Lite"
DEVICE_TYPE_KEYWORD_PLUG = "Plug"
DEVICE_TYPE_KEYWORD_METER = "Meter"

def render_electricity(df_sensor: pd.DataFrame, now: datetime):
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
        df_today = df_sensor[
            (df_sensor["device_type"] == DEVICE_TYPE_NATURE_REMO_E_LITE) &
            (df_sensor["timestamp"] >= today_start) & (df_sensor["timestamp"] < today_end)
        ].copy()
        df_yesterday = df_sensor[
            (df_sensor["device_type"] == DEVICE_TYPE_NATURE_REMO_E_LITE) &
            (df_sensor["timestamp"] >= yesterday_start) & (df_sensor["timestamp"] < today_start)
        ].copy()

        if not df_today.empty or not df_yesterday.empty:
            fig = go.Figure()
            if not df_yesterday.empty:
                df_yesterday["plot_time"] = df_yesterday["timestamp"] + timedelta(days=1)
                fig.add_trace(go.Scatter(x=df_yesterday["plot_time"], y=df_yesterday["power_watts"], mode="lines", name="昨日", line=dict(color="#cccccc", width=2)))
            if not df_today.empty:
                fig.add_trace(go.Scatter(x=df_today["timestamp"], y=df_today["power_watts"], mode="lines", name="今日", line=dict(color="#3366cc", width=3)))
            fig.update_layout(xaxis_range=[today_start, today_end], xaxis_title="時間", yaxis_title="電力(W)", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig, width="stretch")
        else:
            # Issue #453: device_type名の変更等で恒常的に0件になっても気づけるよう警告表示にする
            st.warning(f"{DEVICE_TYPE_NATURE_REMO_E_LITE}のデータがありません")

    with col_right:
        st.subheader("🔌 個別家電 (今日)")
        df_app = df_sensor[
            (df_sensor["device_type"].str.contains(DEVICE_TYPE_KEYWORD_PLUG, na=False)) &
            (df_sensor["timestamp"] >= today_start) & (df_sensor["timestamp"] < today_end)
        ]
        if not df_app.empty:
            fig_app = px.line(df_app, x="timestamp", y="power_watts", color="friendly_name", title="プラグ計測値")
            fig_app.update_xaxes(range=[today_start, today_end])
            st.plotly_chart(fig_app, width="stretch")
        else:
            st.warning("プラグデータなし")

def render_temperature(df_sensor: pd.DataFrame, now: datetime):
    """気温詳細タブ"""
    if df_sensor.empty or "device_type" not in df_sensor.columns:
        st.info("データがありません")
        return
    
    st.subheader("🌡️ 室温・湿度 (今日の推移)")
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    df_temp = df_sensor[
        (df_sensor["device_type"].str.contains(DEVICE_TYPE_KEYWORD_METER, na=False)) &
        (df_sensor["timestamp"] >= today_start) & (df_sensor["timestamp"] < today_end)
    ]

    col1, col2 = st.columns(2)
    with col1:
        if not df_temp.empty:
            fig_t = px.line(df_temp, x="timestamp", y="temperature_celsius", color="friendly_name", title="室温 (℃)")
            fig_t.update_xaxes(range=[today_start, today_end])
            st.plotly_chart(fig_t, width="stretch")
        else:
            st.warning("今日の室温データなし")

    with col2:
        if not df_temp.empty:
            fig_h = px.line(df_temp, x="timestamp", y="humidity_percent", color="friendly_name", title="湿度 (%)")
            fig_h.update_xaxes(range=[today_start, today_end])
            st.plotly_chart(fig_h, width="stretch")
        else:
            st.warning("今日の湿度データなし")

    st.markdown("---")
    st.subheader(f"📅 年間気温・室温推移 ({now.year}年)")
    df_yearly = analysis_service.load_yearly_temperature_stats(now.year)

    if not df_yearly.empty:
        fig = go.Figure()
        if "out_max" in df_yearly.columns:
            fig.add_trace(go.Scatter(x=df_yearly["date"], y=df_yearly["out_max"], mode="lines", name="最高気温(外)", line=dict(color="#ff5252", width=2)))
        if "out_min" in df_yearly.columns:
            fig.add_trace(go.Scatter(x=df_yearly["date"], y=df_yearly["out_min"], mode="lines", name="最低気温(外)", line=dict(color="#448aff", width=2)))
        if "in_max" in df_yearly.columns:
            fig.add_trace(go.Scatter(x=df_yearly["date"], y=df_yearly["in_max"], mode="lines", name="最高室温(内)", line=dict(color="#ff9800", width=2, dash="dot")))
        if "in_min" in df_yearly.columns:
            fig.add_trace(go.Scatter(x=df_yearly["date"], y=df_yearly["in_min"], mode="lines", name="最低室温(内)", line=dict(color="#00bcd4", width=2, dash="dot")))
        fig.update_layout(xaxis_title="日付", yaxis_title="温度(℃)", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), hovermode="x unified")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("年間データがまだありません。")

def render_takasago(df_sensor: pd.DataFrame):
    """高砂実家タブ"""
    if not df_sensor.empty:
        st.subheader("👵 実家ログ")
        st.dataframe(
            df_sensor[df_sensor["location"] == "高砂"][["timestamp", "friendly_name", "contact_state"]].head(50),
            width="stretch",
        )