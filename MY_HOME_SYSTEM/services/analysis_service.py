# MY_HOME_SYSTEM/services/analysis_service.py
import sqlite3
import shutil
import subprocess
import requests
import os
from datetime import datetime, timedelta, date
import pytz
from typing import Dict, List, Optional, Any

import pandas as pd

import config
from core.logger import setup_logging

# ロガー設定
logger = setup_logging("analysis_service")

# 表示名の揺らぎ吸収用マッピング
FRIENDLY_NAME_FIXES: Dict[str, str] = {
    "リビング": "高砂のリビング",
    "１Fの洗面所": "高砂の洗面所",
    "居間": "伊丹のリビング",
    "仕事部屋": "伊丹の書斎",
    "人感センサー": "高砂のトイレ(人感)",
}

# ==========================================
# Database Helpers
# ==========================================

def get_ro_db_connection() -> sqlite3.Connection:
    """
    読み取り専用でデータベース接続を取得します。
    Service層内部またはView層でキャッシュする際に使用します。
    """
    return sqlite3.connect(
        f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True, timeout=10.0
    )

def process_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """DataFrameのタイムスタンプを日本時間に変換し、表示名を適用する共通処理"""
    if df.empty or "timestamp" not in df.columns:
        return df

    df = df.copy()
    
    # 【最終修正】format="mixed" を確実に追加し、以降の冗長なapply処理を削除
    df["timestamp"] = pd.to_datetime(
        df["timestamp"], 
        format="mixed", 
        utc=True
    ).dt.tz_convert("Asia/Tokyo")

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
        df["friendly_name"] = "Unknown"
        df["location"] = "その他"
        return df

    # 2. Configからデフォルトのマッピングを作成
    id_map = {d["id"]: d.get("name", d["id"]) for d in config.MONITOR_DEVICES}
    loc_map = {d["id"]: d.get("location", "その他") for d in config.MONITOR_DEVICES}

    # 3. DB内の「最新のデバイス名」を取得してマッピングを上書き
    if "device_name" in df.columns and "timestamp" in df.columns:
        try:
            latest_df = df.sort_values("timestamp", ascending=False)
            latest_df = latest_df.drop_duplicates(subset="device_id", keep="first")
            valid_latest = latest_df[latest_df["device_name"].notna() & (latest_df["device_name"] != "")]
            db_latest_map = valid_latest.set_index("device_id")["device_name"].to_dict()
            id_map.update(db_latest_map)
        except Exception as e:
            logger.warning(f"Friendly name mapping update failed: {e}")

    # 4. マッピングの適用
    df["friendly_name"] = df["device_id"].map(id_map)
    
    # マッピングで見つからなかった場合は device_name -> device_id の順でフォールバック
    if "device_name" in df.columns:
        df["friendly_name"] = df["friendly_name"].fillna(df["device_name"])
    df["friendly_name"] = df["friendly_name"].fillna(df["device_id"])

    # 5. ロケーションの適用
    df["location"] = df["device_id"].map(loc_map).fillna("その他")

    # 6. 名称の微調整
    df["friendly_name"] = df["friendly_name"].replace(FRIENDLY_NAME_FIXES)

    return df

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

# ==========================================
# Data Fetching Methods (Core Logic)
# ==========================================

def load_nas_status() -> Optional[pd.Series]:
    """NASの最新状態を取得"""
    table_name = getattr(config, "SQLITE_TABLE_NAS", "nas_records")
    try:
        with get_ro_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
            if not cur.fetchone():
                return None
            
        query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT 1"
        df = load_data_from_db(query)
        return df.iloc[0] if not df.empty else None
    except Exception as e:
        logger.error(f"NAS Data Load Error: {e}")
        return None

def load_generic_data(table_name: str, limit: int = 500) -> pd.DataFrame:
    """指定テーブルからデータを取得（汎用）"""
    query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
    return load_data_from_db(query)

