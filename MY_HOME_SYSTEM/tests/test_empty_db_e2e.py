# MY_HOME_SYSTEM/tests/test_empty_db_e2e.py
"""
Issue #330 (スキーマ管理のmigrations/一本化) の回帰テスト。

8/22レビューレポート「テスト追加優先順位: Critical」で推奨されていた
「空DBからのE2E」テスト。H-2(新規/再構築DBで承認フローが無効化される)の
再発防止として、migrations/ だけで構築したDBが実経路
(サーバー起動 → sync_master → 子の完了 → 親の承認) を通ることを検証する。

あわせて、init_unified_db.init_db() がスキーマ定義を持っていた時代
(リファクタ前) に構築されるスキーマのスナップショット
(tests/fixtures/legacy_init_db_schema.json) と、migrations/ のみで構築した
スキーマを突き合わせ、ベースライン移設(0000_baseline_schema.sql)の
写経ミス・取りこぼしを機械的に検知する。
"""
import json
import os
import sqlite3
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
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


class TestEmptyDbEndToEnd:
    """空DB → migrations構築 → sync → 子の完了 → 親の承認 の実経路E2E (H-2再発防止)。"""

    def test_fresh_db_supports_sync_complete_approve_flow(self, isolated_db, api_client):
        # 1. マスタ同期: role/reset_period/description カラムが存在しないと失敗する
        #    (レガシー実行時ALTERは退役済みのため、migrations供給の実証になる)
        res = api_client.post("/api/quest/sync_master")
        assert res.status_code == 200

        # 2. quest_data 由来のユーザーにroleが投入されていること (H-2の核心)
        with common.get_db_cursor() as cur:
            dad_role = cur.execute(
                "SELECT role FROM quest_users WHERE user_id='dad'"
            ).fetchone()["role"]
            daughter_role = cur.execute(
                "SELECT role FROM quest_users WHERE user_id='daughter'"
            ).fetchone()["role"]
        assert dad_role == "role_adult"
        assert daughter_role == "role_child"

        # 3. 時間帯・曜日制約のない決定的なテスト用クエストを追加
        #    (quest_dataの実クエストはstart_time/days依存で時刻により結果が変わるため)
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) "
                "VALUES (9999, 'E2E検証クエスト', 'daily', 10, 5)"
            )

        # 4. 子の完了 → 承認待ち(pending)になること
        res = api_client.post(
            "/api/quest/complete", json={"user_id": "daughter", "quest_id": 9999}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "pending"

        with common.get_db_cursor() as cur:
            history_id = cur.execute(
                "SELECT id FROM quest_history WHERE user_id='daughter' AND quest_id=9999 "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]

        # 5. 親の承認 → 成功し、報酬が付与されること
        res = api_client.post(
            "/api/quest/approve", json={"approver_id": "dad", "history_id": history_id}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        with common.get_db_cursor() as cur:
            status = cur.execute(
                "SELECT status FROM quest_history WHERE id=?", (history_id,)
            ).fetchone()["status"]
            gold = cur.execute(
                "SELECT gold FROM quest_users WHERE user_id='daughter'"
            ).fetchone()["gold"]
        assert status == "approved"
        assert gold >= 5
