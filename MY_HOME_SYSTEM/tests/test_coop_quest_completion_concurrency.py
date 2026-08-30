# MY_HOME_SYSTEM/tests/test_coop_quest_completion_concurrency.py
"""
Issue #96: 兄妹連携クエスト(quest_master.target_user='siblings')の同時完了報告で
pendingペアが二重生成される不具合の回帰防止テスト。

process_complete_quest の完了ロックは元々 (user_id, quest_id) をキーにしていたため、
兄の報告は (son, quest_id)、妹の報告は (daughter, quest_id) と別ロックとなり直列化
されなかった。双方のスレッドが互いのINSERT前にスパムチェックを通過すると、
quest_history に pending 4行(連結ペア2組)が生成され、承認で報酬が2倍になっていた。

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


def _seed_family(cur):
    cur.execute(
        "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
        "('dad', 'Dad', 'Warrior', 1, 0, 100, 'role_adult'), "
        "('son', 'Son', 'Novice', 1, 0, 0, 'role_child'), "
        "('daughter', 'Daughter', 'Novice', 1, 0, 0, 'role_child')"
    )
    cur.execute(
        "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) VALUES "
        "(501, 'いっしょにお片付け', 'daily', 'siblings', 20, 10)"
    )


class TestCoopQuestCompletionConcurrency:
    def test_concurrent_completion_reports_produce_single_pending_pair(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            _seed_family(cur)

        quest_service = QuestService()

        def _report(reporter):
            try:
                return quest_service.process_complete_quest(reporter, 501)
            except Exception as exc:  # スパムチェック等でブロックされた側は例外を投げる
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_report, ["son", "daughter"]))

        successes = [r for r in results if isinstance(r, dict)]
        # どちらか一方だけが完了報告として受理され、もう一方はブロックされること
        assert len(successes) == 1

        with common.get_db_cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM quest_history WHERE quest_id = 501 ORDER BY id"
            ).fetchall()

        # 連結ペアは1組(2行)のみで、二重生成(4行)されていないこと
        assert len(rows) == 2
        by_user = {row["user_id"]: dict(row) for row in rows}
        assert set(by_user.keys()) == {"son", "daughter"}
        assert by_user["son"]["status"] == "pending"
        assert by_user["daughter"]["status"] == "pending"
        assert by_user["son"]["linked_history_id"] == by_user["daughter"]["id"]
        assert by_user["daughter"]["linked_history_id"] == by_user["son"]["id"]
