# MY_HOME_SYSTEM/tests/test_quest_cancellation_audit.py
"""クエスト取消の監査証跡 (`quest_cancellation_audit`) の回帰テスト。

Issue #762 (AUDIT-034) のステップ1。取消(`_revert_and_delete_history`)は
`quest_history` の行を**物理削除**するため、削除前にその内容を写しておかないと
「誰がいつ何を取り消したか」がログファイル以外のどこにも残らず、家族の年代記
(`user_service._fetch_full_adventure_logs`)の原資が復元不能に消える。
Issue #733 の保持期間削除を有効化した後は「取消で消えた」と「保持期間で消えた」
の区別もつかなくなる。

**本テストが固定しないこと**: `quest_history` の意味論は一切変えていない。
取消済みを年代記に表示するか・`status='cancelled'` の論理削除へ切り替えるかは
仕様判断として #762 で未決であり、ここでは「後からその判断を実データに基づいて
下せる状態にする」ことだけを固定する。
"""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
from services import quest_service as qs_module
from services.quest_service import ROLE_ADULT, ROLE_CHILD

TS = "2026-09-19 10:00:00"


@pytest.fixture
def seeded(isolated_db, monkeypatch):
    """大人1人・子ども1人とクエスト1件。完了 → 自動承認まで通せる状態。"""
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: True)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES ('dad', 'Dad', 'Warrior', 1, 0, 500, ?)",
            (ROLE_ADULT,),
        )
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            # 連携クエストのカスケード取消で #356 のガード(残高 < 付与額なら拒否)に
            # 引っかからないよう、付与額を戻せる残高を持たせる
            "VALUES ('kid', 'Kid', 'Mage', 1, 0, 500, ?)",
            (ROLE_CHILD,),
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, exp_gain, gold_gain, quest_type, target_user) "
            "VALUES (901, 'テストクエスト', 10, 100, 'daily', 'all')"
        )
    return isolated_db


def _audit_rows() -> list:
    with get_db_cursor() as cur:
        return cur.execute(
            "SELECT * FROM quest_cancellation_audit ORDER BY id"
        ).fetchall()