def load_sensor_data(limit: int = 5000) -> pd.DataFrame:
    """
    新旧テーブルからセンサーデータを統合して取得する
    Target Tables: device_records, switchbot_meter_logs, power_usage
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
    query_power = f"""
        SELECT timestamp, device_id, device_name, 
               wattage as power_watts
        FROM {config.SQLITE_TABLE_POWER_USAGE}
        ORDER BY timestamp DESC LIMIT {limit}
    """
    df_power = load_data_from_db(query_power)
    if not df_power.empty:
        df_power["device_type"] = df_power["device_name"].apply(
            lambda x: "Nature Remo E Lite" if x and "Remo" in str(x) else "Plug"
        )
        df_power["device_type"] = df_power["device_type"].replace("Plug", "Nature Remo E Lite") 

    # --- 統合 ---
    df_list = []
    if not df_legacy.empty: df_list.append(df_legacy)
    if not df_meter.empty: df_list.append(df_meter)
    if not df_power.empty: df_list.append(df_power)

    if not df_list:
        return pd.DataFrame()

    df_merged = pd.concat(df_list, ignore_index=True)
    
    if "timestamp" in df_merged.columns:
        df_merged["timestamp"] = pd.to_datetime(df_merged["timestamp"])
        df_merged = df_merged.sort_values("timestamp", ascending=False).reset_index(drop=True)

    return apply_friendly_names(df_merged).head(limit)

def calculate_monthly_cost_cumulative() -> int:
    """今月の電気代概算"""
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
        df = df[df["time_diff"] <= 1.0]

        df["kwh"] = (df["power_watts"] / 1000) * df["time_diff"]
        return int(df["kwh"].sum() * 31)
    except Exception as e:
        logger.error(f"Cost Calc Error: {e}")
        return 0

def load_weather_history(days: int = 40, location: str = "伊丹") -> pd.DataFrame:
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    query = f"""
        SELECT date, min_temp, max_temp, weather_desc, umbrella_level 
        FROM weather_history 
        WHERE location = '{location}' AND date >= '{start_date}'
    """
    conn = get_ro_db_connection()
    try:
        df = pd.read_sql_query(query, conn)
        return df
    except Exception as e:
        logger.error(f"Weather Load Error: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

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
        
        itami_ids = [d["id"] for d in config.MONITOR_DEVICES if d.get("location") == location]
        if not itami_ids:
            return df_weather
            
        ids_str = "'" + "','".join(itami_ids) + "'"

        q_new = f"""
            SELECT substr(timestamp, 1, 10) as date, MAX(temperature) as in_max, MIN(temperature) as in_min
            FROM {config.SQLITE_TABLE_SWITCHBOT_LOGS}
            WHERE timestamp >= '{start_date}' AND timestamp <= '{end_date}T23:59:59'
            AND device_id IN ({ids_str}) AND temperature IS NOT NULL GROUP BY date
        """
        q_old = f"""
            SELECT substr(timestamp, 1, 10) as date, MAX(temperature_celsius) as in_max, MIN(temperature_celsius) as in_min
            FROM device_records
            WHERE timestamp >= '{start_date}' AND timestamp <= '{end_date}T23:59:59'
            AND device_id IN ({ids_str}) AND temperature_celsius IS NOT NULL GROUP BY date
        """
        
        df_new = pd.DataFrame()
        try: df_new = pd.read_sql_query(q_new, conn)
        except: pass
            
        df_old = pd.DataFrame()
        try: df_old = pd.read_sql_query(q_old, conn)
        except: pass

        if not df_new.empty and not df_old.empty:
            df_sensor = pd.concat([df_new, df_old]).groupby("date").agg({"in_max": "max", "in_min": "min"}).reset_index()
        elif not df_new.empty:
            df_sensor = df_new
        else:
            df_sensor = df_old

        if df_weather.empty and df_sensor.empty:
            return pd.DataFrame()
        if df_weather.empty: df_merged = df_sensor
        elif df_sensor.empty: df_merged = df_weather
        else: df_merged = pd.merge(df_weather, df_sensor, on="date", how="outer")

        return df_merged.sort_values("date")
    except Exception as e:
        logger.error(f"Yearly Temp Load Error: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def load_bicycle_data(limit: int = 2000) -> pd.DataFrame:
    """駐輪場データを取得"""
    table_name = getattr(config, "SQLITE_TABLE_BICYCLE", "bicycle_parking_records")
    try:
        with get_ro_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
            if not cur.fetchone():
                return pd.DataFrame()
        query = f"SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}"
        return load_data_from_db(query)
    except Exception as e:
        logger.error(f"Bicycle Data Load Error: {e}")
        return pd.DataFrame()

def load_ai_report() -> Optional[pd.Series]:
    """最新のAIレポートを取得"""
    query = f"SELECT * FROM {config.SQLITE_TABLE_AI_REPORT} ORDER BY id DESC LIMIT 1"
    df = load_data_from_db(query)
    return df.iloc[0] if not df.empty else None

def load_ranking_dates(limit: int = 3) -> List[str]:
    """ランキングの日付リストを取得"""
    try:
        with get_ro_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_rankings'")
            if not cur.fetchone(): return []
            query = f"SELECT DISTINCT date FROM app_rankings ORDER BY date DESC LIMIT {limit}"
            df = pd.read_sql_query(query, conn)
            return df["date"].tolist()
    except Exception as e:
        logger.error(f"Ranking Dates Load Error: {e}")
        return []

def load_ranking_data(date_str: str, ranking_type: str) -> pd.DataFrame:
    """特定日付・タイプのランキングを取得"""
    query = f"""
        SELECT rank, title, app_id FROM app_rankings 
        WHERE date = '{date_str}' AND ranking_type = '{ranking_type}'
        ORDER BY rank ASC
    """
    conn = get_ro_db_connection()
    try:
        return pd.read_sql_query(query, conn)
    except Exception as e:
        logger.error(f"Ranking Data Load Error: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

# ==========================================
# System Stats & Utils
# ==========================================

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