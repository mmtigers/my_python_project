# MY_HOME_SYSTEM/tests/test_coop_quest_router.py
"""
兄妹連携クエスト(quest_master.target_user='siblings')のTestClient経由のテスト。

どちらか一方の子どもが完了報告すると、子ども2人分の quest_history 行(共に
status='pending')が作成され、互いを linked_history_id で連結する。
承認・却下・取り消しは連結された2行に対してアトミックにカスケードすることを検証する。
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import game_logic


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


@pytest.fixture
def seeded_client(isolated_db, api_client):
    with common.get_db_cursor(commit=True) as cur:
        _seed_family(cur)
    return api_client


def _complete_coop_quest(client, reporter="son"):
    res = client.post("/api/quest/complete", json={"user_id": reporter, "quest_id": 501})
    assert res.status_code == 200
    assert res.json()["status"] == "pending"

    with common.get_db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM quest_history WHERE quest_id = 501 ORDER BY id"
        ).fetchall()
    return {row["user_id"]: dict(row) for row in rows}


class TestCoopQuestCompletion:
    def test_completion_creates_two_linked_pending_rows(self, seeded_client):
        histories = _complete_coop_quest(seeded_client, reporter="son")

        assert set(histories.keys()) == {"son", "daughter"}
        son_hist = histories["son"]
        daughter_hist = histories["daughter"]

        assert son_hist["status"] == "pending"
        assert daughter_hist["status"] == "pending"
        assert son_hist["linked_history_id"] == daughter_hist["id"]
        assert daughter_hist["linked_history_id"] == son_hist["id"]
        # 兄妹とも同額の報酬が記録されていること(分割ではなく両者フル付与)
        assert son_hist["exp_earned"] == daughter_hist["exp_earned"] == 20
        assert son_hist["gold_earned"] == daughter_hist["gold_earned"] == 10

    def test_either_sibling_can_report_completion(self, seeded_client):
        histories = _complete_coop_quest(seeded_client, reporter="daughter")
        assert set(histories.keys()) == {"son", "daughter"}

    def test_completion_with_only_one_child_registered_returns_400(self, isolated_db, api_client):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('son', 'Son', 'Novice', 1, 0, 0, 'role_child')"
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) VALUES "
                "(501, 'いっしょにお片付け', 'daily', 'siblings', 20, 10)"
            )
        res = api_client.post("/api/quest/complete", json={"user_id": "son", "quest_id": 501})
        assert res.status_code == 400


class TestCoopQuestApproval:
    def test_approve_one_side_cascades_rewards_to_both(self, seeded_client):
        histories = _complete_coop_quest(seeded_client, reporter="son")
        son_history_id = histories["son"]["id"]

        res = seeded_client.post(
            "/api/quest/approve", json={"approver_id": "dad", "history_id": son_history_id}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        with common.get_db_cursor() as cur:
            rows = {
                row["user_id"]: dict(row)
                for row in cur.execute(
                    "SELECT * FROM quest_history WHERE quest_id = 501 ORDER BY id"
                ).fetchall()
            }
            son = cur.execute("SELECT * FROM quest_users WHERE user_id='son'").fetchone()
            daughter = cur.execute("SELECT * FROM quest_users WHERE user_id='daughter'").fetchone()

        assert rows["son"]["status"] == "approved"
        assert rows["daughter"]["status"] == "approved"
        # 分割ではなく両者ともフル付与(片方だけ半額等になっていないこと)
        assert son["exp"] == 20
        assert daughter["exp"] == 20
        assert son["gold"] == 10
        assert daughter["gold"] == 10

    def test_approving_already_cascaded_partner_row_returns_400(self, seeded_client):
        histories = _complete_coop_quest(seeded_client, reporter="son")
        son_history_id = histories["son"]["id"]
        daughter_history_id = histories["daughter"]["id"]

        first = seeded_client.post(
            "/api/quest/approve", json={"approver_id": "dad", "history_id": son_history_id}
        )
        assert first.status_code == 200

        # 相方側の行は既にカスケードで承認済みのため、個別に承認しようとすると400
        second = seeded_client.post(
            "/api/quest/approve", json={"approver_id": "dad", "history_id": daughter_history_id}
        )
        assert second.status_code == 400

    def test_non_parent_cannot_approve_coop_quest(self, seeded_client):
        histories = _complete_coop_quest(seeded_client, reporter="son")
        res = seeded_client.post(
            "/api/quest/approve", json={"approver_id": "daughter", "history_id": histories["son"]["id"]}
        )
        assert res.status_code == 403


class TestCoopQuestApprovalPartnerRewardInfo:
    """Issue #238の回帰テスト: カスケード承認されるパートナー(自分でタップしなかった
    方の子ども)側のgold/exp/level/medalはDB上正しく付与されるものの、以前は
    承認APIのレスポンスに一切含まれず、フロント側がパートナーのレベルアップ/
    メダル獲得演出を出す手段が無かった。レスポンスにpartnerUserId/partnerLeveledUp/
    partnerNewLevel/partnerEarnedMedalsが含まれることを検証する。"""

    def test_response_includes_partner_level_up_info(self, seeded_client, monkeypatch):
        # daughterのexpを事前に積んでおき、今回の20exp加算で確実にレベルアップさせる
        # (calculate_next_level_exp(1) == 100 のため、95+20=115 >= 100 でレベルアップ確定)
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE quest_users SET exp = 95 WHERE user_id = 'daughter'")
        # メダルドロップ(5%)を無効化して結果を決定的にする
        monkeypatch.setattr(game_logic.random, "random", lambda: 0.99)

        histories = _complete_coop_quest(seeded_client, reporter="son")
        son_history_id = histories["son"]["id"]

        res = seeded_client.post(
            "/api/quest/approve", json={"approver_id": "dad", "history_id": son_history_id}
        )
        assert res.status_code == 200
        body = res.json()

        assert body["partnerUserId"] == "daughter"
        assert body["partnerLeveledUp"] is True
        assert body["partnerNewLevel"] == 2
        assert body["partnerEarnedMedals"] == 0

        with common.get_db_cursor() as cur:
            daughter = cur.execute("SELECT * FROM quest_users WHERE user_id='daughter'").fetchone()
        assert daughter["level"] == 2

    def test_response_includes_partner_earned_medals(self, seeded_client, monkeypatch):
        # メダルドロップ(5%)を強制的に発生させる
        monkeypatch.setattr(game_logic.random, "random", lambda: 0.0)

        histories = _complete_coop_quest(seeded_client, reporter="son")
        son_history_id = histories["son"]["id"]

        res = seeded_client.post(
            "/api/quest/approve", json={"approver_id": "dad", "history_id": son_history_id}
        )
        assert res.status_code == 200
        body = res.json()

        assert body["partnerUserId"] == "daughter"
        assert body["partnerEarnedMedals"] == 1

    def test_response_has_no_partner_fields_for_non_coop_quest(self, isolated_db, api_client):
        """連携クエストでない(linked_history_idがNULLの)通常の承認では、
        partner系フィールドは既定値のままであること。"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 100, 'role_adult'), "
                "('son', 'Son', 'Novice', 1, 0, 0, 'role_child')"
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) VALUES "
                "(601, 'おてつだい', 'daily', 'son', 20, 10)"
            )
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES ('son', 601, 'おてつだい', 20, 10, datetime('now'), 'pending')"
            )
            history_id = cur.lastrowid

        approve_res = api_client.post(
            "/api/quest/approve", json={"approver_id": "dad", "history_id": history_id}
        )
        assert approve_res.status_code == 200
        body = approve_res.json()
        assert body["partnerUserId"] is None
        assert body["partnerLeveledUp"] is False
        assert body["partnerNewLevel"] is None
        assert body["partnerEarnedMedals"] == 0


