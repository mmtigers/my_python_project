# MY_HOME_SYSTEM/tests/test_current_schema_sql.py
"""
current_schema.sql (本番DBのスキーマダンプとしてドキュメント上参照される静的ファイル)が、
migrations/配下の全マイグレーションを反映済みであることを検証する。

current_schema.sqlはコードから実行されない参考ドキュメントのため、マイグレーションを
追加してもこのファイルの更新を忘れても何のエラーも起きない(Issue #115で顕在化: 0006の
nas_usage_percent列とschema_migrationsテーブル自体が未反映のままだった)。このテストは、
各マイグレーションがALTER TABLEで追加するカラムが対応するCREATE TABLE文に含まれているかを
機械的にチェックすることで、今後の更新忘れを検知する。
"""
import os
import re

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")
SCHEMA_FILE = os.path.join(BASE_DIR, "current_schema.sql")

_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)", re.IGNORECASE
)


def _migration_added_columns():
    """migrations/*.sql から ALTER TABLE ... ADD COLUMN で追加される(テーブル名, カラム名, ファイル名)を全て抽出する"""
    pairs = []
    for filename in sorted(os.listdir(MIGRATIONS_DIR)):
        if not filename.endswith(".sql"):
            continue
        with open(os.path.join(MIGRATIONS_DIR, filename), "r", encoding="utf-8") as f:
            content = f.read()
        for table, column in _ALTER_ADD_COLUMN_RE.findall(content):
            pairs.append((table, column, filename))
    return pairs


def _create_table_statement(schema_sql: str, table: str) -> str:
    """current_schema.sql から指定テーブルの CREATE TABLE 文本体を抜き出す(次の CREATE TABLE またはファイル末尾まで)"""
    match = re.search(
        rf"CREATE TABLE {re.escape(table)}\s*\(.*?(?=\nCREATE TABLE |\Z)",
        schema_sql,
        re.DOTALL,
    )
    return match.group(0) if match else ""


def test_all_migration_added_columns_are_present_in_current_schema_sql():
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    missing = []
    for table, column, filename in _migration_added_columns():
        statement = _create_table_statement(schema_sql, table)
        if not statement:
            missing.append(f"{filename}: table '{table}' not found in current_schema.sql")
            continue
        if not re.search(rf"\b{re.escape(column)}\b", statement):
            missing.append(f"{filename}: column '{table}.{column}' missing from current_schema.sql")

    assert not missing, "current_schema.sql is stale (Issue #115): " + "; ".join(missing)


def test_schema_migrations_tracking_table_is_present_in_current_schema_sql():
    """core/migrations.py が実際に作成する schema_migrations テーブルも
    current_schema.sql のスナップショットに含まれているべき"""
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    assert "CREATE TABLE schema_migrations" in schema_sql



# Issue #543: migrations/ を全適用したスキーマと current_schema.sql の列定義(型・NOT NULL・DEFAULT)を
# 比較する。README(migrations/README.md)に記載した既知の差分だけを許容し、それ以外の差分
# (新しいマイグレーションの反映漏れ、README に無い差分の混入)を検知する。
import re as _re
import sqlite3 as _sqlite3
import sys as _sys

_sys.path.append(BASE_DIR)

# (テーブル, 列) -> (migrated 側の (型, notnull, default), current_schema 側の同タプル)。None は列が無いことを表す。
KNOWN_SCHEMA_DIFFS = {
    ("car_records", "timestamp"): (("DATETIME", 0, None), ("DATETIME", 1, None)),
    ("daily_records", "category"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("daily_records", "date"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("daily_records", "timestamp"): (("DATETIME", 0, None), ("DATETIME", 1, None)),
    ("daily_records", "user_id"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("daily_records", "value"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("device_records", "battery_level"): (None, ("INTEGER", 0, None)),
    ("device_records", "device_id"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("device_records", "device_name"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("device_records", "device_type"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("food_records", "created_at"): (None, ("TEXT", 0, None)),
    ("food_records", "date"): (None, ("TEXT", 0, None)),
    ("food_records", "menu"): (None, ("TEXT", 0, None)),
    ("health_records", "timestamp"): (("DATETIME", 0, None), ("DATETIME", 1, None)),
    ("ohayo_records", "timestamp"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("ohayo_records", "user_id"): (("TEXT", 0, None), ("TEXT", 1, None)),
    ("party_state", "max_hp"): (("INTEGER", 0, "100"), ("INTEGER", 0, "1000")),
    ("party_state", "week_start_date"): (("TEXT", 0, None), ("TEXT", 0, "''")),
}


def _table_columns(conn):
    out = {}
    for (table,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        out[table] = {
            row[1]: (row[2].upper(), row[3], row[4])
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
    return out


def test_migrated_schema_matches_current_schema_sql_except_known_diffs():
    from core.migrations import apply_pending_migrations

    migrated_conn = _sqlite3.connect(":memory:")
    apply_pending_migrations(migrated_conn)
    migrated = _table_columns(migrated_conn)

    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    # sqlite_sequence はユーザーが CREATE できない内部テーブル(ダンプ由来の行)なので除外して読み込む
    schema_sql = _re.sub(r"CREATE TABLE sqlite_sequence\s*\([^;]*\);", "", schema_sql)
    current_conn = _sqlite3.connect(":memory:")
    current_conn.executescript(schema_sql)
    current = _table_columns(current_conn)

    assert set(migrated) == set(current), (
        f"テーブル集合が一致しません: migrated のみ={sorted(set(migrated) - set(current))}, "
        f"current_schema.sql のみ={sorted(set(current) - set(migrated))}"
    )

    actual_diffs = {}
    for table in sorted(migrated):
        for column in sorted(set(migrated[table]) | set(current[table])):
            a, b = migrated[table].get(column), current[table].get(column)
            if a != b:
                actual_diffs[(table, column)] = (a, b)

    unexpected = {k: v for k, v in actual_diffs.items() if KNOWN_SCHEMA_DIFFS.get(k) != v}
    stale = {k: v for k, v in KNOWN_SCHEMA_DIFFS.items() if actual_diffs.get(k) != v}
    assert not unexpected, f"migrations/README.md に無い差分があります: {unexpected}"
    assert not stale, f"解消済みなのに許容リストに残っている差分があります: {stale}"
