# MY_HOME_SYSTEM/core/migrations.py
"""
バージョン管理されたスキーママイグレーションの適用。

これまでスキーマ変更は services/quest_service.py の sync_master_data() 内で
「SELECTを試して失敗したらALTER TABLE」という実行時チェックとして場当たり的に
追加されてきた。この方式は「いつ・なぜ追加されたカラムか」を追跡できず、
複数プロセスからの同時実行時にレースの懸念もある。

本モジュールは migrations/ 配下の *.sql ファイルをファイル名の昇順で適用し、
適用済みバージョンを schema_migrations テーブルで管理する軽量なランナー。
quest_service.py 側にあった上記の実行時チェックは Issue #330 で完全に退役済み
(quest_service.sync_master_data 参照)であり、migrations/ (0000ベースライン+
0001以降) がスキーマの唯一の定義元である。今後のスキーマ変更は
本モジュール経由（migrations/ 配下への追加）で行うこと。
"""
import os
import re
import sqlite3
from typing import List, Set

from core.logger import setup_logging

logger = setup_logging("core.migrations")

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")

# 「既に別経路(旧来の実行時ALTER等)で適用済み」と断定できる、既知のSQLiteエラー文言のみ。
# それ以外のOperationalError(DBロック・ディスクフル・SQL誤り等)は失敗として扱う。
# #440: 以前は単純な部分文字列一致("in"演算子)だったため、無関係なエラーメッセージが
# たまたまこれらの語を含む場合に誤って「適用済み」と握りつぶす恐れがあった。
# SQLiteが実際に返す既知のエラー文言の形("duplicate column name: <col>"、
# "table <name> already exists"、"index <name> already exists")に厳密に
# アンカーした正規表現に置き換える。
_ALREADY_APPLIED_ERROR_PATTERNS = (
    re.compile(r"^duplicate column name: \S"),
    re.compile(r"^table \S+ already exists$"),
    re.compile(r"^index \S+ already exists$"),
)


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


def _strip_line_comment(line: str) -> str:
    """1行から `--` 以降の行コメントを取り除く。シングルクォート文字列内の `--`
    （通常のマイグレーションでは想定しにくいが）は保持する単純な状態機械。"""
    in_string = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'":
            in_string = not in_string
        elif not in_string and ch == "-" and line[i:i + 2] == "--":
            return line[:i]
        i += 1
    return line


def _split_statements(sql: str) -> List[str]:
    """
    マイグレーションSQLを ';' 区切りのステートメント単位に分割する。

    このリポジトリのマイグレーション規約(migrations/README.md)は「ALTER TABLE ...
    ADD COLUMN を先頭に、後続はシンプルなUPDATE」という単純な構成のみを前提として
    いるため、文字列/BLOBリテラル内にセミコロンを含むような複雑な文は想定しない。
    ただし、このリポジトリの規約(コメント・docstringは日本語で書く)では
    ALTER文の前に長い日本語の説明コメントを書くことが多く(#411 品質:
    _split_statementsのロバスト化)、そのプローズ文中に句点代わりの
    セミコロンが登場すると、
    行コメント全体をまだ読み切っていないのに文が分割されてしまう恐れがある。
    分割前に各行の `--` 以降の行コメントを取り除くことで、コメント内の
    セミコロンが誤った分割点にならないようにする。
    """
    cleaned = "\n".join(_strip_line_comment(line) for line in sql.splitlines())
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def apply_pending_migrations(conn: sqlite3.Connection) -> None:
    """
    migrations/ 配下の未適用 *.sql をファイル名昇順で適用する。

    各マイグレーションファイルは、既に適用済みの環境（列が既に存在する等）に対して
    再実行されても致命的にならないよう ALTER TABLE を先頭に書くことを想定している。
    ファイルはステートメント単位(`_split_statements`)で1文ずつ実行する。以前は
    `conn.executescript()` でスクリプト全体を一度に実行していたため、先頭の
    ALTER TABLE が「duplicate column」で失敗すると、その時点でスクリプト全体の
    実行が中断され、後続のデータ移行文(UPDATE等)が1文も実行されないまま
    マイグレーション全体が適用済み記録されてしまっていた(#99)。ステートメントごとに
    実行することで、「duplicate column」「already exists」のように「既に別経路
    （旧来の実行時チェック等）で適用済み」と断定できる既知のエラー文言が出た
    ステートメントのみを警告ログとともにスキップし、後続の文の実行を継続する。
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
            for statement in _split_statements(sql):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as e:
                    message = str(e).lower()
                    if not any(pattern.search(message) for pattern in _ALREADY_APPLIED_ERROR_PATTERNS):
                        raise
                    logger.warning(
                        f"⚠️ Migration '{filename}': statement skipped "
                        f"(likely already applied via a different path): {e}\n  SQL: {statement}"
                    )

            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (filename,))
            conn.commit()
            logger.info(f"✅ Migration applied: {filename}")
        except sqlite3.OperationalError as e:
            conn.rollback()
            logger.error(f"❌ Migration '{filename}' failed and was not recorded as applied: {e}")
            raise
