# MY_HOME_SYSTEM/tests/test_empty_db_e2e.py
"""
Issue #330 (スキーマ管理のmigrations/一本化) の回帰テスト。

8/22レビューレポート「テスト追加優先順位: Critical」で推奨されていた
「空DBからのE2E」テスト。H-2(新規/再構築DBで承認フローが無効化される)の
再発防止として、migrations/ だけで構築したDBが実経路
(サーバー起動 → sync_master → 子の完了 → 親の承認) を通ることを検証する狙いで
当初 TestEmptyDbEndToEnd クラスを追加したが、#414 C-L5 の棚卸しで
tests/test_h2_fresh_db_e2e.py::TestFreshDbApprovalFlowE2E と実質的に同一の
回帰(空DB→migrations→sync_master→子の完了→親の承認)を重複してカバーしている
ことが判明したため削除した。当該フロー(実quest_dataを使用)の検証は
test_h2_fresh_db_e2e.py 側に一本化されている。本ファイルは、以下の
スキーマ移設検証(TestMigrationsOnlySchemaEquivalence)のみを担う。

init_unified_db.init_db() がスキーマ定義を持っていた時代(リファクタ前)に
構築されるスキーマのスナップショット(tests/fixtures/legacy_init_db_schema.json)
と、migrations/ のみで構築したスキーマを突き合わせ、ベースライン移設
(0000_baseline_schema.sql)の写経ミス・取りこぼしを機械的に検知する。
"""
import json
import os
import sqlite3
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.migrations import apply_pending_migrations

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "legacy_init_db_schema.json"
)


class TestMigrationsOnlySchemaEquivalence:
    """migrations/ のみで構築したスキーマが、旧init_db()構築スキーマを包含すること。"""

    def _build_migrations_only_db(self):
        """init_db()を経由せず、空DBに apply_pending_migrations() だけを適用する。

        0000_baseline_schema.sql が(他のマイグレーションのテーブル存在前提を
        満たす形で)最初に適用され、単独でフルスキーマを構築できることの検証を兼ねる。
        """
        conn = sqlite3.connect(":memory:")
        apply_pending_migrations(conn)
        return conn

    def test_all_legacy_tables_and_columns_are_present(self):
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            expected = json.load(f)

        conn = self._build_migrations_only_db()
        try:
            missing = []
            for table, columns in expected["tables"].items():
                actual_cols = {
                    row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                }
                if not actual_cols:
                    missing.append(f"table '{table}' not created")
                    continue
                for col in columns:
                    # 将来のマイグレーションによるカラム追加は許容する(包含チェック)
                    if col not in actual_cols:
                        missing.append(f"column '{table}.{col}' missing")
            assert not missing, (
                "migrations/のみで構築したDBが旧init_db()のスキーマを包含していません"
                "(0000_baseline_schema.sqlの移設漏れの疑い): " + "; ".join(missing)
            )
        finally:
            conn.close()

    def test_all_legacy_indexes_are_present(self):
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            expected = json.load(f)

        conn = self._build_migrations_only_db()
        try:
            actual_indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                )
            }
            for idx in expected["indexes"]:
                assert idx in actual_indexes, f"index '{idx}' missing"
        finally:
            conn.close()

    def test_reapplying_baseline_on_built_db_is_safe(self):
        """0000ベースラインは既存DB(全テーブル存在済み)への再実行でno-opであること。
        本番DB(0001〜0008適用済み・0000未記録)への初回適用シナリオに相当する。"""
        conn = self._build_migrations_only_db()
        try:
            # 適用記録から0000だけを消し、再度ランナーを回す(=本番初回適用の再現)
            conn.execute(
                "DELETE FROM schema_migrations WHERE version = '0000_baseline_schema.sql'"
            )
            conn.commit()
            before = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
                )
            }
            apply_pending_migrations(conn)  # 例外なく完走すること
            after = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
                )
            }
            assert before == after
        finally:
            conn.close()
