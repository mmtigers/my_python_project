# MY_HOME_SYSTEM/views/dashboard/summary.py
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict

from services import train_service
from services import analysis_service
from .common import render_status_card_html

# === Status Helpers ===

def get_takasago_status(df_sensor: pd.DataFrame, now: datetime) -> Tuple[str, str]:
    val = "⚪ データなし"
    theme = "theme-gray"
    if df_sensor.empty or "location" not in df_sensor.columns or "contact_state" not in df_sensor.columns:
        return val, theme

    df_taka = df_sensor[
        (df_sensor["location"] == "高砂") & (df_sensor["contact_state"].isin(["open", "detected"]))
    ]
    if not df_taka.empty:
        last_active = df_taka.iloc[0]["timestamp"]
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

def get_itami_status(df_sensor: pd.DataFrame, now: datetime) -> Tuple[str, str]:
    """伊丹（自宅）のステータス判定（修正版）"""
    val = "⚪ データなし"
    theme = "theme-gray"
    required_cols = ["location", "device_type", "movement_state", "contact_state"]
    if df_sensor.empty or not all(col in df_sensor.columns for col in required_cols):
        return val, theme

    # 1. デバイスタイプの判定: 'Motion' を含むか、または 'Webhook' (SwitchBot) である
    is_motion_device = (
        df_sensor["device_type"].str.contains("Motion", na=False) | 
        (df_sensor["device_type"] == "Webhook")
    )
    
    # 2. 検知ステータスの判定: movement_state または contact_state が 'detected' である
    # (webhook_routerが contact_state に保存してしまう問題への対応)
    is_detected = (
        (df_sensor["movement_state"] == "detected") | 
        (df_sensor["contact_state"] == "detected")
    )

    df_motion = df_sensor[
        (df_sensor["location"] == "伊丹") & 
        is_motion_device & 
        is_detected
    ].sort_values("timestamp", ascending=False)

    if not df_motion.empty:
        diff_m = (now - df_motion.iloc[0]["timestamp"]).total_seconds() / 60
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
        # 開閉センサーのロジック
        df_contact = df_sensor[
            (df_sensor["location"] == "伊丹") & (df_sensor["contact_state"] == "open")
        ].sort_values("timestamp", ascending=False)
        if not df_contact.empty:
            diff_c = (now - df_contact.iloc[0]["timestamp"]).total_seconds() / 60
            if diff_c < 60:
                val = f"🟢 活動中 ({int(diff_c)}分前)"
                theme = "theme-green"
    return val, theme

def get_traffic_status() -> Tuple[str, str]:
    jr_status = train_service.get_jr_traffic_status()
    line_g = jr_status["宝塚線"]
    line_a = jr_status["神戸線"]
    if line_g.get("is_suspended") or line_a.get("is_suspended"):
        return "⛔ 運休発生", "theme-red"
    elif line_g["is_delay"] or line_a["is_delay"]:
        return "⚠️ 遅延あり", "theme-yellow"
    elif line_g.get("is_unavailable") or line_a.get("is_unavailable"):
        # Low修正: 取得不可を「平常運転」と偽らず区別する(遅延見逃し防止)
        return "⚪ 情報取得不可", "theme-gray"
    else:
        return "🟢 平常運転", "theme-green"

def get_server_status() -> Tuple[str, str]:
    mem = analysis_service.get_memory_usage()
    if mem:
        return f"💻 RAM: {int(mem['percent'])}%", "theme-green" if mem["percent"] < 80 else "theme-red"
    return "⚪ 取得失敗", "theme-gray"

def get_nas_status_simple(nas_data: Optional[pd.Series]) -> Tuple[str, str]:
    if nas_data is None: return "⚪ データなし", "theme-gray"
    try:
        if nas_data["status_ping"] == "OK":
            return "🗄️ NAS: 稼働中", "theme-green"
        else:
            return "⚠️ NAS: 応答なし", "theme-red"
    except KeyError:
        return "⚠️ NAS: データ異常", "theme-yellow"

def get_car_status(df_car: pd.DataFrame) -> Tuple[str, str]:
    if not df_car.empty and df_car.iloc[0]["action"] == "LEAVE":
        return "🚗 外出中", "theme-yellow"
    return "🏠 在宅", "theme-green"


def get_rice_status(df_sensor: pd.DataFrame, now: datetime) -> Tuple[str, str]:
    val = "🍚 炊いてない"
    theme = "theme-red"
    # カラム存在チェック
    if "device_name" not in df_sensor.columns or "power_watts" not in df_sensor.columns:
        return val, theme

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 炊飯器の電力データを検索
    df_rice = df_sensor[
        (df_sensor["device_name"].astype(str).str.contains("炊飯器")) &
        (df_sensor["timestamp"] >= today_start)
    ]
    
    if not df_rice.empty:
        max_watts = df_rice["power_watts"].max()
        # 500W以上で稼働していれば「ご飯あり」とみなす
        if max_watts is not None and max_watts >= 500:
            val = "🍚 ご飯あり"
            theme = "theme-green"
    return val, theme