def _latest_history_id() -> int:
    with get_db_cursor() as cur:
        return cur.execute(
            "SELECT id FROM quest_history ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]


def _insert_history(cur, user_id, status="approved", **over):
    cols = {
        "user_id": user_id,
        "quest_id": 901,
        "quest_title": "テストクエスト",
        "status": status,
        "completed_at": TS,
        "exp_earned": 10,
        "gold_earned": 100,
        "medals_earned": 0,
        "linked_history_id": None,
    }
    cols.update(over)
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    cur.execute(
        f"INSERT INTO quest_history ({names}) VALUES ({marks})",
        tuple(cols.values()),
    )
    return cur.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


class TestApprovedCancelIsRecorded:
    def test_deleted_row_content_is_preserved(self, seeded):
        service = qs_module.QuestService()
        approval = qs_module.ApprovalService()
        service.process_complete_quest("dad", 901)  # 大人は自動承認
        hist_id = _latest_history_id()

        approval.process_cancel_quest("dad", hist_id)

        rows = _audit_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row["history_id"] == hist_id
        assert row["user_id"] == "dad"
        assert row["cancelled_by"] == "dad"
        assert row["quest_id"] == 901
        assert row["status_before"] == "approved"
        assert row["exp_earned"] == 10
        assert row["gold_earned"] == 100
        assert row["cascaded"] == 0
        # approved は残高をロールバックした取消である
        assert row["rewards_reverted"] == 1
        assert row["cancelled_at"]

    def test_history_row_is_still_deleted(self, seeded):
        """監査証跡を足しただけで、取消の挙動(物理削除)は変えていない。"""
        service = qs_module.QuestService()
        approval = qs_module.ApprovalService()
        service.process_complete_quest("dad", 901)
        hist_id = _latest_history_id()

        approval.process_cancel_quest("dad", hist_id)

        with get_db_cursor() as cur:
            assert cur.execute(
                "SELECT 1 FROM quest_history WHERE id = ?", (hist_id,)
            ).fetchone() is None
        # 消えた行を指す監査行は残る(FK を張っていないため参照は切れたまま残せる)
        assert _audit_rows()[0]["history_id"] == hist_id


class TestUnapprovedCancelIsRecorded:
    @pytest.mark.parametrize("status", ["pending", "rejected"])
    def test_rewards_reverted_is_zero(self, seeded, status):
        """pending / rejected は報酬が未付与なので残高に触れない(#97)。
        監査行にもその事実を残す。"""
        with get_db_cursor(commit=True) as cur:
            hist_id = _insert_history(cur, "kid", status=status)

        qs_module.ApprovalService().process_cancel_quest("kid", hist_id)

        rows = _audit_rows()
        assert len(rows) == 1
        assert rows[0]["status_before"] == status
        assert rows[0]["rewards_reverted"] == 0
        assert rows[0]["cancelled_by"] == "kid"


class TestRejectedCancelWritesNothing:
    def test_spent_gold_guard_leaves_no_audit_row(self, seeded):
        """#356 のガードで 400 になる取消は「取り消していない」ので、
        監査行も残ってはならない(同一トランザクションなので巻き戻る)。"""
        service = qs_module.QuestService()
        approval = qs_module.ApprovalService()
        with get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE quest_users SET gold = 0 WHERE user_id = 'dad'")
        service.process_complete_quest("dad", 901)
        hist_id = _latest_history_id()
        with get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE quest_users SET gold = 0 WHERE user_id = 'dad'")

        with pytest.raises(HTTPException) as exc:
            approval.process_cancel_quest("dad", hist_id)
        assert exc.value.status_code == 400
        assert _audit_rows() == []

    def test_other_users_history_leaves_no_audit_row(self, seeded):
        """403(他人の履歴)でも監査行は書かれない。"""
        with get_db_cursor(commit=True) as cur:
            hist_id = _insert_history(cur, "kid")

        with pytest.raises(HTTPException) as exc:
            qs_module.ApprovalService().process_cancel_quest("dad", hist_id)
        assert exc.value.status_code == 403
        assert _audit_rows() == []


class TestCoopCascadeIsAttributedToTheRequester:
    def test_partner_row_records_who_actually_cancelled(self, seeded):
        """兄妹連携クエストのカスケード取消では、相方の履歴は
        「別人(要求者)が取り消した」記録になる。所有者と要求者が異なる
        唯一の経路であり、これが分からないと監査証跡の意味が無い。"""
        with get_db_cursor(commit=True) as cur:
            kid_hist = _insert_history(cur, "kid")
            dad_hist = _insert_history(cur, "dad", linked_history_id=kid_hist)
            cur.execute(
                "UPDATE quest_history SET linked_history_id = ? WHERE id = ?",
                (dad_hist, kid_hist),
            )

        qs_module.ApprovalService().process_cancel_quest("dad", dad_hist)

        rows = {r["history_id"]: r for r in _audit_rows()}
        assert set(rows) == {dad_hist, kid_hist}
        assert rows[dad_hist]["cascaded"] == 0
        assert rows[dad_hist]["user_id"] == "dad"
        assert rows[dad_hist]["cancelled_by"] == "dad"
        # 相方側: 所有者は kid だが取り消したのは dad
        assert rows[kid_hist]["cascaded"] == 1
        assert rows[kid_hist]["user_id"] == "kid"
        assert rows[kid_hist]["cancelled_by"] == "dad"
        assert rows[kid_hist]["linked_history_id"] == dad_hist


class TestAppendOnly:
    def test_repeated_cancels_accumulate(self, seeded):
        """追記専用: 過去の取消記録が後の取消で上書き・削除されないこと。"""
        approval = qs_module.ApprovalService()
        for _ in range(3):
            with get_db_cursor(commit=True) as cur:
                hist_id = _insert_history(cur, "dad", status="pending")
            approval.process_cancel_quest("dad", hist_id)
        rows = _audit_rows()
        assert len(rows) == 3
        assert len({r["history_id"] for r in rows}) == 3


class TestRetentionDeliberatelySkipsTheAuditTable:
    def test_audit_table_is_not_a_retention_target(self):
        """監査証跡は保持期間削除の対象外(#733 が quest_history /
        reward_history を対象外にしたのと同じ判断)。対象に足すと、
        「取消で消えた」記録そのものが期限で消えてしまう。"""
        from services import db_retention_service

        targets = {t.table for t in db_retention_service.RETENTION_TARGETS}
        assert "quest_cancellation_audit" not in targets
