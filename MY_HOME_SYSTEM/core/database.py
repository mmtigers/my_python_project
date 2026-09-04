import re
import sqlite3
import time
import json
import logging
import asyncio
from typing import List
from contextlib import contextmanager
import config

logger = logging.getLogger("core.database")

# save_log_generic の table/カラム名はプレースホルダ化できず、SQL文字列へ直接
# 展開せざるを得ない。現状の呼び出し元は全てリテラル固定値かconfig定数のみだが、
# 将来ユーザー入力等の動的な値が渡された場合に備え、SQLite識別子として妥当な
# 文字種(英数字・アンダースコアのみ、数字始まり不可)のみを許可するホワイトリスト
# 検証を構造的な防御として設ける(B3)。
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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
    if not _SQL_IDENTIFIER_RE.match(table) or not all(_SQL_IDENTIFIER_RE.match(c) for c in columns_list):
        logger.error(f"データ保存失敗: 不正なtable/カラム名 (table={table!r}, columns={columns_list!r})")
        return False
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

def save_logs_batch_generic(table: str, columns_list: List[str], values_list: List[tuple]) -> bool:
    """複数行をまとめて単一トランザクションで保存する汎用関数。

    #231: save_log_generic を複数回呼び出す実装(handlers/line_logic.py の
    all_genki等)では、各呼び出しがそれぞれ独立にcommitされるため、途中の1件が
    失敗しても、既に成功した分はコミット済みのまま残ってしまう。呼び出し元は
    「1件でも失敗すれば全体を失敗扱いとする」と案内しユーザーに再試行を促すが、
    再試行すると既に成功していた分まで重複して保存されていた。単一の
    get_db_cursor(commit=True)ブロック内で全件INSERTすることで、1件でも
    失敗すれば例外がget_db_cursor側のrollbackへ伝播し、全件ロールバックされる
    (真のall-or-nothing)。
    """
    # Q-L9(#409): save_log_generic と同じ識別子ホワイトリストを適用する(以前は片方だけだった)
    if not _SQL_IDENTIFIER_RE.match(table) or not all(_SQL_IDENTIFIER_RE.match(c) for c in columns_list):
        logger.error(f"バッチデータ保存失敗: 不正なtable/カラム名 (table={table!r}, columns={columns_list!r})")
        return False
    try:
        with get_db_cursor(commit=True) as cur:
            placeholders = ", ".join(["?"] * len(columns_list))
            columns = ", ".join(columns_list)
            sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
            for values in values_list:
                cur.execute(sql, values)
        return True
    except Exception as e:
        logger.error(f"バッチデータ保存失敗 ({table}): {e}")
        return False

async def save_logs_batch_async(table: str, columns_list: List[str], values_list: List[tuple]) -> bool:
    """save_logs_batch_generic の非同期ラッパー"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, save_logs_batch_generic, table, columns_list, values_list)