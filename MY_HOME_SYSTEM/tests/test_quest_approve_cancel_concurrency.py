# MY_HOME_SYSTEM/tests/test_quest_approve_cancel_concurrency.py
"""
H-3: process_approve_quest / process_cancel_quest の並行実行における
gold/exp更新の消失(lost update)レースの回帰防止テスト。

process_approve_quest は「quest_usersをSELECT→Pythonでgold/exp/levelを
計算→UPDATE」というread-modify-write。親が承認一覧を連続タップする
(フロントのhandleApproveAll)と、同一子ユーザーに対する複数のpending
履歴への承認リクエストがスレッドプールでほぼ同時に実行され得る。
ロックが無ければ、後勝ち(last-write-wins)で片方の加算が消失する。

実際のスレッドを使い、ファイルベースのSQLite(isolated_db)に対して
本物のQuestServiceメソッドを並行呼び出しすることで検証する。
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import QuestService

N_PENDING = 12
GOLD_PER_QUEST = 10
EXP_PER_QUEST = 5


def _seed_adult_and_child_with_pending_history(n=N_PENDING):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
            "('son', 'Son', 'Novice', 1, 0, 0, 'role_child')"
        )
        history_ids = []
        for i in range(n):
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, "
                "completed_at, status) VALUES ('son', ?, ?, ?, ?, ?, 'pending')",
                (2000 + i, f"Quest{i}", EXP_PER_QUEST, GOLD_PER_QUEST, common.get_now_iso()),
            )
            history_ids.append(cur.lastrowid)
        return history_ids


class TestConcurrentApprove:
    def test_concurrent_approvals_do_not_lose_gold_updates(self, isolated_db):
        history_ids = _seed_adult_and_child_with_pending_history()
        quest_service = QuestService()

        with ThreadPoolExecutor(max_workers=N_PENDING) as pool:
            results = list(pool.map(
                lambda hid: quest_service.process_approve_quest("dad", hid), history_ids
            ))

        assert all(r["status"] == "success" for r in results)

        with common.get_db_cursor() as cur:
            son = cur.execute("SELECT gold FROM quest_users WHERE user_id = 'son'").fetchone()
            approved_count = cur.execute(
                "SELECT COUNT(*) c FROM quest_history WHERE user_id='son' AND status='approved'"
            ).fetchone()["c"]

        assert approved_count == N_PENDING
        assert son["gold"] == N_PENDING * GOLD_PER_QUEST


class TestConcurrentCancel:
    def test_concurrent_cancels_of_approved_history_do_not_lose_gold_rollback(self, isolated_db):
        """承認済み(gold付与済み)の履歴を並行して取り消した場合も、
        gold のロールバック(減算)が正しく全件反映されること。"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('son', 'Son', 'Novice', 5, 500, 1000, 'role_child')"
            )
            history_ids = []
            for i in range(N_PENDING):
                cur.execute(
                    "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, "
                    "completed_at, status) VALUES ('son', ?, ?, ?, ?, ?, 'approved')",
                    (3000 + i, f"Quest{i}", EXP_PER_QUEST, GOLD_PER_QUEST, common.get_now_iso()),
                )
                history_ids.append(cur.lastrowid)

        quest_service = QuestService()

        with ThreadPoolExecutor(max_workers=N_PENDING) as pool:
            results = list(pool.map(
                lambda hid: quest_service.process_cancel_quest("son", hid), history_ids
            ))

        assert all(r["status"] == "cancelled" for r in results)

        with common.get_db_cursor() as cur:
            son = cur.execute("SELECT gold FROM quest_users WHERE user_id = 'son'").fetchone()
            remaining = cur.execute(
                "SELECT COUNT(*) c FROM quest_history WHERE user_id='son'"
            ).fetchone()["c"]

        assert remaining == 0
        assert son["gold"] == 1000 - N_PENDING * GOLD_PER_QUEST
