# MY_HOME_SYSTEM/init_unified_db.py
import argparse
import contextlib
import os
import sqlite3
import sys
from typing import List, Dict
import config
import common
from core.migrations import apply_pending_migrations

logger = common.setup_logging("init_db")

# current_schema.sql の既定の出力先(本ファイルと同じディレクトリ)
CURRENT_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "current_schema.sql")

# current_schema.sql の先頭に書き出すヘッダ。生成物であることと再生成コマンドを明記する。
CURRENT_SCHEMA_HEADER = """\
-- このファイルは生成物です。手で編集しないでください。
--
-- migrations/ 配下の全マイグレーションを空の SQLite DB へ適用した結果のスキーマ
-- (sqlite_master の CREATE 文を作成順に列挙したもの)で、参照用ドキュメントです。
-- 実行時にはどこからも読み込まれず、スキーマの唯一の定義元は migrations/ です
-- (Issue #330。位置づけの経緯は migrations/README.md の「current_schema.sql との関係」参照)。
--
-- 再生成: MY_HOME_SYSTEM/ で
--   python init_unified_db.py --dump-schema
-- tests/test_current_schema_sql.py が「本ファイル == 再生成結果」を検証するため、
-- マイグレーションを追加・変更した PR では必ず再生成してコミットすること。
"""

def validate_schema_integrity(conn: sqlite3.Connection) -> None:
    """
    設計書(3.1)に基づくスキーマ整合性の自動検証を行う。
    主要テーブルのカラム定義が期待通りかチェックする。
    """
    # 検証対象のテーブル定義 (テーブル名: [必須カラムリスト])
    expected_schemas: Dict[str, List[str]] = {
        # New Core Tables
        "users": ["name", "level", "xp", "gold", "status"],
        "quests": ["title", "description", "xp_reward", "gold_reward", "difficulty"],
        config.SQLITE_TABLE_DAILY_LOGS: ["category", "detail", "timestamp"],
        config.SQLITE_TABLE_SWITCHBOT_LOGS: ["device_id", "temperature", "humidity", "timestamp"],
        config.SQLITE_TABLE_POWER_USAGE: ["wattage", "timestamp"],
        
        # Legacy/Existing Tables (Critical for current operation)
        config.SQLITE_TABLE_CHILD: ["child_name", "condition", "timestamp"],
        config.SQLITE_TABLE_FOOD: ["menu_category", "meal_time_category"],
        config.SQLITE_TABLE_DEFECATION: ["record_type", "condition"]
    }

    cur = conn.cursor()
    issues: List[str] = []

    for table, columns in expected_schemas.items():
        try:
            cur.execute(f"PRAGMA table_info({table})")
            existing_cols = [row[1] for row in cur.fetchall()]
            
            if not existing_cols:
                # 初期化前は存在しないのが正常だが、init後に呼ぶ前提
                issues.append(f"Missing Table: {table}")
                continue

            for col in columns:
                if col not in existing_cols:
                    issues.append(f"Table '{table}' missing column '{col}'")
        except Exception as e:
            issues.append(f"Error checking {table}: {e}")

    if issues:
        for issue in issues:
            logger.warning(f"⚠️ Schema Integrity Issue: {issue}")
    else:
        logger.info("✅ Schema Integrity Validation Passed.")

def init_db() -> None:
    """
    アプリケーションで使用する全SQLiteテーブルを初期化する。

    Issue #330 (スキーマ管理のmigrations/一本化) 以降、本関数はスキーマ定義を
    一切持たない薄いラッパーである。ベースラインスキーマを含む全スキーマは
    migrations/ 配下のSQL(空DBでは 0000_baseline_schema.sql が最初に適用される)が
    唯一の定義元であり、本関数は接続の準備とマイグレーション適用・検証のみを行う。
    以前ここにあったCREATE TABLE群は migrations/0000_baseline_schema.sql へ移設済み。
    """
    logger.info(f"データベース初期化開始: {config.SQLITE_DB_PATH}")

    with common.get_db_cursor(commit=True) as cur:
        # WALモード有効化
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
            # PRAGMA journal_mode は結果行('wal'等)を返す。未消費のまま後続の
            # apply_pending_migrations() がcommitすると "cannot commit transaction -
            # SQL statements in progress" になるため、必ず読み切る。
            cur.fetchall()
        except Exception as e:
            logger.warning(f"⚠️ WALモード設定失敗: {e}")

        # バージョン管理されたマイグレーション (migrations/ 配下) を適用する。
        # 空DBでは 0000_baseline_schema.sql が全テーブル・インデックスを作成し、
        # 0001以降がカラム追加・データ移行を積み上げる。既存DBでは未適用分のみ実行される。
        apply_pending_migrations(cur.connection)

    # 検証実行
    # #411 S-L8: `with sqlite3.connect(...) as conn:` は接続をcloseしない
    # (sqlite3の既知の挙動。commit/rollbackのみ行う)ため、contextlib.closingで
    # 明示的にcloseする。
    try:
        with contextlib.closing(sqlite3.connect(config.SQLITE_DB_PATH)) as conn:
            validate_schema_integrity(conn)
    except Exception as e:
        logger.error(f"Schema Validation Failed: {e}")

    logger.info("✅ 全テーブルの準備・初期化が完了しました。")


def dump_schema_sql() -> str:
    """migrations/ を空の :memory: DB へ全適用した結果のスキーマを SQL 文字列として返す。

    current_schema.sql の生成元。sqlite_master を作成順(rowid 順)に読み、sql 列を持つ
    エントリ(テーブル・インデックス。自動生成の内部インデックスは sql が NULL のため除外)を
    `;` 区切りで列挙する。SQLite が内部で作る sqlite_sequence(AUTOINCREMENT 用)も
    実 DB のダンプと同じく含まれる。実 DB(config.SQLITE_DB_PATH)には一切触れない。
    """
    with contextlib.closing(sqlite3.connect(":memory:")) as conn:
        apply_pending_migrations(conn)
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"
        ).fetchall()
    body = "\n".join(f"{sql};" for (sql,) in rows)
    return CURRENT_SCHEMA_HEADER + body + "\n"


def write_current_schema(path: str = CURRENT_SCHEMA_PATH) -> str:
    """dump_schema_sql() の結果を path へ書き出し、書き出した内容を返す。"""
    content = dump_schema_sql()
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return content


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="SQLite DB の初期化(migrations/ の適用)。--dump-schema で current_schema.sql を再生成する。"
    )
    parser.add_argument(
        "--dump-schema", nargs="?", const=CURRENT_SCHEMA_PATH, metavar="PATH",
        help=f"DB を初期化する代わりに、migrations/ 全適用後のスキーマを PATH へ書き出す(既定: {CURRENT_SCHEMA_PATH})",
    )
    args = parser.parse_args(argv)
    if args.dump_schema:
        write_current_schema(args.dump_schema)
        print(f"スキーマを書き出しました: {args.dump_schema}")
        return 0
    init_db()
    return 0


if __name__ == "__main__":
    sys.exit(main())
