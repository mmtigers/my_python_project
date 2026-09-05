# MY_HOME_SYSTEM/tests/test_quest_role_unknown_defaults_to_child.py
"""
Issue #370 (Q-M2)の回帰テスト。

process_complete_quest は以前 `if user['role'] == ROLE_CHILD:` の else を
「大人扱い(即時報酬)」としていたため、role が NULL/未知(migration 0001対象外の
user_idや、MasterUser.role=Noneで同期された行)のユーザーは承認ゲート無しで
即時にゴールド・経験値を得られていた。承認(role == ROLE_ADULT必須)・購入
(is_adult = role == ROLE_ADULT)は元々「不明=子ども」側に倒れる判定だったため、
完了処理だけが逆だった。オーナー判断により、完了処理も「role == ROLE_ADULT の
ときだけ即時、それ以外(role_child・NULL・未知の文字列いずれも)はpending」に
統一した。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import QuestService, ROLE_ADULT, ROLE_CHILD


def _seed_user_and_quest(role):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES ('u1', 'U1', 'Novice', 1, 0, 0, ?)",
            (role,),
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, target_user) "
            "VALUES (9101, 'テスト', 'daily', 10, 5, 'all')"
        )


def test_null_role_goes_to_pending_not_immediate_reward(isolated_db):
    """回帰対象のバグそのもの: role が NULL のユーザーは、以前は承認ゲート無しで
    即時報酬を得られていた。"""
    _seed_user_and_quest(role=None)
    quest_service = QuestService()

    result = quest_service.process_complete_quest("u1", 9101)

    assert result["status"] == "pending"
    assert result["earnedGold"] == 0
    assert result["earnedExp"] == 0

    with common.get_db_cursor() as cur:
        user = cur.execute("SELECT gold, exp FROM quest_users WHERE user_id = 'u1'").fetchone()
    assert user["gold"] == 0
    assert user["exp"] == 0


def test_unknown_role_string_also_goes_to_pending(isolated_db):
    """role_adult/role_child のいずれでもない未知の文字列も、安全側(子ども扱い)にpendingとなること。"""
    _seed_user_and_quest(role="some_future_role")
    quest_service = QuestService()

    result = quest_service.process_complete_quest("u1", 9101)

    assert result["status"] == "pending"


def test_role_adult_still_gets_immediate_reward(isolated_db):
    """role_adult は従来どおり即時報酬(regression: 今回の修正で大人側を壊していないこと)。"""
    _seed_user_and_quest(role=ROLE_ADULT)
    quest_service = QuestService()

    result = quest_service.process_complete_quest("u1", 9101)

    assert result["status"] == "success"
    assert result["earnedGold"] == 5
    assert result["earnedExp"] == 10


def test_role_child_still_goes_to_pending(isolated_db):
    """role_child は従来どおりpending(regression: 今回の修正で子ども側を壊していないこと)。"""
    _seed_user_and_quest(role=ROLE_CHILD)
    quest_service = QuestService()

    result = quest_service.process_complete_quest("u1", 9101)

    assert result["status"] == "pending"
