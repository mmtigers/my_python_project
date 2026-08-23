# MY_HOME_SYSTEM/core/migrations.py
"""
バージョン管理されたスキーママイグレーションの適用。

これまでスキーマ変更は services/quest_service.py の sync_master_data() 内で
「SELECTを試して失敗したらALTER TABLE」という実行時チェックとして場当たり的に
追加されてきた。この方式は「いつ・なぜ追加されたカラムか」を追跡できず、
複数プロセスからの同時実行時にレースの懸念もある。

本モジュールは migrations/ 配下の *.sql ファイルをファイル名の昇順で適用し、
適用済みバージョンを schema_migrations テーブルで管理する軽量なランナー。
既存の quest_service.py 側の実行時チェックは、init_db() を経由しない
既存の本番運用パス（sync_master_data の初回呼び出し時にのみ列が追加される
運用）との後方互換のため、あえて残している。今後のスキーマ変更は
本モジュール経由（migrations/ 配下への追加）で行うことを推奨する。
"""
import os
import sqlite3
from typing import List, Set

from core.logger import setup_logging

logger = setup_logging("core.migrations")

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")

# 「既に別経路(旧来の実行時ALTER等)で適用済み」と断定できる、既知のSQLiteエラー文言のみ。
# それ以外のOperationalError(DBロック・ディスクフル・SQL誤り等)は失敗として扱う。
_ALREADY_APPLIED_ERROR_PATTERNS = ("duplicate column", "already exists")


def _ensure_tracking_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def _applied_versions(conn: sqlite3.Connection) -> Set[str]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def _discover_migration_files() -> List[str]:
    if not os.path.isdir(MIGRATIONS_DIR):
        return []
    return sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))


def apply_pending_migrations(conn: sqlite3.Connection) -> None:
    """
    migrations/ 配下の未適用 *.sql をファイル名昇順で適用する。

    各マイグレーションファイルは、既に適用済みの環境（列が既に存在する等）に対して
    再実行されても致命的にならないよう ALTER TABLE を先頭に書くことを想定している。
    「duplicate column」「already exists」のように「既に別経路（旧来の実行時チェック等）で
    適用済み」と断定できる既知のエラー文言のみ警告ログを出して適用済み扱いにする。
    それ以外の OperationalError（DBロック・ディスクフル・SQL誤り等）は、原因不明なまま
    「適用済み」と記録してしまうとスキーマドリフトを見逃すため、起動失敗として
    バージョンを記録せず再送出する。
    """
    _ensure_tracking_table(conn)
    applied = _applied_versions(conn)

    for filename in _discover_migration_files():
        if filename in applied:
            continue

        path = os.path.join(MIGRATIONS_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            sql = f.read()

        logger.info(f"🔧 Applying migration: {filename}")
        try:
            conn.executescript(sql)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (filename,))
            conn.commit()
            logger.info(f"✅ Migration applied: {filename}")
        except sqlite3.OperationalError as e:
            message = str(e).lower()
            if any(pattern in message for pattern in _ALREADY_APPLIED_ERROR_PATTERNS):
                logger.warning(
                    f"⚠️ Migration '{filename}' could not be fully applied "
                    f"(likely already applied via a different path): {e}"
                )
                conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (filename,))
                conn.commit()
                continue

            conn.rollback()
            logger.error(f"❌ Migration '{filename}' failed and was not recorded as applied: {e}")
            raise