def get_bicycle_status(df_bicycle: pd.DataFrame) -> Tuple[str, str]:
    if df_bicycle.empty: return "⚪ データなし", "theme-gray"
    
    targets = {
        "JR伊丹駅前(第1)自転車駐車場 (A)": "第1A",
        "JR伊丹駅前(第3)自転車駐車場 (A)": "第3A",
        "JR伊丹駅前(第3)自転車駐車場 (E)": "第3E",
    }
    
    # タイムゾーン処理
    if not pd.api.types.is_datetime64_any_dtype(df_bicycle["timestamp"]):
        df_bicycle = df_bicycle.copy()
        df_bicycle["timestamp"] = pd.to_datetime(df_bicycle["timestamp"]).dt.tz_convert("Asia/Tokyo")

    latest_df = df_bicycle.sort_values("timestamp", ascending=False).drop_duplicates("area_name")
    details = []
    total_wait = 0
    has_data = False

    for full_name, short_name in targets.items():
        row = latest_df[latest_df["area_name"] == full_name]
        if not row.empty:
            current_val = int(row.iloc[0]["waiting_count"])
            current_time = row.iloc[0]["timestamp"]
            
            # 前日比計算
            target_time = current_time - timedelta(days=1)
            df_area = df_bicycle[df_bicycle["area_name"] == full_name]
            df_near = df_area[
                (df_area["timestamp"] >= target_time - timedelta(hours=2)) & 
                (df_area["timestamp"] <= target_time + timedelta(hours=2))
            ]
            
            diff_str = ""
            if not df_near.empty:
                nearest_idx = (df_near["timestamp"] - target_time).abs().idxmin()
                past_val = int(df_near.loc[nearest_idx]["waiting_count"])
                diff = current_val - past_val
                if diff > 0: diff_str = f" <span style='color:#d32f2f;'>(🔺{diff})</span>"
                elif diff < 0: diff_str = f" <span style='color:#388e3c;'>(🔻{abs(diff)})</span>"
                else: diff_str = f" <span style='color:#757575;'>(➡️0)</span>"
            else:
                diff_str = " <span style='color:#999;'>(--)</span>"

            details.append(f"{short_name}: <b>{current_val}</b>台{diff_str}")
            total_wait += current_val
            has_data = True
        else:
            details.append(f"{short_name}: -")

    if not has_data: return "⚪ データなし", "theme-gray"
    
    val = f"<div style='font-size:0.85rem; line-height:1.4; text-align:left; display:inline-block;'>{'<br>'.join(details)}</div>"
    theme = "theme-green" if total_wait == 0 else ("theme-yellow" if total_wait < 10 else "theme-red")
    return val, theme

# === Render Function ===

def render_summary(
    now: datetime,
    df_sensor: pd.DataFrame,
    df_car: pd.DataFrame,
    df_bicycle: pd.DataFrame,
    nas_data: Optional[pd.Series],
):
    """トップ画面サマリー描画"""
    taka_val, taka_theme = get_takasago_status(df_sensor, now)
    itami_val, itami_theme = get_itami_status(df_sensor, now)
    car_val, car_theme = get_car_status(df_car)
    
    rice_val, rice_theme = get_rice_status(df_sensor, now)
    cost = analysis_service.calculate_monthly_cost_cumulative()
    elec_val = f"⚡ {cost:,} 円"
    bicycle_val, bicycle_theme = get_bicycle_status(df_bicycle)
    
    traffic_val, traffic_theme = get_traffic_status()
    server_val, server_theme = get_server_status()
    nas_val, nas_theme = get_nas_status_simple(nas_data)

    c1, c2, c3 = st.columns(3)
    c1.markdown(render_status_card_html("👵 高砂 (実家)", taka_val, taka_theme), unsafe_allow_html=True)
    c2.markdown(render_status_card_html("🏠 伊丹 (自宅)", itami_val, itami_theme), unsafe_allow_html=True)
    c3.markdown(render_status_card_html("🚗 車 (伊丹)", car_val, car_theme), unsafe_allow_html=True)

    c4, c5, c6 = st.columns(3)
    c4.markdown(render_status_card_html("🍚 炊飯器", rice_val, rice_theme), unsafe_allow_html=True)
    c5.markdown(render_status_card_html("💰 今月の電気代", elec_val, "theme-blue"), unsafe_allow_html=True)
    c6.markdown(render_status_card_html("🚲 駐輪場待機", bicycle_val, bicycle_theme), unsafe_allow_html=True)

    c7, c8, c9 = st.columns(3)
    c7.markdown(render_status_card_html("🚃 JR運行情報", traffic_val, traffic_theme), unsafe_allow_html=True)
    c8.markdown(render_status_card_html("🖥️ サーバー", server_val, server_theme), unsafe_allow_html=True)
    c9.markdown(render_status_card_html("🗄️ NAS", nas_val, nas_theme), unsafe_allow_html=True)
    
    st.markdown("---")