# MY_HOME_SYSTEM/tests/test_balance_lock_cross_path_concurrency.py
"""
Issue #161: quest_users(gold/exp/level)を書き換える経路が
_completion_locks / _user_balance_locks / _purchase_locks という3つの
独立したロックレジストリに分断されており、対象ユーザー単位でのプロセス内
直列化という不変条件(quest_service.py冒頭のコメント参照)に反する並行経路が
残っていた回帰防止テスト。

1. process_complete_quest (大人の即時完了パス) は completion lock
   ((user_id, quest_id)単位)しか取得しないため、同一の大人が異なる
   quest_id をほぼ同時に完了すると別々のロックキーとなり並行実行され、
   quest_users への read-modify-write (_apply_quest_rewards) が競合して
   lost update が起こり得た。
2. process_purchase_reward (アトミック減算) と process_approve_quest
   (絶対値SET) が別ロック系統だったため、承認処理のSELECT後に購入の
   減算が確定すると、承認の絶対値UPDATEが減算を上書きし、購入代金が
   実質返金されてしまう不整合が起こり得た。

test_quest_approve_cancel_concurrency.py / test_purchase_double_tap_concurrency.py
と同様、実際のスレッドを使い、ファイルベースのSQLite(isolated_db)に対して
本物のQuestService/ShopServiceメソッドを並行呼び出しすることで検証する。
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import QuestService, ShopService

N_QUESTS = 12
GOLD_PER_QUEST = 10
EXP_PER_QUEST = 5


def _seed_adult_with_individual_quests(n=N_QUESTS):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult')"
        )
        quest_ids = []
        for i in range(n):
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, target_user) "
                "VALUES (?, ?, 'daily', ?, ?, 'dad')",
                (5000 + i, f"Quest{i}", EXP_PER_QUEST, GOLD_PER_QUEST),
            )
            quest_ids.append(5000 + i)
        return quest_ids


class TestConcurrentAdultCompletionOfDifferentQuests:
    """シナリオ1: completion lock は (user_id, quest_id) 単位なので、
    同一の大人が異なる quest_id をほぼ同時に完了すると別ロックキーとなり
    並行実行される。user balance lock が無ければ quest_users への
    read-modify-write が競合し、gold/expの加算がほとんど消失する。"""

    def test_concurrent_completions_of_different_quests_do_not_lose_balance_updates(self, isolated_db):
        quest_ids = _seed_adult_with_individual_quests()
        quest_service = QuestService()

        with ThreadPoolExecutor(max_workers=N_QUESTS) as pool:
            results = list(pool.map(
                lambda qid: quest_service.process_complete_quest("dad", qid), quest_ids
            ))

        assert all(r["status"] == "success" for r in results)

        with common.get_db_cursor() as cur:
            dad = cur.execute("SELECT gold, exp FROM quest_users WHERE user_id = 'dad'").fetchone()
            approved_count = cur.execute(
                "SELECT COUNT(*) c FROM quest_history WHERE user_id='dad' AND status='approved'"
            ).fetchone()["c"]

        assert approved_count == N_QUESTS
        # レベルアップしない範囲のexp量のため、gold/expはそのまま合計と一致するはず
        assert dad["gold"] == N_QUESTS * GOLD_PER_QUEST
        assert dad["exp"] == N_QUESTS * EXP_PER_QUEST


class TestPurchaseVersusApproveCrossPathConcurrency:
    """シナリオ2: 購入(アトミック減算)と承認(絶対値SET)が別ロック系統だと、
    承認処理のSELECT後に購入の減算が確定した場合、承認の絶対値UPDATEが
    その減算を上書きしてしまい、購入代金が実質返金される。"""

    def test_concurrent_purchase_and_approvals_do_not_clobber_each_others_balance_update(self, isolated_db):
        initial_gold = 1000
        reward_cost = 50
        n_pending = 8
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
                "('son', 'Son', 'Novice', 1, 0, ?, 'role_child')",
                (initial_gold,),
            )
            cur.execute(
                "INSERT INTO reward_master (reward_id, title, cost_gold, target) VALUES "
                "(700, 'Popular Reward', ?, 'all')",
                (reward_cost,),
            )
            history_ids = []
            for i in range(n_pending):
                cur.execute(
                    "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, "
                    "completed_at, status) VALUES ('son', ?, ?, ?, ?, ?, 'pending')",
                    (6000 + i, f"Quest{i}", EXP_PER_QUEST, GOLD_PER_QUEST, common.get_now_iso()),
                )
                history_ids.append(cur.lastrowid)

        quest_service = QuestService()
        shop_service = ShopService()

        tasks = [lambda hid=hid: quest_service.process_approve_quest("dad", hid) for hid in history_ids]
        tasks.append(lambda: shop_service.process_purchase_reward("son", 700))

        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            results = list(pool.map(lambda fn: fn(), tasks))

        approve_results = results[:-1]
        purchase_result = results[-1]

        assert all(r["status"] == "success" for r in approve_results)
        assert purchase_result["status"] == "purchased"

        with common.get_db_cursor() as cur:
            son = cur.execute("SELECT gold FROM quest_users WHERE user_id = 'son'").fetchone()
            approved_count = cur.execute(
                "SELECT COUNT(*) c FROM quest_history WHERE user_id='son' AND status='approved'"
            ).fetchone()["c"]
            purchase_count = cur.execute(
                "SELECT COUNT(*) c FROM reward_history WHERE user_id='son' AND reward_id=700"
            ).fetchone()["c"]

        assert approved_count == n_pending
        assert purchase_count == 1
        assert son["gold"] == initial_gold + n_pending * GOLD_PER_QUEST - reward_cost
