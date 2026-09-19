# MY_HOME_SYSTEM/tests/test_db_indexes.py
"""
init_unified_db.init_db() が高頻度アクセスのテーブルにインデックスを作成することの
回帰テスト (CODE_REVIEW_REPORT.md 3.2 / Issue #734 の再発防止)。

power_usage / switchbot_meter_logs / device_records はスケジューラにより
5〜10分間隔で継続的に書き込まれ、「直近の値」を ORDER BY timestamp DESC LIMIT 1
で頻繁に読み取る。インデックスがないと将来データ量が増えた際に全件スキャンになる。

Issue #734 (AUDIT-004): このテストの対象が上記3テーブルだけに限定されていたことが、
**アプリで最も読み書きされる quest_history に索引が1つも無い**状態が誰にも
検知されないまま続いた直接の原因だった(根本原因 RC-4: 自動チェックの保証範囲が
その出力の見た目より狭い)。全クライアントが10秒間隔(useGameData.ts の
refetchInterval)で叩く GET /api/quest/data が、1リクエストあたり quest_history を
3回フルスキャンしていた。対象を「アプリのホットパスが叩くテーブル」へ拡張し、
さらに EXPLAIN QUERY PLAN で「索引があるのにクエリが使っていない」ことも検知する。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor

EXPECTED_INDEXES = {
    # スケジューラが継続的に書き込む時系列テーブル
    "power_usage": "idx_power_usage_device_ts",
    "switchbot_meter_logs": "idx_switchbot_logs_device_ts",
    "device_records": "idx_device_records_device_ts",
    # アプリのホットパス (Issue #734 で追加)
    "quest_history": "idx_quest_history_user_quest_completed",
    "user_inventory": "idx_user_inventory_user_status",
    "reward_history": "idx_reward_history_user_reward_redeemed",
    "routine_progress": "idx_routine_progress_user_date",
    "routine_step_events": "idx_routine_step_events_user_date",
}


def _index_names(cur, table: str) -> set:
    rows = cur.execute(f"PRAGMA index_list({table})").fetchall()  # nosec B608
    return {row["name"] for row in rows}


def _index_columns(cur, index_name: str) -> list:
    info = cur.execute(f"PRAGMA index_info({index_name})").fetchall()  # nosec B608
    return [row["name"] for row in info]


def _plan(cur, sql: str, params=()) -> str:
    rows = cur.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()  # nosec B608
    return "\n".join(row["detail"] for row in rows)


class TestExpectedIndexesExist:
    def test_power_usage_has_device_timestamp_index(self, isolated_db):
        with get_db_cursor() as cur:
            assert EXPECTED_INDEXES["power_usage"] in _index_names(cur, "power_usage")

    def test_switchbot_meter_logs_has_device_timestamp_index(self, isolated_db):
        with get_db_cursor() as cur:
            assert EXPECTED_INDEXES["switchbot_meter_logs"] in _index_names(cur, "switchbot_meter_logs")

    def test_device_records_has_device_timestamp_index(self, isolated_db):
        with get_db_cursor() as cur:
            assert EXPECTED_INDEXES["device_records"] in _index_names(cur, "device_records")

    def test_index_columns_cover_device_id_and_timestamp(self, isolated_db):
        """インデックスが (device_id, timestamp) の複合であることを確認する"""
        with get_db_cursor() as cur:
            columns = _index_columns(cur, EXPECTED_INDEXES["power_usage"])
            assert columns == ["device_id", "timestamp"]

    def test_every_hot_path_table_has_its_expected_index(self, isolated_db):
        """EXPECTED_INDEXES に挙げた全テーブルについて索引が存在すること。

        新しいホットパスのテーブルを足したらこの辞書にも足すこと。
        """
        with get_db_cursor() as cur:
            missing = {
                table: index
                for table, index in EXPECTED_INDEXES.items()
                if index not in _index_names(cur, table)
            }
        assert not missing, f"期待する索引が無いテーブルがあります: {missing}"


class TestQuestHistoryIndexes:
    """Issue #734 (AUDIT-004): quest_history / user_inventory / reward_history の索引。"""

    def test_quest_history_has_both_indexes(self, isolated_db):
        with get_db_cursor() as cur:
            names = _index_names(cur, "quest_history")
        assert "idx_quest_history_user_quest_completed" in names
        assert "idx_quest_history_status_completed" in names

    def test_user_quest_index_includes_status_to_be_covering(self, isolated_db):
        """status を末尾に含めることで、集約クエリが本体テーブルに触れなくなる。

        含めないと `SCAN ... USING INDEX` 止まりで行ごとに本体を引きにいく。
        """
        with get_db_cursor() as cur:
            columns = _index_columns(cur, "idx_quest_history_user_quest_completed")
        assert columns == ["user_id", "quest_id", "completed_at", "status"]

    def test_get_all_view_data_aggregate_uses_the_covering_index(self, isolated_db):
        """10秒ポーリングの最ホットパス(game_system の last_completed_map)が
        被覆索引でスキャンされ、GROUP BY の一時Bツリーも作られないこと。

        `status != 'rejected'` を `status IN ('pending','approved')` に書き換えると
        SQLite は idx_quest_history_status_completed を選び、**GROUP BY のために
        一時Bツリーを作る**ので、むしろ悪化する(実測済み)。書き換えないこと。
        """
        sql = """
            SELECT user_id, quest_id, MAX(completed_at) AS last_completed_at
            FROM quest_history
            WHERE status != 'rejected'
            GROUP BY user_id, quest_id
        """
        with get_db_cursor() as cur:
            plan = _plan(cur, sql)
        assert "COVERING INDEX idx_quest_history_user_quest_completed" in plan
        assert "TEMP B-TREE" not in plan

    def test_pending_list_uses_the_status_index(self, isolated_db):
        with get_db_cursor() as cur:
            plan = _plan(
                cur,
                "SELECT * FROM quest_history WHERE status='pending' ORDER BY completed_at DESC",
            )
        assert "idx_quest_history_status_completed" in plan
        assert "TEMP B-TREE" not in plan

    def test_approved_since_uses_the_status_index(self, isolated_db):
        with get_db_cursor() as cur:
            plan = _plan(
                cur,
                "SELECT * FROM quest_history WHERE status='approved' AND completed_at >= ? "
                "ORDER BY completed_at DESC",
                ("2026-01-01",),
            )
        assert "idx_quest_history_status_completed" in plan
        assert "TEMP B-TREE" not in plan

    def test_streak_and_spam_lookup_uses_the_user_quest_index(self, isolated_db):
        """連続達成ボーナス・スパムチェック・前提クエスト判定の共通形。"""
        with get_db_cursor() as cur:
            plan = _plan(
                cur,
                "SELECT * FROM quest_history WHERE user_id = ? AND quest_id = ? "
                "AND status != 'rejected' ORDER BY completed_at DESC LIMIT 1",
                ("u1", 1),
            )
        assert "idx_quest_history_user_quest_completed" in plan
        assert "TEMP B-TREE" not in plan

    def test_inventory_lookup_uses_the_user_status_index(self, isolated_db):
        with get_db_cursor() as cur:
            plan = _plan(
                cur,
                "SELECT ui.id FROM user_inventory ui "
                "JOIN reward_master rm ON ui.reward_id = rm.reward_id "
                "WHERE ui.user_id = ? AND ui.status = 'owned' ORDER BY ui.purchased_at DESC",
                ("u1",),
            )
        assert "idx_user_inventory_user_status" in plan

    def test_purchase_spam_check_uses_the_covering_index(self, isolated_db):
        with get_db_cursor() as cur:
            plan = _plan(
                cur,
                "SELECT redeemed_at FROM reward_history WHERE user_id = ? AND reward_id = ? "
                "ORDER BY redeemed_at DESC LIMIT 1",
                ("u1", 1),
            )
        assert "idx_reward_history_user_reward_redeemed" in plan
        assert "TEMP B-TREE" not in plan
