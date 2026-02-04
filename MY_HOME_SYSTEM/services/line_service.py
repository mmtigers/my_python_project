# MY_HOME_SYSTEM/services/line_service.py
import sqlite3
import datetime
from typing import List, Tuple, Optional

import config
from core.logger import setup_logging
from core.utils import get_now_iso, get_today_date_str
from core.database import save_log_async

# ロガー設定
logger = setup_logging("line_service")

TARGET_MEMBERS = config.FAMILY_SETTINGS["members"]

# === DB Operations (同期/非同期ラッパー) ===

async def log_child_health(user_id: str, user_name: str, child_name: str, condition: str) -> None:
    """子供の体調を記録"""
    await save_log_async(
        config.SQLITE_TABLE_CHILD,
        ["user_id", "user_name", "child_name", "condition", "timestamp"],
        (user_id, user_name, child_name, condition, get_now_iso())
    )

async def log_food_record(user_id: str, user_name: str, category: str, item: str, is_manual: bool = False) -> None:
    """食事を記録"""
    final_rec = f"{category}: {item}" + (" (手入力)" if is_manual else "")
    await save_log_async(
        config.SQLITE_TABLE_FOOD,
        ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
        (user_id, user_name, get_today_date_str(), "Dinner", final_rec, get_now_iso())
    )

async def log_daily_action(user_id: str, user_name: str, category: str, value: str) -> None:
    """日常動作（外出・面会など）を記録"""
    await save_log_async(
        config.SQLITE_TABLE_DAILY, 
        ["user_id", "user_name", "date", "category", "value", "timestamp"],
        (user_id, user_name, get_today_date_str(), category, value, get_now_iso())
    )

async def log_defecation(user_id: str, user_name: str, record_type: str, condition: str) -> None:
    """排便記録"""
    await save_log_async(
        config.SQLITE_TABLE_DEFECATION, 
        ["user_id", "user_name", "record_type", "condition", "timestamp"], 
        (user_id, user_name, record_type, condition, get_now_iso())
    )

async def log_ohayo(user_id: str, user_name: str, message: str, keyword: str) -> None:
    """おはようメッセージ記録"""
    await save_log_async(
        config.SQLITE_TABLE_OHAYO, 
        ["user_id", "user_name", "message", "timestamp", "recognized_keyword"], 
        (user_id, user_name, message, get_now_iso(), keyword)
    )

def get_daily_health_summary_text() -> str:
    """今日の体調記録サマリを取得してテキストで返す"""
    today_str = get_today_date_str()
    summary_lines = []
    
    try:
        # 読み取り専用で接続
        with sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            for name in TARGET_MEMBERS:
                cur.execute(f"""
                    SELECT condition, timestamp FROM {config.SQLITE_TABLE_CHILD}
                    WHERE child_name = ? AND timestamp LIKE ?
                    ORDER BY id DESC LIMIT 1
                """, (name, f"{today_str}%"))
                row = cur.fetchone()
                
                if row:
                    try:
                        dt = datetime.datetime.fromisoformat(row["timestamp"])
                        time_str = dt.strftime("%H:%M")
                    except:
                        time_str = "??:??"
                    status = row["condition"]
                    icon = "✅" if "元気" in status else "⚠️"
                    summary_lines.append(f"{icon} {name}: {status} ({time_str})")
                else:
                    summary_lines.append(f"❓ {name}: (未記録)")
    except Exception as e:
        logger.error(f"DB Read Error: {e}")
        return "（データ取得エラー）"
    
    return "\n".join(summary_lines)