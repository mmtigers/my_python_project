# MY_HOME_SYSTEM/tests/test_coop_approve_cascade_balance_lock_concurrency.py
"""
Issue #98: 兄妹連携クエストの承認カスケードで、相方の quest_users 更新が相方の
ユーザーロック外で行われるため、相方を対象とする別の承認と並行実行すると
lost update(更新の消失)が起こりうる不具合の回帰防止テスト。

process_approve_quest は「報告者(呼び出し元history_idの本来の完了者)のロック」
のみを取得していたが、承認カスケード(_approve_linked_history)は連結された
相方の quest_users(gold/exp/level)も同一トランザクション内でread-modify-write
する。相方を対象とする別の(連携クエストとは無関係な)承認操作が並行実行される
と、相方側のロックが取得されていないため一方の更新が消失しうる。

test_quest_approve_cancel_concurrency.py と同様、実際のスレッドを使い、
ファイルベースのSQLite(isolated_db)に対して本物のQuestServiceメソッドを
並行呼び出しすることで検証する。
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import QuestService

N_SOLO_PENDING = 11
GOLD_PER_QUEST = 10
EXP_PER_QUEST = 5
COOP_GOLD = 20
COOP_EXP = 15


def _seed_family_with_pending_history(cur):
    cur.execute(
        "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
        "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
        "('son', 'Son', 'Novice', 1, 0, 0, 'role_child'), "
        "('daughter', 'Daughter', 'Novice', 1, 0, 0, 'role_child')"
    )
    cur.execute(
        "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) VALUES "
        "(501, 'いっしょにお片付け', 'daily', 'siblings', ?, ?)",
        (COOP_EXP, COOP_GOLD),
    )

    daughter_solo_history_ids = []
    for i in range(N_SOLO_PENDING):
        cur.execute(
            "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, "
            "completed_at, status) VALUES ('daughter', ?, ?, ?, ?, ?, 'pending')",
            (9000 + i, f"SoloQuest{i}", EXP_PER_QUEST, GOLD_PER_QUEST, common.get_now_iso()),
        )
        daughter_solo_history_ids.append(cur.lastrowid)
    return daughter_solo_history_ids


class TestCoopApproveCascadeBalanceLock:
    def test_concurrent_approvals_do_not_lose_partner_balance_updates(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            daughter_solo_history_ids = _seed_family_with_pending_history(cur)

        quest_service = QuestService()

        # 兄妹連携クエストのpendingペアを作成(息子が報告すると、娘側にも
        # 連結されたpending行が自動生成される)
        quest_service.process_complete_quest("son", 501)
        with common.get_db_cursor() as cur:
            son_coop_hist = cur.execute(
                "SELECT id FROM quest_history WHERE user_id = 'son' AND quest_id = 501"
            ).fetchone()
        son_coop_history_id = son_coop_hist["id"]

        # 娘の単独pendingクエストN件と、兄妹連携クエスト(承認すると娘側は
        # カスケードで承認される)を全て並行して承認する。
        all_targets = daughter_solo_history_ids + [son_coop_history_id]

        with ThreadPoolExecutor(max_workers=len(all_targets)) as pool:
            results = list(pool.map(
                lambda hid: quest_service.process_approve_quest("dad", hid), all_targets
            ))

        assert all(r["status"] == "success" for r in results)

        with common.get_db_cursor() as cur:
            daughter = cur.execute("SELECT gold, exp FROM quest_users WHERE user_id = 'daughter'").fetchone()
            approved_count = cur.execute(
                "SELECT COUNT(*) c FROM quest_history WHERE user_id='daughter' AND status='approved'"
            ).fetchone()["c"]

        expected_gold = N_SOLO_PENDING * GOLD_PER_QUEST + COOP_GOLD
        expected_exp = N_SOLO_PENDING * EXP_PER_QUEST + COOP_EXP
        # 娘の分の承認件数(単独N件 + 兄妹連携クエストのカスケード分1件)がすべて
        # 反映され、どの更新も失われていないこと
        assert approved_count == N_SOLO_PENDING + 1
        assert daughter["gold"] == expected_gold
        assert daughter["exp"] == expected_exp
