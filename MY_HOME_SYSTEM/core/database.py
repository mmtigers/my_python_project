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
    """DB接続コンテキストマネージャ (接続確立のみリトライ。yieldは必ず1回だけ行う)"""
    conn = None
    max_retries = 5
    retry_delay = 1.0

    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(config.SQLITE_DB_PATH, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            break
        except sqlite3.OperationalError as e:
            if conn:
                conn.close()
                conn = None
            if "locked" in str(e) and attempt < max_retries - 1:
                logger.warning(f"⚠️ DB is locked. Retrying connection... ({attempt+1}/{max_retries})")
                time.sleep(retry_delay)
                continue
            logger.error(f"❌ DB接続エラー: {e}")
            raise

    try:
        yield conn.cursor()
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def execute_read_query(query: str, params: tuple = ()) -> str:
    """読み取り専用モードで安全にSELECTを実行する"""
    # #178: conn.close()が正常経路にしかなくtry/finallyが無かったため、
    # cursor.execute()が例外を送出する(不正なSQL等)たびに接続がGC任せで
    # 残りリークしていた。connをtry節の前で初期化し、finallyで確実に
    # closeする。
    conn = None
    try:
        conn = sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

        if not rows: return "該当するデータはありませんでした。"
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
    except Exception as e:
        return f"検索エラー: {str(e)}"
    finally:
        if conn:
            conn.close()

def save_log_generic(table: str, columns_list: List[str], values_list: tuple) -> bool:
    """汎用データ保存関数"""
    try:
        with get_db_cursor(commit=True) as cur:
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