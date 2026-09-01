# MY_HOME_SYSTEM/tests/test_quest_completion_server_side_validation.py
"""
Issue #163: process_complete_quest(クエスト完了API)に対象者・スケジュールの
サーバー側検証が無く、API直叩きで以下がバイパスできていた不具合の回帰防止テスト。

- 対象者制限(target_user)を無視して他人向けクエストを完了できる
- 時間帯外(start_time/end_time)でも完了できる
- 曜日外(day_of_week)でも完了できる
- target_user='siblings'(兄妹連携クエスト)を大人が完了すると、
  _process_coop_quest_completionを経由せず単独即時報酬になってしまう

報酬購入側は Issue #95 でサーバー側の対象者チェックが追加済みだったが、完了側には
未展開のまま残っていた。修正では filter_active_quests(表示用フィルタ)が使う
出現条件判定を _is_quest_currently_active として切り出し、process_complete_quest側の
検証にも共用することで、表示と完了可否の基準を一致させている。
"""
import datetime
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

import common
from services.quest_service import QuestService


def _seed_adult(cur, user_id="dad"):
    cur.execute(
        "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
        "(?, ?, 'Warrior', 1, 0, 0, 'role_adult')", (user_id, user_id.capitalize()),
    )


def _seed_two_children(cur):
    cur.execute(
        "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
        "('son', 'Son', 'Novice', 1, 0, 0, 'role_child'), "
        "('daughter', 'Daughter', 'Novice', 1, 0, 0, 'role_child')"
    )


class TestQuestCompletionTargetUserValidation:
    def test_completing_other_users_targeted_quest_is_rejected(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            _seed_adult(cur, "dad")
            _seed_adult(cur, "mom")
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, target_user) "
                "VALUES (101, 'Mom Only Quest', 'daily', 50, 20, 'mom')"
            )

        quest_service = QuestService()
        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_complete_quest("dad", 101)
        assert exc_info.value.status_code == 403

        with common.get_db_cursor() as cur:
            count = cur.execute("SELECT COUNT(*) c FROM quest_history").fetchone()["c"]
        assert count == 0, "拒否されたクエスト完了は履歴を残さないこと"

    def test_completing_own_targeted_quest_succeeds(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            _seed_adult(cur, "dad")
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, target_user) "
                "VALUES (101, 'Dad Only Quest', 'daily', 50, 20, 'dad')"
            )

        quest_service = QuestService()
        result = quest_service.process_complete_quest("dad", 101)
        assert result["status"] == "success"

    def test_completing_all_targeted_quest_succeeds(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            _seed_adult(cur, "dad")
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, target_user) "
                "VALUES (101, 'Anyone Quest', 'daily', 50, 20, 'all')"
            )

        quest_service = QuestService()
        result = quest_service.process_complete_quest("dad", 101)
        assert result["status"] == "success"


class TestSiblingTargetedQuestRequiresChildRole:
    def test_adult_completing_sibling_targeted_quest_is_rejected(self, isolated_db):
        """target_user='siblings'は兄妹連携クエストの前提(_process_coop_quest_completion
        で2人分のpending行を作成)を持つため、role_adultが完了すると、その前提を
        通らず単独即時報酬になってしまっていた(修正前)。"""
        with common.get_db_cursor(commit=True) as cur:
            _seed_adult(cur, "dad")
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, target_user) "
                "VALUES (101, 'Siblings Quest', 'daily', 50, 20, 'siblings')"
            )

        quest_service = QuestService()
        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_complete_quest("dad", 101)
        assert exc_info.value.status_code == 403

        with common.get_db_cursor() as cur:
            count = cur.execute("SELECT COUNT(*) c FROM quest_history").fetchone()["c"]
        assert count == 0

    def test_child_completing_sibling_targeted_quest_still_succeeds(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            _seed_two_children(cur)
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, target_user) "
                "VALUES (101, 'Siblings Quest', 'daily', 50, 20, 'siblings')"
            )

        quest_service = QuestService()
        result = quest_service.process_complete_quest("son", 101)
        assert result["status"] == "pending"

        with common.get_db_cursor() as cur:
            rows = cur.execute("SELECT user_id FROM quest_history").fetchall()
        assert {r["user_id"] for r in rows} == {"son", "daughter"}


class TestQuestCompletionTimeWindowValidation:
    def test_completing_outside_time_window_is_rejected(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            _seed_adult(cur, "dad")
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, "
                "start_time, end_time) VALUES (101, 'Afternoon Quest', 'daily', 50, 20, '14:00', '16:00')"
            )

        quest_service = QuestService()
        # JST 09:00 (= UTC 00:00、tz_offset=0で日付跨ぎなし) は 14:00-16:00 の時間帯外
        with freeze_time("2026-08-24 00:00:00", tz_offset=0):
            with pytest.raises(HTTPException) as exc_info:
                quest_service.process_complete_quest("dad", 101)
        assert exc_info.value.status_code == 403

    def test_completing_within_time_window_succeeds(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            _seed_adult(cur, "dad")
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, "
                "start_time, end_time) VALUES (101, 'Afternoon Quest', 'daily', 50, 20, '14:00', '16:00')"
            )

        quest_service = QuestService()
        # JST 15:00 (= UTC 06:00、tz_offset=0で日付跨ぎなし) は 14:00-16:00 の時間帯内
        with freeze_time("2026-08-24 06:00:00", tz_offset=0):
            result = quest_service.process_complete_quest("dad", 101)
        assert result["status"] == "success"


class TestQuestCompletionDayOfWeekValidation:
    def test_completing_on_wrong_day_of_week_is_rejected(self, isolated_db):
        anchor_weekday = datetime.date(2026, 8, 24).weekday()
        wrong_weekday = (anchor_weekday + 1) % 7
        with common.get_db_cursor(commit=True) as cur:
            _seed_adult(cur, "dad")
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, day_of_week) "
                "VALUES (101, 'Weekday Quest', 'daily', 50, 20, ?)", (str(wrong_weekday),)
            )

        quest_service = QuestService()
        with freeze_time("2026-08-24 12:00:00", tz_offset=0):
            with pytest.raises(HTTPException) as exc_info:
                quest_service.process_complete_quest("dad", 101)
        assert exc_info.value.status_code == 403

    def test_completing_on_matching_day_of_week_succeeds(self, isolated_db):
        anchor_weekday = datetime.date(2026, 8, 24).weekday()
        with common.get_db_cursor(commit=True) as cur:
            _seed_adult(cur, "dad")
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, day_of_week) "
                "VALUES (101, 'Weekday Quest', 'daily', 50, 20, ?)", (str(anchor_weekday),)
            )

        quest_service = QuestService()
        with freeze_time("2026-08-24 12:00:00", tz_offset=0):
            result = quest_service.process_complete_quest("dad", 101)
        assert result["status"] == "success"
