import sqlite3
import time
import json
import logging
import asyncio
from typing import List
from contextlib import contextmanager
import config

logger = logging.getLogger("core.database")

@contextmanager
def get_db_cursor(commit: bool = False):
    """DB接続コンテキストマネージャ (リトライ機能付き)"""
    conn = None
    max_retries = 5
    retry_delay = 1.0

    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            
            yield conn.cursor()
            
            if commit:
                conn.commit()
            break 
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                logger.warning(f"⚠️ DB is locked. Retrying... ({attempt+1}/{max_retries})")
                if conn: conn.close()
                time.sleep(retry_delay)
            else:
                logger.error(f"データベース操作エラー: {e}")
                if conn: conn.rollback()
                raise e
        except Exception as e:
            logger.error(f"予期せぬDBエラー: {e}")
            if conn: conn.rollback()
            raise e
    else:
        logger.error("❌ DB Retry limit reached.")
        if conn: conn.close()
    
    if conn:
        try: conn.close()
        except: pass

def execute_read_query(query: str, params: tuple = ()) -> str:
    """読み取り専用モードで安全にSELECTを実行する"""
    try:
        conn = sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        if not rows: return "該当するデータはありませんでした。"
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
    except Exception as e:
        return f"検索エラー: {str(e)}"

def save_log_generic(table: str, columns_list: List[str], values_list: tuple) -> bool:
    """汎用データ保存関数"""
    with get_db_cursor(commit=True) as cur:
        if cur:
            try:
                placeholders = ", ".join(["?"] * len(values_list))
                columns = ", ".join(columns_list)
                sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
                cur.execute(sql, values_list)
                return True
            except Exception as e:
                logger.error(f"データ保存失敗 ({table}): {e}")
    return False

async def save_log_async(table: str, columns_list: List[str], values_list: tuple) -> bool:
    """save_log_generic の非同期ラッパー"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, save_log_generic, table, columns_list, values_list)