# MY_HOME_SYSTEM/dashboard.py
import logging
import os
import shutil
import glob
import sqlite3
import subprocess
import traceback
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pytz
import requests
import streamlit as st

# 自作モジュール
import common
import config
import tools.financial_service as financial_service
import train_service

# === ロガー設定 ===
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# === 定数・設定 ===
st.set_page_config(
    page_title="My Home Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

FRIENDLY_NAME_FIXES: Dict[str, str] = {
    "リビング": "高砂のリビング",
    "１Fの洗面所": "高砂の洗面所",
    "居間": "伊丹のリビング",
    "仕事部屋": "伊丹の書斎",
    "人感センサー": "高砂のトイレ(人感)",
}

CUSTOM_CSS: str = """
<style>
    html, body, [class*="css"] { 
        font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif; 
    }
    .status-card {
        padding: 10px 5px;
        border-radius: 10px;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        margin-bottom: 8px;
        height: 90px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }
    .status-title {
        font-size: 0.8rem; color: #555; margin-bottom: 5px; font-weight: bold; opacity: 0.8;
    }
    .status-value {
        font-size: 1.1rem; font-weight: bold; line-height: 1.2; white-space: normal; 
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

# === ヘルパー関数: データ処理 ===

def get_ro_db_connection() -> sqlite3.Connection:
    """
    読み取り専用でデータベース接続を取得します。
    pandas.read_sql_query等で使用するためにConnectionオブジェクトを返します。
    Note: 呼び出し元で必ず close() するか、コンテキストマネージャを使用してください。
    """
    # 既存のロジックを踏襲し、READ ONLYモードを指定
    return sqlite3.connect(
        f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True, timeout=10.0
    )


def process_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """DataFrameのタイムスタンプを日本時間に変換し、表示名を適用する共通処理"""
    if df.empty or "timestamp" not in df.columns:
        return df

    # タイムゾーン変換
    # コピーを作成して警告を抑制
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC").dt.tz_convert("Asia/Tokyo")
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Tokyo")

    return df


def apply_friendly_names(df: pd.DataFrame) -> pd.DataFrame:
    """デバイスIDから表示名への変換と、特定の名称置換を行う"""
    if df.empty:
        return df

    df = df.copy()

    # 0. カラム名の揺らぎ吸収
    if "device_id" not in df.columns and "camera_id" in df.columns:
        df["device_id"] = df["camera_id"]

    # 1. 必須カラム 'device_id' の存在チェック
    if "device_id" not in df.columns:
        # UIが落ちないように最低限の列を埋める
        df["friendly_name"] = "Unknown"
        df["location"] = "その他"
        return df

    # 2. Configからデフォルトのマッピングを作成
    id_map = {d["id"]: d.get("name", d["id"]) for d in config.MONITOR_DEVICES}
    
    # 3. ロケーションマップの作成
    loc_map = {d["id"]: d.get("location", "その他") for d in config.MONITOR_DEVICES}

    # 4. DB内の「最新のデバイス名」を取得してマッピングを上書き
    #    (注: 複数テーブルからの統合データの場合、device_nameが含まれていない場合もあるためチェック)
    if "device_name" in df.columns and "timestamp" in df.columns:
        try:
            latest_df = df.sort_values("timestamp", ascending=False)
            latest_df = latest_df.drop_duplicates(subset="device_id", keep="first")
            valid_latest = latest_df[latest_df["device_name"].notna() & (latest_df["device_name"] != "")]
            db_latest_map = valid_latest.set_index("device_id")["device_name"].to_dict()
            id_map.update(db_latest_map)
        except Exception as e:
            logger.warning(f"Friendly name mapping update failed: {e}")

    # 5. マッピングの適用
    df["friendly_name"] = df["device_id"].map(id_map)
    
    # マッピングで見つからなかった場合は device_name -> device_id の順でフォールバック
    if "device_name" in df.columns:
        df["friendly_name"] = df["friendly_name"].fillna(df["device_name"])
    df["friendly_name"] = df["friendly_name"].fillna(df["device_id"])

    # 6. ロケーションの適用
    df["location"] = df["device_id"].map(loc_map).fillna("その他")

    # 7. 名称の微調整
    df["friendly_name"] = df["friendly_name"].replace(FRIENDLY_NAME_FIXES)

    return df


@st.cache_data(ttl=60)
def load_data_from_db(query: str, date_column: str = "timestamp") -> pd.DataFrame:
    """汎用データロード関数"""
    conn = None
    try:
        conn = get_ro_db_connection()
        df = pd.read_sql_query(query, conn)
        
        if date_column in df.columns:
            if date_column != "timestamp":
                df.rename(columns={date_column: "timestamp"}, inplace=True)
            
            df = process_dataframe(df)
            
            if date_column != "timestamp":
                df.rename(columns={"timestamp": date_column}, inplace=True)

        return df
    except Exception as e:
        logger.error(f"Data Load Error (Query: {query[:30]}...): {e}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()


def load_nas_status() -> Optional[pd.Series]:
    """NASの最新状態を取得"""
    table_name = getattr(config, "SQLITE_TABLE_NAS", "nas_records")
    conn = None
    try:
        conn = get_ro_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"
        )
        if not cur.fetchone():
            return None
        cur.close()
        conn.close()
        conn = None

        query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT 1"
        df = load_data_from_db(query)
        return df.iloc[0] if not df.empty else None
    except Exception as e:
        logger.error(f"NAS Data Load Error: {e}")
        if conn:
            conn.close()
        return None


# 個別のデータロード関数群
def load_generic_data(table_name: str, limit: int = 500) -> pd.DataFrame:
    query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
    return load_data_from_db(query)


def load_sensor_data(limit: int = 5000) -> pd.DataFrame:
    """
    【v1.0.0対応】新旧テーブルからセンサーデータを統合して取得する
    Target Tables:
      1. device_records (Legacy / Other sensors)
      2. switchbot_meter_logs (Temperature / Humidity)
      3. power_usage (Electricity)
    """
    # 1. Legacy / Others (開閉センサー等)
    query_legacy = f"""
        SELECT timestamp, device_id, device_name, device_type, 
               temperature_celsius, humidity_percent, power_watts, 
               contact_state, movement_state, brightness_state
        FROM device_records 
        ORDER BY timestamp DESC LIMIT {limit}
    """
    df_legacy = load_data_from_db(query_legacy)

    # 2. SwitchBot Meter Logs (New: 温湿度)
    # カラム名を旧仕様 (temperature_celsius, humidity_percent) にエイリアスして取得
    query_meter = f"""
        SELECT timestamp, device_id, device_name, 
               temperature as temperature_celsius, 
               humidity as humidity_percent
        FROM {config.SQLITE_TABLE_SWITCHBOT_LOGS}
        ORDER BY timestamp DESC LIMIT {limit}
    """
    df_meter = load_data_from_db(query_meter)
    if not df_meter.empty:
        df_meter["device_type"] = "Meter"

    # 3. Power Usage (New: 電力)
    # カラム名を旧仕様 (power_watts) にエイリアスして取得
    query_power = f"""
        SELECT timestamp, device_id, device_name, 
               wattage as power_watts
        FROM {config.SQLITE_TABLE_POWER_USAGE}
        ORDER BY timestamp DESC LIMIT {limit}
    """
    df_power = load_data_from_db(query_power)
    if not df_power.empty:
        # Nature Remo E Lite か Plug かは device_name 等で区別が必要だが、
        # いったん 'Nature Remo E Lite' と仮定するか、既存ロジックに任せる
        # ここでは後段のロジックが device_type='Nature Remo E Lite' を期待している箇所があるため補完
        df_power["device_type"] = df_power["device_name"].apply(
            lambda x: "Nature Remo E Lite" if x and "Remo" in str(x) else "Plug"
        )
        # device_nameがない場合
        df_power["device_type"] = df_power["device_type"].replace("Plug", "Nature Remo E Lite") 

    # --- 統合 ---
    df_list = []
    if not df_legacy.empty: df_list.append(df_legacy)
    if not df_meter.empty: df_list.append(df_meter)
    if not df_power.empty: df_list.append(df_power)

    if not df_list:
        return pd.DataFrame()

    df_merged = pd.concat(df_list, ignore_index=True)
    
    # 統合後の再ソート
    if "timestamp" in df_merged.columns:
        # load_data_from_db で既に型変換されているはずだが念のため
        df_merged["timestamp"] = pd.to_datetime(df_merged["timestamp"])
        df_merged = df_merged.sort_values("timestamp", ascending=False).reset_index(drop=True)

    # 表示名適用
    return apply_friendly_names(df_merged).head(limit)


def get_ngrok_url() -> Dict[str, str]:
    """ngrokの現在の公開URLを取得する"""
    try:
        res = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        if res.status_code == 200:
            data = res.json()
            urls = {}
            for t in data.get("tunnels", []):
                addr = t.get("config", {}).get("addr", "")
                if "8000" in addr:
                    urls["server"] = t.get("public_url")
                elif "8501" in addr:
                    urls["dashboard"] = t.get("public_url")
            return urls
    except Exception:
        pass
    return {}


def get_disk_usage() -> Optional[Dict[str, float]]:
    """ディスク使用量を取得"""
    try:
        total, used, free = shutil.disk_usage("/")
        return {
            "total_gb": total // (2**30),
            "used_gb": used // (2**30),
            "free_gb": free // (2**30),
            "percent": (used / total) * 100,
        }
    except Exception as e:
        logger.error(f"Disk usage check failed: {e}")
        return None


def get_memory_usage() -> Optional[Dict[str, float]]:
    """メモリ使用状況を取得"""
    try:
        res = subprocess.run(["free", "-m"], capture_output=True, text=True, check=False)
        lines = res.stdout.strip().split("\n")

        if len(lines) >= 2:
            parts = lines[1].split()
            total = int(parts[1])
            used = int(parts[2])
            available = int(parts[6])

            percent = (used / total) * 100 if total > 0 else 0

            return {
                "total_mb": total,
                "used_mb": used,
                "available_mb": available,
                "percent": percent,
            }
    except Exception as e:
        logger.error(f"Memory check failed: {e}")
        pass
    return None


def get_system_logs(lines: int = 50, priority: Optional[str] = None, target_date: Optional[date] = None) -> str:
    """Systemdのログを取得"""
    try:
        cmd = ["journalctl", "-u", "home_system.service", "--no-pager"]
        if target_date:
            since_str = f"{target_date} 00:00:00"
            until_str = f"{target_date} 23:59:59"
            cmd.extend(["--since", since_str, "--until", until_str, "-n", "5000"])
        else:
            cmd.extend(["-n", str(lines)])

        if priority:
            cmd.extend(["-p", priority])

        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.stdout
    except Exception as e:
        return f"ログ取得エラー: {e}"


@st.cache_data(ttl=300)
def load_weather_history(days: int = 40, location: str = "伊丹") -> pd.DataFrame:
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    query = f"""
        SELECT date, min_temp, max_temp, weather_desc, umbrella_level 
        FROM weather_history 
        WHERE location = '{location}' AND date >= '{start_date}'
    """
    try:
        conn = get_ro_db_connection()
        df = pd.read_sql_query(query, conn)
        return df
    except Exception as e:
        logger.error(f"Weather Load Error: {e}")
        return pd.DataFrame()
    finally:
        if 'conn' in locals() and conn:
            conn.close()


@st.cache_data(ttl=3600)
def load_yearly_temperature_stats(year: int, location: str = "伊丹") -> pd.DataFrame:
    """指定年の外気温と室温(伊丹)の日次統計を取得"""
    conn = get_ro_db_connection()
    try:
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

        q_weather = f"""
            SELECT date, max_temp as out_max, min_temp as out_min
            FROM weather_history
            WHERE location = '{location}' AND date >= '{start_date}' AND date <= '{end_date}'
        """
        df_weather = pd.read_sql_query(q_weather, conn)

        # 温室度の取得：load_sensor_data は重いので直接SQLで集計する
        # ここも新旧テーブル両方を見る必要があるが、簡略化のため新テーブル優先で結合
        
        # 伊丹のデバイスIDを取得
        itami_ids = [
            d["id"] for d in config.MONITOR_DEVICES if d.get("location") == location
        ]
        if not itami_ids:
            return df_weather
            
        ids_str = "'" + "','".join(itami_ids) + "'"

        # 新テーブル (switchbot_meter_logs) から集計
        q_new = f"""
            SELECT 
                substr(timestamp, 1, 10) as date,
                MAX(temperature) as in_max,
                MIN(temperature) as in_min
            FROM {config.SQLITE_TABLE_SWITCHBOT_LOGS}
            WHERE 
                timestamp >= '{start_date}' AND timestamp <= '{end_date}T23:59:59'
                AND device_id IN ({ids_str})
                AND temperature IS NOT NULL
            GROUP BY date
        """
        
        # 旧テーブル (device_records) から集計
        q_old = f"""
            SELECT 
                substr(timestamp, 1, 10) as date,
                MAX(temperature_celsius) as in_max,
                MIN(temperature_celsius) as in_min
            FROM device_records
            WHERE 
                timestamp >= '{start_date}' AND timestamp <= '{end_date}T23:59:59'
                AND device_id IN ({ids_str})
                AND temperature_celsius IS NOT NULL
            GROUP BY date
        """
        
        # 実行と結合
        df_new = pd.DataFrame()
        df_old = pd.DataFrame()
        
        try:
            df_new = pd.read_sql_query(q_new, conn)
        except Exception:
            pass # テーブルがない場合など
            
        try:
            df_old = pd.read_sql_query(q_old, conn)
        except Exception:
            pass

        # 結合処理
        if not df_new.empty and not df_old.empty:
            df_sensor = pd.concat([df_new, df_old]).groupby("date").agg({
                "in_max": "max",
                "in_min": "min"
            }).reset_index()
        elif not df_new.empty:
            df_sensor = df_new
        else:
            df_sensor = df_old

        if df_weather.empty and df_sensor.empty:
            return pd.DataFrame()

        if df_weather.empty:
            df_merged = df_sensor
        elif df_sensor.empty:
            df_merged = df_weather
        else:
            df_merged = pd.merge(df_weather, df_sensor, on="date", how="outer")

        return df_merged.sort_values("date")

    except Exception as e:
        logger.error(f"Yearly Temp Load Error: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_ranking_dates(limit: int = 3) -> List[str]:
    conn = get_ro_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='app_rankings'"
        )
        if not cur.fetchone():
            return []

        query = f"SELECT DISTINCT date FROM app_rankings ORDER BY date DESC LIMIT {limit}"
        df = pd.read_sql_query(query, conn)
        return df["date"].tolist()
    except Exception as e:
        logger.error(f"Ranking Dates Load Error: {e}")
        return []
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_ranking_data(date_str: str, ranking_type: str) -> pd.DataFrame:
    conn = get_ro_db_connection()
    try:
        query = f"""
            SELECT rank, title, app_id 
            FROM app_rankings 
            WHERE date = '{date_str}' AND ranking_type = '{ranking_type}'
            ORDER BY rank ASC
        """
        return pd.read_sql_query(query, conn)
    except Exception as e:
        logger.error(f"Ranking Data Load Error: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_bicycle_data(limit: int = 2000) -> pd.DataFrame:
    table_name = getattr(config, "SQLITE_TABLE_BICYCLE", "bicycle_parking_records")
    conn = None
    try:
        conn = get_ro_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"
        )
        if not cur.fetchone():
            return pd.DataFrame()
        conn.close()
        conn = None

        query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
        return load_data_from_db(query)
    except Exception as e:
        logger.error(f"Bicycle Data Load Error: {e}")
        if conn:
            conn.close()
        return pd.DataFrame()


def load_ai_report() -> Optional[pd.Series]:
    query = f"SELECT * FROM {config.SQLITE_TABLE_AI_REPORT} ORDER BY id DESC LIMIT 1"
    df = load_data_from_db(query)
    return df.iloc[0] if not df.empty else None


def calculate_monthly_cost_cumulative() -> int:
    """今月の電気代概算 (v1.0.0対応: power_usage優先)"""
    try:
        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0).isoformat()

        # 1. 新テーブル (power_usage) から取得
        query = f"""
            SELECT timestamp, wattage as power_watts 
            FROM {config.SQLITE_TABLE_POWER_USAGE} 
            WHERE timestamp >= '{start_of_month}'
            ORDER BY timestamp ASC
        """
        df = load_data_from_db(query)

        # 2. 新テーブルが空なら旧テーブル (device_records) へフォールバック
        if df.empty:
            query_old = f"""
                SELECT timestamp, power_watts FROM device_records
                WHERE device_type = 'Nature Remo E Lite' AND timestamp >= '{start_of_month}'
                ORDER BY timestamp ASC
            """
            df = load_data_from_db(query_old)

        if df.empty:
            return 0

        df["time_diff"] = df["timestamp"].diff().dt.total_seconds() / 3600
        df = df.dropna(subset=["time_diff"])
        df = df[df["time_diff"] <= 1.0] # 異常な間隔を除外

        df["kwh"] = (df["power_watts"] / 1000) * df["time_diff"]
        # 概算単価 31円/kWh
        return int(df["kwh"].sum() * 31)
    except Exception as e:
        logger.error(f"Cost Calc Error: {e}")
        return 0


# === ロジック層: ステータス判定 ===


def get_takasago_status(df_sensor: pd.DataFrame, now: datetime) -> Tuple[str, str]:
    """高砂の実家のステータス判定"""
    val = "⚪ データなし"
    theme = "theme-gray"

    if df_sensor.empty:
        return val, theme

    df_taka = df_sensor[
        (df_sensor["location"] == "高砂")
        & (df_sensor["contact_state"].isin(["open", "detected"]))
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
    """伊丹（自宅）のステータス判定"""
    val = "⚪ データなし"
    theme = "theme-gray"

    if df_sensor.empty:
        return val, theme

    # 人感センサー優先
    df_motion = df_sensor[
        (df_sensor["location"] == "伊丹")
        & (df_sensor["device_type"].str.contains("Motion", na=False))
        & (df_sensor["movement_state"] == "detected")
    ].sort_values("timestamp", ascending=False)

    if not df_motion.empty:
        last_mov = df_motion.iloc[0]["timestamp"]
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
            (df_sensor["location"] == "伊丹") & (df_sensor["contact_state"] == "open")
        ].sort_values("timestamp", ascending=False)

        if not df_contact.empty:
            last_c = df_contact.iloc[0]["timestamp"]
            diff_c = (now - last_c).total_seconds() / 60
            if diff_c < 60:
                val = f"🟢 活動中 ({int(diff_c)}分前)"
                theme = "theme-green"

    return val, theme


def get_rice_status(df_sensor: pd.DataFrame, now: datetime) -> Tuple[str, str]:
    """炊飯器ステータス判定: その日の最大電力が500W超かで判定"""
    val = "🍚 炊いてない"
    theme = "theme-red"

    # device_nameカラムがない可能性があるためチェック
    if "device_name" not in df_sensor.columns or "power_watts" not in df_sensor.columns:
        return val, theme

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # メモリ上のDataFrameから判定（重いクエリを避ける）
    df_rice = df_sensor[
        (df_sensor["device_name"].astype(str).str.contains("炊飯器")) &
        (df_sensor["timestamp"] >= today_start)
    ]

    if not df_rice.empty:
        max_watts = df_rice["power_watts"].max()
        if max_watts is not None and max_watts >= 500:
            val = "🍚 ご飯あり"
            theme = "theme-green"

    return val, theme


def get_traffic_status() -> Tuple[str, str, Dict, Dict]:
    """交通情報ステータス"""
    jr_status = train_service.get_jr_traffic_status()
    line_g = jr_status["宝塚線"]
    line_a = jr_status["神戸線"]

    if line_g.get("is_suspended") or line_a.get("is_suspended"):
        return "⛔ 運休発生", "theme-red", line_g, line_a
    elif line_g["is_delay"] or line_a["is_delay"]:
        return "⚠️ 遅延あり", "theme-yellow", line_g, line_a
    else:
        return "🟢 平常運転", "theme-green", line_g, line_a


def get_car_status(df_car: pd.DataFrame) -> Tuple[str, str]:
    """車ステータス"""
    val = "🏠 在宅"
    theme = "theme-green"
    if not df_car.empty and df_car.iloc[0]["action"] == "LEAVE":
        val = "🚗 外出中"
        theme = "theme-yellow"
    return val, theme


def get_bicycle_status(df_bicycle: pd.DataFrame) -> Tuple[str, str]:
    """駐輪場ステータス (主要3エリアの個別表示 + 前日比)"""
    if df_bicycle.empty:
        return "⚪ データなし", "theme-gray"

    targets = {
        "JR伊丹駅前(第1)自転車駐車場 (A)": "第1A",
        "JR伊丹駅前(第3)自転車駐車場 (A)": "第3A",
        "JR伊丹駅前(第3)自転車駐車場 (E)": "第3E",
    }

    if not pd.api.types.is_datetime64_any_dtype(df_bicycle["timestamp"]):
        df_bicycle = df_bicycle.copy()
        df_bicycle["timestamp"] = pd.to_datetime(df_bicycle["timestamp"]).dt.tz_convert(
            "Asia/Tokyo"
        )

    latest_df = df_bicycle.sort_values(
        "timestamp", ascending=False
    ).drop_duplicates("area_name")

    details = []
    total_wait = 0
    has_data = False

    for full_name, short_name in targets.items():
        row = latest_df[latest_df["area_name"] == full_name]

        if not row.empty:
            current_val = int(row.iloc[0]["waiting_count"])
            current_time = row.iloc[0]["timestamp"]

            df_area = df_bicycle[df_bicycle["area_name"] == full_name]
            target_time = current_time - timedelta(days=1)
            
            df_near = df_area[
                (df_area["timestamp"] >= target_time - timedelta(hours=2))
                & (df_area["timestamp"] <= target_time + timedelta(hours=2))
            ]

            diff_str = ""
            if not df_near.empty:
                nearest_idx = (df_near["timestamp"] - target_time).abs().idxmin()
                past_val = int(df_near.loc[nearest_idx]["waiting_count"])

                diff = current_val - past_val
                if diff > 0:
                    diff_str = f" <span style='color:#d32f2f;'>(🔺{diff})</span>"
                elif diff < 0:
                    diff_str = f" <span style='color:#388e3c;'>(🔻{abs(diff)})</span>"
                else:
                    diff_str = f" <span style='color:#757575;'>(➡️0)</span>"
            else:
                diff_str = " <span style='color:#999;'>(--)</span>"

            details.append(f"{short_name}: <b>{current_val}</b>台{diff_str}")
            total_wait += current_val
            has_data = True
        else:
            details.append(f"{short_name}: -")

    if not has_data:
        return "⚪ データなし", "theme-gray"

    val = f"<div style='font-size:0.85rem; line-height:1.4; text-align:left; display:inline-block;'>{'<br>'.join(details)}</div>"

    if total_wait == 0:
        theme = "theme-green"
    elif total_wait < 10:
        theme = "theme-yellow"
    else:
        theme = "theme-red"

    return val, theme


def get_server_status() -> Tuple[str, str]:
    """サーバー稼働ステータス"""
    mem = get_memory_usage()
    if mem:
        val = f"💻 RAM: {int(mem['percent'])}%"
        theme = "theme-green" if mem["percent"] < 80 else "theme-red"
    else:
        val = "⚪ 取得失敗"
        theme = "theme-gray"
    return val, theme


def get_nas_status_simple(nas_data: Optional[pd.Series]) -> Tuple[str, str]:
    """NAS簡易ステータス"""
    if nas_data is None:
        return "⚪ データなし", "theme-gray"

    try:
        if nas_data["status_ping"] == "OK":
            val = "🗄️ NAS: 稼働中"
            theme = "theme-green"
        else:
            val = "⚠️ NAS: 応答なし"
            theme = "theme-red"
    except KeyError:
        val = "⚠️ NAS: データ異常"
        theme = "theme-yellow"

    return val, theme


# === UI層: 描画コンポーネント ===


def render_status_card_html(title: str, value: str, theme: str) -> str:
    return f"""
    <div class="status-card {theme}">
        <div class="status-title">{title}</div>
        <div class="status-value">{value}</div>
    </div>
    """


def render_dashboard_summary(
    now: datetime,
    df_sensor: pd.DataFrame,
    df_car: pd.DataFrame,
    df_bicycle: pd.DataFrame,
    nas_data: Optional[pd.Series],
):
    """トップ画面のサマリー（3x3 グリッド）を描画"""

    # --- ステータス取得 ---
    taka_val, taka_theme = get_takasago_status(df_sensor, now)
    itami_val, itami_theme = get_itami_status(df_sensor, now)
    car_val, car_theme = get_car_status(df_car)

    rice_val, rice_theme = get_rice_status(df_sensor, now)
    cost = calculate_monthly_cost_cumulative()
    elec_val = f"⚡ {cost:,} 円"
    bicycle_val, bicycle_theme = get_bicycle_status(df_bicycle)

    traffic_val, traffic_theme, _, _ = get_traffic_status()
    server_val, server_theme = get_server_status()
    nas_val, nas_theme = get_nas_status_simple(nas_data)

    # --- 描画 (3列x3行) ---
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            render_status_card_html("👵 高砂 (実家)", taka_val, taka_theme),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            render_status_card_html("🏠 伊丹 (自宅)", itami_val, itami_theme),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            render_status_card_html("🚗 車 (伊丹)", car_val, car_theme),
            unsafe_allow_html=True,
        )

    c4, c5, c6 = st.columns(3)
    with c4:
        st.markdown(
            render_status_card_html("🍚 炊飯器", rice_val, rice_theme),
            unsafe_allow_html=True,
        )
    with c5:
        st.markdown(
            render_status_card_html("💰 今月の電気代", elec_val, "theme-blue"),
            unsafe_allow_html=True,
        )
    with c6:
        st.markdown(
            render_status_card_html("🚲 駐輪場待機", bicycle_val, bicycle_theme),
            unsafe_allow_html=True,
        )

    c7, c8, c9 = st.columns(3)
    with c7:
        st.markdown(
            render_status_card_html("🚃 JR運行情報", traffic_val, traffic_theme),
            unsafe_allow_html=True,
        )
    with c8:
        st.markdown(
            render_status_card_html("🖥️ サーバー", server_val, server_theme),
            unsafe_allow_html=True,
        )
    with c9:
        st.markdown(
            render_status_card_html("🗄️ NAS", nas_val, nas_theme),
            unsafe_allow_html=True,
        )

    st.markdown("---")


def render_traffic_tab():
    """交通情報タブの描画"""
    st.subheader("🚃 JR宝塚線・神戸線 運行状況")
    _, _, line_g, line_a = get_traffic_status()

    c_t1, c_t2 = st.columns(2)

    for col, line, name in [(c_t1, line_g, "JR 宝塚線"), (c_t2, line_a, "JR 神戸線")]:
        bg_color = "#ffebee" if line["is_delay"] else "#e8f5e9"
        status_color = "#d32f2f" if line["is_delay"] else "#2e7d32"
        with col:
            st.markdown(
                f"""
            <div style="background-color:{bg_color}; padding:15px; border-radius:10px; border:1px solid #ccc;">
                <h3 style="margin:0; color:#333;">{name}</h3>
                <h2 style="margin:5px 0; color:{status_color};">{line['status']}</h2>
                <p style="margin:0;">{line['detail']}</p>
            </div>
            """,
                unsafe_allow_html=True,
            )

    st.markdown("---")
    now_jst = datetime.now(pytz.timezone("Asia/Tokyo"))
    current_hour = now_jst.hour
    dep_time = (now_jst + timedelta(minutes=20)).strftime("%H:%M")

    st.subheader(f"📍 ルート検索 ({dep_time} 出発想定)")
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
                    if "⬇️" in d:
                        steps.append(f"<div class='line-node'>{d}</div>")
                    elif "🔄" in d:
                        steps.append(f"<div class='transfer-mark'>{d}</div>")
                    else:
                        steps.append(f"<div class='station-node'>{d}</div>")
                details_html = f"<div class='route-path'>{''.join(steps)}</div>"

            st.markdown(
                f"""
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
            """,
                unsafe_allow_html=True,
            )
            if data["url"]:
                st.link_button(f"🔗 Yahoo!路線情報で見る", data["url"])
        else:
            st.warning("ルート情報を取得できませんでした")


def render_photos_tab(df_security_log: pd.DataFrame):
    """写真・防犯タブ"""
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
                cols_past[i % 4].image(
                    p, caption=os.path.basename(p), width="stretch"
                )
    else:
        st.info("写真なし")

    st.subheader("🛡️ 防犯ログ (検知分類)")
    if not df_security_log.empty:
        df_security_log = apply_friendly_names(df_security_log)
        cols = ["timestamp", "friendly_name"]
        if "classification" in df_security_log.columns:
            cols.append("classification")
        if "image_path" in df_security_log.columns:
            cols.append("image_path")
        df_disp = df_security_log[cols].copy()
        df_disp.columns = [
            c.replace("timestamp", "検知時刻")
            .replace("friendly_name", "デバイス")
            .replace("classification", "検知種別")
            .replace("image_path", "画像")
            for c in df_disp.columns
        ]
        st.dataframe(df_disp, width="stretch")
    else:
        st.info("不審な検知はありません")


def render_electricity_tab(df_sensor: pd.DataFrame, now: datetime):
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
            (df_sensor["device_type"] == "Nature Remo E Lite")
            & (df_sensor["timestamp"] >= today_start)
            & (df_sensor["timestamp"] < today_end)
        ].copy()
        df_yesterday = df_sensor[
            (df_sensor["device_type"] == "Nature Remo E Lite")
            & (df_sensor["timestamp"] >= yesterday_start)
            & (df_sensor["timestamp"] < today_start)
        ].copy()

        if not df_today.empty or not df_yesterday.empty:
            fig = go.Figure()
            if not df_yesterday.empty:
                df_yesterday["plot_time"] = df_yesterday["timestamp"] + timedelta(days=1)
                fig.add_trace(
                    go.Scatter(
                        x=df_yesterday["plot_time"],
                        y=df_yesterday["power_watts"],
                        mode="lines",
                        name="昨日",
                        line=dict(color="#cccccc", width=2),
                    )
                )
            if not df_today.empty:
                fig.add_trace(
                    go.Scatter(
                        x=df_today["timestamp"],
                        y=df_today["power_watts"],
                        mode="lines",
                        name="今日",
                        line=dict(color="#3366cc", width=3),
                    )
                )
            fig.update_layout(
                xaxis_range=[today_start, today_end],
                xaxis_title="時間",
                yaxis_title="電力(W)",
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
                ),
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("データがありません")

    with col_right:
        st.subheader("🔌 個別家電 (今日)")
        df_app = df_sensor[
            (df_sensor["device_type"].str.contains("Plug", na=False))
            & (df_sensor["timestamp"] >= today_start)
            & (df_sensor["timestamp"] < today_end)
        ]
        if not df_app.empty:
            fig_app = px.line(
                df_app,
                x="timestamp",
                y="power_watts",
                color="friendly_name",
                title="プラグ計測値",
            )
            fig_app.update_xaxes(range=[today_start, today_end])
            st.plotly_chart(fig_app, width="stretch")
        else:
            st.info("プラグデータなし")


def render_temperature_tab(df_sensor: pd.DataFrame, now: datetime):
    if df_sensor.empty or "device_type" not in df_sensor.columns:
        st.info("データがありません")
        return
    
    # 今日の推移
    st.subheader("🌡️ 室温・湿度 (今日の推移)")
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    df_temp = df_sensor[
        (df_sensor["device_type"].str.contains("Meter", na=False))
        & (df_sensor["timestamp"] >= today_start)
        & (df_sensor["timestamp"] < today_end)
    ]

    col1, col2 = st.columns(2)
    with col1:
        if not df_temp.empty:
            fig_t = px.line(
                df_temp,
                x="timestamp",
                y="temperature_celsius",
                color="friendly_name",
                title="室温 (℃)",
            )
            fig_t.update_xaxes(range=[today_start, today_end])
            st.plotly_chart(fig_t, width="stretch")
        else:
            st.info("今日の室温データなし")

    with col2:
        if not df_temp.empty:
            fig_h = px.line(
                df_temp,
                x="timestamp",
                y="humidity_percent",
                color="friendly_name",
                title="湿度 (%)",
            )
            fig_h.update_xaxes(range=[today_start, today_end])
            st.plotly_chart(fig_h, width="stretch")
        else:
            st.info("今日の湿度データなし")

    st.markdown("---")

    # 年間推移グラフ
    st.subheader(f"📅 年間気温・室温推移 ({now.year}年)")
    df_yearly = load_yearly_temperature_stats(now.year)

    if not df_yearly.empty:
        fig = go.Figure()

        if "out_max" in df_yearly.columns:
            fig.add_trace(
                go.Scatter(
                    x=df_yearly["date"],
                    y=df_yearly["out_max"],
                    mode="lines",
                    name="最高気温(外)",
                    line=dict(color="#ff5252", width=2),
                )
            )

        if "out_min" in df_yearly.columns:
            fig.add_trace(
                go.Scatter(
                    x=df_yearly["date"],
                    y=df_yearly["out_min"],
                    mode="lines",
                    name="最低気温(外)",
                    line=dict(color="#448aff", width=2),
                )
            )

        if "in_max" in df_yearly.columns:
            fig.add_trace(
                go.Scatter(
                    x=df_yearly["date"],
                    y=df_yearly["in_max"],
                    mode="lines",
                    name="最高室温(内)",
                    line=dict(color="#ff9800", width=2, dash="dot"),
                )
            )

        if "in_min" in df_yearly.columns:
            fig.add_trace(
                go.Scatter(
                    x=df_yearly["date"],
                    y=df_yearly["in_min"],
                    mode="lines",
                    name="最低室温(内)",
                    line=dict(color="#00bcd4", width=2, dash="dot"),
                )
            )

        fig.update_layout(
            xaxis_title="日付",
            yaxis_title="温度(℃)",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            hovermode="x unified",
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("年間データがまだありません。")


def render_health_tab(
    df_child: pd.DataFrame, df_poop: pd.DataFrame, df_food: pd.DataFrame
):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 🏥 子供")
        if not df_child.empty:
            st.dataframe(
                df_child[["timestamp", "child_name", "condition"]],
                width="stretch",
            )
    with c2:
        st.markdown("##### 💩 排便")
        if not df_poop.empty:
            st.dataframe(
                df_poop[["timestamp", "user_name", "condition"]],
                width="stretch",
            )
    st.markdown("##### 🍽️ 食事")
    if not df_food.empty:
        st.dataframe(
            df_food[["timestamp", "menu_category"]], width="stretch"
        )


def render_takasago_tab(df_sensor: pd.DataFrame):
    """高砂詳細タブ"""
    if not df_sensor.empty:
        st.subheader("👵 実家ログ")
        st.dataframe(
            df_sensor[df_sensor["location"] == "高砂"][
                ["timestamp", "friendly_name", "contact_state"]
            ].head(50),
            width="stretch",
        )


def render_logs_tab(df_sensor: pd.DataFrame):
    """全ログタブ"""
    if not df_sensor.empty:
        locs = df_sensor["location"].unique()
        sel = st.multiselect("場所", locs, default=locs)
        st.dataframe(
            df_sensor[df_sensor["location"].isin(sel)][
                ["timestamp", "friendly_name", "location", "contact_state", "power_watts"]
            ].head(200),
            width="stretch",
        )


def render_trends_tab():
    """最近の流行タブ"""
    st.title("🌟 最近の流行・トレンド推移")
    st.caption("Google Playストアのランキング（最新3回分）を表示します")

    dates = load_ranking_dates(limit=3)
    if not dates:
        st.info("データがありません。ランキング取得スクリプトを実行してください。")
        return

    def render_history_section(title, ranking_type):
        st.subheader(title)
        cols = st.columns(len(dates))
        for i, date_str in enumerate(dates):
            with cols[i]:
                label = "今週" if i == 0 else ("先週" if i == 1 else "先々週")
                st.markdown(f"**{label} ({date_str[5:]})**")
                df = load_ranking_data(date_str, ranking_type)
                if df.empty:
                    st.write("- データなし -")
                    continue
                for _, row in df.iterrows():
                    url = f"https://play.google.com/store/apps/details?id={row['app_id']}"
                    st.markdown(f"{row['rank']}. [{row['title']}]({url})")

    render_history_section("🆓 無料トップ (流行)", "free")
    st.markdown("---")
    render_history_section("💰 売上トップ (人気)", "grossing")


def render_bicycle_tab(df_bicycle: pd.DataFrame):
    """駐輪場タブの描画"""
    st.title("🚲 駐輪場待機数推移")

    if df_bicycle.empty:
        st.info("駐輪場データがまだありません。スクリプトが実行されているか確認してください。")
        return

    target_areas = [
        "JR伊丹駅前(第1)自転車駐車場 (A)",
        "JR伊丹駅前(第3)自転車駐車場 (A)",
        "JR伊丹駅前(第3)自転車駐車場 (E)",
    ]

    df_target = df_bicycle[df_bicycle["area_name"].isin(target_areas)].copy()

    if df_target.empty:
        st.warning("指定されたエリアのデータが見つかりません。")
        with st.expander("現在取得できているエリア一覧"):
            st.write(df_bicycle["area_name"].unique())
        return

    fig = px.line(
        df_target,
        x="timestamp",
        y="waiting_count",
        color="area_name",
        title="待機人数の変化",
        markers=True,
        symbol="area_name",
    )
    fig.update_layout(
        xaxis_title="日時",
        yaxis_title="待機数 (人/台)",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
        ),
    )
    st.plotly_chart(fig, width="stretch")

    st.subheader("📊 最新の状況")
    latest_df = df_target.sort_values(
        "timestamp", ascending=False
    ).drop_duplicates("area_name")
    st.dataframe(
        latest_df[
            ["timestamp", "area_name", "waiting_count", "status_text"]
        ].sort_values("area_name"),
        width="stretch",
    )


def render_quest_tab():
    """Family Questの状況を表示するタブ"""
    st.title("⚔️ Family Quest 現在の状況")
    
    try:
        with common.get_db_cursor() as cur:
            cur.execute("SELECT name, exp, gold, job_class FROM quest_users ORDER BY exp DESC")
            rows = cur.fetchall()
            
            cur.execute("""
                SELECT u.name, h.quest_title, h.completed_at 
                FROM quest_history h
                JOIN quest_users u ON h.user_id = u.user_id
                ORDER BY h.completed_at DESC 
                LIMIT 5
            """)
            history = cur.fetchall()

        if not rows:
            st.info("データがありません。 seed_quest_data.py を実行するか、アプリでユーザー登録を行ってください。")
            return

        cols = st.columns(len(rows))
        for i, (name, exp, gold, job_class) in enumerate(rows):
            with cols[i]:
                rank_icon = "👑" if i == 0 else "🛡️"
                st.metric(
                    label=f"{rank_icon} {name} ({job_class})",
                    value=f"{exp} EXP",
                    delta=f"{gold} G"
                )

        st.divider()

        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.subheader("📊 経験値ランキング")
            df_quest = pd.DataFrame(rows, columns=["名前", "経験値", "ゴールド", "職業"])
            fig = px.bar(
                df_quest, 
                x="名前", 
                y="経験値", 
                color="職業", 
                text="経験値",
                title="現在のレベル状況"
            )
            fig.update_traces(textposition='outside')
            st.plotly_chart(fig, width="stretch")

        with col2:
            st.subheader("📜 最近の達成履歴")
            if history:
                for name, title, completed_at in history:
                    try:
                        t_str = completed_at.split('.')[0].replace('T', ' ')
                        dt = datetime.strptime(t_str, '%Y-%m-%d %H:%M:%S')
                        time_display = dt.strftime('%m/%d %H:%M')
                    except:
                        time_display = completed_at

                    st.markdown(f"**{name}** が **『{title}』** を達成！  \n<span style='color:grey; font-size:0.8em'>({time_display})</span>", unsafe_allow_html=True)
                    st.write("---")
            else:
                st.write("まだ冒険の記録がありません")

    except Exception as e:
        st.error(f"クエスト情報の読み込みに失敗しました: {e}")
        logger.error(f"Quest Tab Error: {e}")


def render_system_tab():
    """システム管理タブの描画"""
    st.title("🔧 システム管理コックピット")

    st.subheader("🌐 外部接続 (ngrok)")
    urls = get_ngrok_url()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**📱 LINE Bot / Server (Port 8000)**")
        if urls.get("server"):
            st.success(f"接続OK: {urls['server']}")
            st.caption("※LINE Botの設定URLはこれになります")
        else:
            st.error("取得失敗 (ngrokを確認してください)")

    with c2:
        st.markdown("**📊 Dashboard (Port 8501)**")
        if urls.get("dashboard"):
            st.success(f"接続OK: {urls['dashboard']}")
            st.link_button("ダッシュボードを開く", urls['dashboard'])
        else:
            st.warning("取得失敗 (固定ドメイン設定を確認)")

    st.markdown("---")

    st.subheader("💻 リソース状況")

    disk = get_disk_usage()
    if disk:
        st.write(
            f"**💾 ディスク使用率: {disk['percent']:.1f}%** (使用 {disk['used_gb']} GB / 全体 {disk['total_gb']} GB)"
        )
        st.progress(int(disk["percent"]))

    st.write("")

    mem = get_memory_usage()
    if mem:
        st.write(
            f"**🧠 メモリ使用率: {mem['percent']:.1f}%** (使用 {mem['used_mb']} MB / 全体 {mem['total_mb']} MB)"
        )
        st.caption(f"実質空き容量 (Available): {mem['available_mb']} MB")
        st.progress(int(mem["percent"]))
    else:
        st.warning("メモリ情報の取得に失敗しました")

    st.markdown("---")

    st.subheader("🗄️ NAS 状態 (BUFFALO LS720D)")
    nas_data = load_nas_status()

    if nas_data is not None:
        c_nas1, c_nas2, c_nas3 = st.columns(3)
        with c_nas1:
            ping_icon = "✅" if nas_data["status_ping"] == "OK" else "❌"
            st.metric("Ping疎通", f"{ping_icon} {nas_data['status_ping']}")
        with c_nas2:
            mount_icon = "✅" if nas_data["status_mount"] == "OK" else "❌"
            st.metric("マウント", f"{mount_icon} {nas_data['status_mount']}")
        with c_nas3:
            ts = nas_data["timestamp"]
            if isinstance(ts, str):
                if "T" in ts:
                    ts = pd.to_datetime(ts).tz_localize("UTC").tz_convert("Asia/Tokyo")
                else:
                    ts = pd.to_datetime(ts)
            last_upd = ts.strftime("%m/%d %H:%M")
            st.metric("最終確認", last_upd)

        if nas_data["total_gb"] > 0:
            usage_rate = nas_data["percent"]
            st.write(
                f"**💾 NASディスク使用率: {usage_rate:.1f}%** (使用 {nas_data['used_gb']} GB / 全体 {nas_data['total_gb']} GB)"
            )
            if usage_rate > 90:
                st.warning("⚠️ 容量が残り少なくなっています！")
            st.progress(int(usage_rate))
        else:
            st.warning("容量データが取得できていません")
    else:
        st.info("NASの監視データがまだありません")

    st.markdown("---")

    st.subheader("📜 サーバーログ (Journalctl)")

    search_mode = st.radio("検索モード", ["直近のログを表示", "日付を指定して検索"], horizontal=True)
    col_opt1, col_opt2, _ = st.columns([1, 1, 2])
    target_date = None
    lines_val = 50

    with col_opt1:
        if search_mode == "日付を指定して検索":
            target_date = st.date_input("対象日", date.today())
        else:
            lines_val = st.selectbox("表示行数", [50, 100, 200, 500], index=0)

    with col_opt2:
        level_options = {
            "全て (Info以上)": None,
            "警告 (Warning以上)": "warning",
            "エラー (Errorのみ)": "err",
        }
        selected_label = st.selectbox("ログレベル", list(level_options.keys()))
        selected_priority = level_options[selected_label]

    if st.button("🔄 ログを更新"):
        st.rerun()

    logs = get_system_logs(
        lines=lines_val, priority=selected_priority, target_date=target_date
    )

    if not logs:
        st.info("該当するログはありません")
    else:
        st.code(logs, language="text")

    st.markdown("---")
    st.subheader("⚠️ サーバー操作")

    col_reboot, _ = st.columns([1, 2])
    with col_reboot:
        if st.button("🔄 システム再起動 (Restart Service)"):
            try:
                st.info("再起動コマンドを送信しました。しばらくお待ちください...")
                subprocess.run(
                    ["sudo", "systemctl", "restart", "home_system"], check=True
                )
                st.success("再起動を受け付けました。10秒後にページをリロードしてください。")
            except subprocess.CalledProcessError as e:
                st.error(f"再起動に失敗しました: {e}")
            except Exception as e:
                st.error(f"エラー: {e}")

    st.markdown("---")
    st.subheader("📦 データバックアップ")

    import glob

    backup_dir = os.path.join(config.BASE_DIR, "..", "backups")
    if os.path.exists(backup_dir):
        files = sorted(glob.glob(os.path.join(backup_dir, "*.zip")), reverse=True)
        if files:
            latest_file = files[0]
            f_name = os.path.basename(latest_file)
            f_size = os.path.getsize(latest_file) / (1024 * 1024)
            f_time = datetime.fromtimestamp(os.path.getmtime(latest_file)).strftime(
                "%Y/%m/%d %H:%M"
            )

            c_bk1, c_bk2 = st.columns([2, 1])
            with c_bk1:
                st.success(f"✅ 最新バックアップ: {f_time}")
                st.caption(f"ファイル名: {f_name} | サイズ: {f_size:.2f} MB")
            with c_bk2:
                with open(latest_file, "rb") as f:
                    st.download_button(
                        "⬇️ ダウンロード", f, file_name=f_name, mime="application/zip"
                    )

            with st.expander("🗂️ バックアップ履歴 (最新5件)"):
                for bf in files[:5]:
                    bs = os.path.getsize(bf) / (1024 * 1024)
                    bt = datetime.fromtimestamp(os.path.getmtime(bf)).strftime(
                        "%m/%d %H:%M"
                    )
                    st.text(f"・{bt} : {os.path.basename(bf)} ({bs:.2f}MB)")
        else:
            st.warning("バックアップファイルがまだありません")
            if st.button("今すぐ手動バックアップを実行"):
                import MY_HOME_SYSTEM.backup_service as backup_service

                success, res, size = backup_service.perform_backup()
                if success:
                    st.success(f"完了しました！ ({size:.1f}MB)")
                    st.rerun()
                else:
                    st.error(f"失敗: {res}")
    else:
        st.info("バックアップディレクトリが作成されていません（次回実行時に作成されます）")


# === メイン処理 ===


def main():
    with st.sidebar:
        st.header("設定")
        if st.button("🔄 データを更新"):
            st.cache_data.clear()
            st.rerun()
        st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        logger.info(f"Dashboard Rendering... ({now.strftime('%H:%M:%S')})")

    try:
        st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
        now = datetime.now(pytz.timezone("Asia/Tokyo"))

        # データ読み込み
        df_sensor = load_sensor_data(limit=10000)
        df_weather = load_weather_history(days=40, location="伊丹")
        df_poop = load_generic_data(config.SQLITE_TABLE_DEFECATION)
        df_child = load_generic_data(config.SQLITE_TABLE_CHILD)
        df_food = load_generic_data(config.SQLITE_TABLE_FOOD)
        df_car = load_generic_data(config.SQLITE_TABLE_CAR)
        df_security_log = load_generic_data("security_logs", limit=100)
        df_bicycle = load_bicycle_data(limit=3000)
        nas_data = load_nas_status()

        # AIレポート表示
        report = load_ai_report()
        if report is not None:
            report_time = pd.to_datetime(report["timestamp"]).tz_convert("Asia/Tokyo")
            time_str = report_time.strftime("%H:%M")
            hour = report_time.hour
            icon = "☀️" if 5 <= hour < 11 else ("🕛" if 11 <= hour < 17 else "🌙")
            with st.expander(
                f"{icon} セバスチャンからの報告 ({time_str}) - タップして読む", expanded=False
            ):
                st.markdown(report["message"].replace("\n", "  \n"))

        # メトリクス（ステータスカード）表示
        render_dashboard_summary(now, df_sensor, df_car, df_bicycle, nas_data)

        # タブ切り替え
        tabs = st.tabs(
            [
                "⚔️ クエスト",  # <--- 追加
                "🚃 電車遅延",
                "📸 防犯カメラ",
                "💡 電力・環境",
                "🌡️ 気温詳細",
                "🏥 健康管理",
                "👵 高砂実家",
                "📝 ログ分析",
                "📊 トレンド",
                "🔧 システム管理",
                "🚲 駐輪場",
            ]
        )

        (
            tab_quest,      # <--- 追加
            tab_train,
            tab_photo,
            tab_elec,
            tab_temp,
            tab_health,
            tab_taka,
            tab_log,
            tab_trends,
            tab_sys,
            tab_bicycle,
        ) = tabs

        with tab_quest:
            render_quest_tab()
        with tab_train:
            render_traffic_tab()
        with tab_photo:
            render_photos_tab(df_security_log)
        with tab_elec:
            render_electricity_tab(df_sensor, now)
        with tab_temp:
            render_temperature_tab(df_sensor, now)
        with tab_health:
            render_health_tab(df_child, df_poop, df_food)
        with tab_taka:
            render_takasago_tab(df_sensor)
        with tab_log:
            render_logs_tab(df_sensor)
        with tab_trends:
            render_trends_tab()
        with tab_sys:
            render_system_tab()
        with tab_bicycle:
            render_bicycle_tab(df_bicycle)

    except Exception as e:
        err_msg = f"📉 Dashboard Error: {e}"
        logger.error(err_msg)
        try:
            common.send_push(
                config.LINE_USER_ID,
                [{"type": "text", "text": err_msg}],
                target="discord",
                channel="error",
            )
        except Exception:
            pass  # エラー送信自体のエラーは無視
        st.error("システムエラーが発生しました。ログを確認してください。")
        st.code(traceback.format_exc())


if __name__ == "__main__":
    main()