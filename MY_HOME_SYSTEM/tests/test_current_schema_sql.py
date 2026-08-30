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
