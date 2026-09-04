# MY_HOME_SYSTEM/tests/test_h2_fresh_db_e2e.py
"""
H-2 の回帰防止テスト: 「新規/再構築 DB では承認フローが無効化される」問題。

空DB(init_unified_db.init_db()によるmigrations適用込みの新規作成)に対して、
実際の運用パスと同じ順序で
  1. GameSystem.sync_master_data() でマスタデータを同期
  2. 子供ユーザーが quest を complete → pending
  3. 親ユーザーが quest を approve → 報酬確定
を実行し、途中でroleがNULLのままにならず、子供の完了が承認スキップで
即時報酬付与にならないこと、親の承認が403にならないことを検証する。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import GameSystem, QuestService

# quest_data.py に実在する target='son' の日次クエスト(TV_UNLOCK対象外)
SON_QUEST_ID = 1009


class TestFreshDbApprovalFlowE2E:
    def test_role_is_populated_on_first_sync_for_brand_new_users(self, isolated_db):
        """新規DB(quest_usersが空)への初回sync_master_dataで、
        role列がNULLのまま挿入されないこと。"""
        with common.get_db_cursor() as cur:
            count = cur.execute("SELECT COUNT(*) c FROM quest_users").fetchone()["c"]
        assert count == 0

        game_system = GameSystem()
        result = game_system.sync_master_data()
        assert result["status"] == "synced"

        with common.get_db_cursor() as cur:
            rows = cur.execute("SELECT user_id, role FROM quest_users").fetchall()
        roles_by_user = {row["user_id"]: row["role"] for row in rows}

        assert roles_by_user["dad"] == "role_adult"
        assert roles_by_user["mom"] == "role_adult"
        assert roles_by_user["son"] == "role_child"
        assert roles_by_user["daughter"] == "role_child"
        assert None not in roles_by_user.values()

    def test_child_complete_then_adult_approve_succeeds_on_fresh_db(self, isolated_db):
        """空DB→migrations→sync_master→子供complete→親approveが
        承認スキップにも403にもならず、正しく完結すること。

        #414 C-L5: 以前は tests/test_empty_db_e2e.py::TestEmptyDbEndToEnd に
        ほぼ同一のフロー(空DB→sync_master→子の完了→親の承認)をHTTP層
        (TestClient経由)で検証する重複テストが存在したが、HTTP層の
        complete/approve自体は test_quest_router_api.py 側で(役割は事前投入の
        seeded_clientを使って)別途カバーされており、ルーターはロジックを
        持たない薄い委譲層(CLAUDE.md参照)のため「空DB+HTTP経由」の組み合わせは
        新たな分岐を検証していなかった。実quest_data(SON_QUEST_ID)を使う本テスト
        に一本化した。"""
        game_system = GameSystem()
        game_system.sync_master_data()

        quest_service = QuestService()

        # 子供が完了報告 → roleがrole_childとして認識され、即時報酬ではなくpendingになること
        complete_result = quest_service.process_complete_quest("son", SON_QUEST_ID)
        assert complete_result["status"] == "pending"
        assert complete_result["earnedGold"] == 0

        with common.get_db_cursor() as cur:
            hist = cur.execute(
                "SELECT * FROM quest_history WHERE user_id='son' AND quest_id=?",
                (SON_QUEST_ID,),
            ).fetchone()
        assert hist is not None
        assert hist["status"] == "pending"

        with common.get_db_cursor() as cur:
            son_gold_before = cur.execute(
                "SELECT gold FROM quest_users WHERE user_id='son'"
            ).fetchone()["gold"]

        # 親が承認 → roleがrole_adultとして認識され、403にならず報酬が確定すること
        approve_result = quest_service.process_approve_quest("dad", hist["id"])
        assert approve_result["status"] == "success"

        with common.get_db_cursor() as cur:
            son_row = cur.execute(
                "SELECT gold, exp FROM quest_users WHERE user_id='son'"
            ).fetchone()
            hist_after = cur.execute(
                "SELECT status FROM quest_history WHERE id=?", (hist["id"],)
            ).fetchone()

        assert son_row["gold"] > son_gold_before
        assert hist_after["status"] == "approved"


class TestSyncMasterDataTimestampFormatIsConsistent:
    """
    Low: sync_master_data() の quest_users upsert だけが updated_at に
    naive な datetime.datetime.now() (例: '2026-08-22 19:30:00.123456') を
    書き込んでおり、ファイル内の他の全ての箇所(avatar更新等)は
    common.get_now_iso() (ISO8601 + JSTオフセット, 例: '...T...+09:00') を
    使っていて形式が不統一だった。
    """

    def test_updated_at_uses_get_now_iso_format_not_naive_datetime_str(self, isolated_db):
        game_system = GameSystem()
        game_system.sync_master_data()

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT updated_at FROM quest_users WHERE user_id='dad'").fetchone()

        updated_at = row["updated_at"]
        assert "T" in updated_at and "+09:00" in updated_at, (
            f"quest_users.updated_at should use the ISO8601+JST format produced by "
            f"common.get_now_iso() (like every other timestamp column in this file), "
            f"got: {updated_at!r}"
        )