class TestCoopQuestRejection:
    def test_reject_one_side_marks_both_rows_rejected(self, seeded_client):
        histories = _complete_coop_quest(seeded_client, reporter="son")
        son_history_id = histories["son"]["id"]
        daughter_history_id = histories["daughter"]["id"]

        res = seeded_client.post(
            "/api/quest/reject", json={"approver_id": "dad", "history_id": son_history_id}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "rejected"

        # 却下しても行は削除されず、双方とも status='rejected' として残ること
        with common.get_db_cursor() as cur:
            remaining = {
                row["id"]: row["status"]
                for row in cur.execute(
                    "SELECT id, status FROM quest_history WHERE id IN (?, ?)",
                    (son_history_id, daughter_history_id),
                ).fetchall()
            }
        assert remaining == {son_history_id: "rejected", daughter_history_id: "rejected"}

        with common.get_db_cursor() as cur:
            son = cur.execute("SELECT * FROM quest_users WHERE user_id='son'").fetchone()
            daughter = cur.execute("SELECT * FROM quest_users WHERE user_id='daughter'").fetchone()
        # 却下なので報酬は付与されていないこと
        assert son["exp"] == 0
        assert daughter["exp"] == 0


class TestCoopQuestCancellation:
    def test_cancel_by_reporter_removes_both_pending_rows(self, seeded_client):
        histories = _complete_coop_quest(seeded_client, reporter="daughter")
        son_history_id = histories["son"]["id"]
        daughter_history_id = histories["daughter"]["id"]

        res = seeded_client.post(
            "/api/quest/quest/cancel", json={"user_id": "daughter", "history_id": daughter_history_id}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"

        with common.get_db_cursor() as cur:
            remaining = cur.execute(
                "SELECT id FROM quest_history WHERE id IN (?, ?)",
                (son_history_id, daughter_history_id),
            ).fetchall()
        assert remaining == []
