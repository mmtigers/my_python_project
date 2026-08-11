# MY_HOME_SYSTEM/tests/test_quest_service_edge_cases.py
"""
services/quest_service.py の未テストだった分岐を補うテスト:
- process_reject_quest の成功パス(親による却下)
- is_within_reset_period の daily/weekly/不明種別/不正日付文字列
- calculate_quest_boost のボーナス計算・上限クランプ
- _apply_boss_damage のボス撃破(is_new_defeat)分岐
- get_all_view_data の対象者限定クエストにおけるボーナス計算分岐
"""
import datetime
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import QuestService, GameSystem


def _seed_user_and_quest(gold_gain=10, exp_gain=20, day_of_week=None):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
            "('dad', 'Dad', 'Warrior', 5, 0, 100)"
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, day_of_week) "
            "VALUES (101, 'DailyQuest', 'daily', ?, ?, ?)",
            (exp_gain, gold_gain, day_of_week),
        )


class TestProcessRejectQuest:
    def test_parent_can_reject_pending_quest(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
                "('daughter', 'Daughter', 'Novice', 1, 0, 0)"
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
                "(101, 'Test', 'daily', 10, 5)"
            )
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('daughter', 101, 'Test', 10, 5, '2026-01-01T00:00:00', 'pending')
            """)
            history_id = cur.lastrowid

        quest_service = QuestService()
        result = quest_service.process_reject_quest("dad", history_id)

        assert result["status"] == "rejected"
        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT * FROM quest_history WHERE id=?", (history_id,)).fetchone()
        assert row is None

    def test_reject_nonexistent_history_returns_404(self, isolated_db):
        quest_service = QuestService()
        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_reject_quest("dad", 999999)
        assert exc_info.value.status_code == 404

    def test_reject_already_processed_history_returns_400(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
                "('daughter', 'Daughter', 'Novice', 1, 0, 0)"
            )
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('daughter', 101, 'Test', 10, 5, '2026-01-01T00:00:00', 'approved')
            """)
            history_id = cur.lastrowid

        quest_service = QuestService()
        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_reject_quest("dad", history_id)
        assert exc_info.value.status_code == 400


class TestIsWithinResetPeriod:
    def setup_method(self):
        self.quest_service = QuestService()

    def test_daily_true_for_today(self):
        today_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
        assert self.quest_service.is_within_reset_period(f"{today_jst}T10:00:00+09:00", "daily") is True

    def test_daily_false_for_yesterday(self):
        yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        assert self.quest_service.is_within_reset_period(f"{yesterday}T10:00:00", "daily") is False

    def test_weekly_true_for_earlier_this_week(self):
        now_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        start_of_week = now_jst.date() - datetime.timedelta(days=now_jst.weekday())
        assert self.quest_service.is_within_reset_period(f"{start_of_week}T00:00:00+09:00", "weekly") is True

    def test_weekly_false_for_last_week(self):
        now_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        start_of_week = now_jst.date() - datetime.timedelta(days=now_jst.weekday())
        last_week = start_of_week - datetime.timedelta(days=1)
        assert self.quest_service.is_within_reset_period(f"{last_week}T00:00:00+09:00", "weekly") is False

    def test_unknown_reset_period_returns_false(self):
        assert self.quest_service.is_within_reset_period("2026-01-01T00:00:00", "monthly") is False

    def test_empty_string_returns_false(self):
        assert self.quest_service.is_within_reset_period("", "daily") is False

    def test_malformed_date_falls_back_to_date_prefix_parsing(self):
        today_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
        assert self.quest_service.is_within_reset_period(f"{today_jst} 10:00:00 garbage", "daily") is True

    def test_completely_unparseable_string_returns_false(self):
        assert self.quest_service.is_within_reset_period("not-a-date-at-all", "daily") is False


class TestCalculateQuestBoost:
    def setup_method(self):
        self.quest_service = QuestService()

    def test_non_daily_quest_type_has_no_boost(self, isolated_db):
        with common.get_db_cursor() as cur:
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
                "(101, 'T', 'infinite', 10, 5)"
            )
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id=101").fetchone()
            boost = self.quest_service.calculate_quest_boost(cur, "dad", quest)
        assert boost == {"gold": 0, "exp": 0}

    def test_day_of_week_limited_quest_has_no_boost(self, isolated_db):
        _seed_user_and_quest(day_of_week="Mon")
        with common.get_db_cursor() as cur:
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id=101").fetchone()
            boost = self.quest_service.calculate_quest_boost(cur, "dad", quest)
        assert boost == {"gold": 0, "exp": 0}

    def test_no_prior_history_has_no_boost(self, isolated_db):
        _seed_user_and_quest()
        with common.get_db_cursor() as cur:
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id=101").fetchone()
            boost = self.quest_service.calculate_quest_boost(cur, "dad", quest)
        assert boost == {"gold": 0, "exp": 0}

    def test_missed_days_grants_proportional_bonus(self, isolated_db):
        _seed_user_and_quest(gold_gain=100, exp_gain=100)
        three_days_ago = (datetime.datetime.now() - datetime.timedelta(days=3)).isoformat()
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('dad', 101, 'DailyQuest', 100, 100, ?, 'approved')
            """, (three_days_ago,))
        with common.get_db_cursor() as cur:
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id=101").fetchone()
            boost = self.quest_service.calculate_quest_boost(cur, "dad", quest)
        # days_diff=3 -> missed_days=2 -> bonus_ratio=0.2
        assert boost == {"gold": 20, "exp": 20}

    def test_bonus_ratio_is_capped_at_one(self, isolated_db):
        _seed_user_and_quest(gold_gain=100, exp_gain=100)
        long_ago = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat()
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('dad', 101, 'DailyQuest', 100, 100, ?, 'approved')
            """, (long_ago,))
        with common.get_db_cursor() as cur:
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id=101").fetchone()
            boost = self.quest_service.calculate_quest_boost(cur, "dad", quest)
        assert boost == {"gold": 100, "exp": 100}


def _this_weeks_monday_str() -> str:
    """
    _apply_boss_damage は内部で _check_and_reset_weekly_boss を呼び、
    week_start_date が「今週の月曜日」と一致しないと自動リセットしてしまうため、
    テストでは過去の固定日付ではなく実行時点の週初めを使う必要がある。
    """
    today = datetime.datetime.now().date()
    monday = today - datetime.timedelta(days=today.weekday())
    return str(monday)


class TestBossDefeatBranch:
    def _seed_low_hp_boss(self):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO party_state (id, current_boss_id, current_hp, max_hp, week_start_date, is_defeated, total_damage, charge_gauge, updated_at)
                VALUES (1, 1, 10, 1000, ?, 0, 0, 0, '2026-01-01T00:00:00')
            """, (_this_weeks_monday_str(),))

    def test_lethal_damage_triggers_new_defeat_and_plays_fanfare(self, isolated_db):
        self._seed_low_hp_boss()
        quest_service = QuestService()
        with patch("services.quest_service.sound_manager.play") as mock_play, \
             common.get_db_cursor(commit=True) as cur:
            result = quest_service._apply_boss_damage(cur, damage=999)

        assert result["isDefeated"] is True
        assert result["isNewDefeat"] is True
        mock_play.assert_called_once_with("boss_defeat_fanfare")

    def test_non_lethal_damage_plays_attack_hit_sound(self, isolated_db):
        self._seed_low_hp_boss()
        quest_service = QuestService()
        with patch("services.quest_service.sound_manager.play") as mock_play, \
             common.get_db_cursor(commit=True) as cur:
            result = quest_service._apply_boss_damage(cur, damage=1)

        assert result["isDefeated"] is False
        assert result["isNewDefeat"] is False
        mock_play.assert_called_once_with("attack_hit")

    def test_already_defeated_boss_ignores_further_damage(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO party_state (id, current_boss_id, current_hp, max_hp, week_start_date, is_defeated, total_damage, charge_gauge, updated_at)
                VALUES (1, 1, 0, 1000, ?, 1, 1000, 0, '2026-01-01T00:00:00')
            """, (_this_weeks_monday_str(),))
        quest_service = QuestService()
        with common.get_db_cursor(commit=True) as cur:
            result = quest_service._apply_boss_damage(cur, damage=50)

        assert result["isDefeated"] is True
        assert result["isNewDefeat"] is False


class TestGetAllViewDataTargetedQuestBoost:
    def test_targeted_quest_includes_bonus_fields(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0)"
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) "
                "VALUES (101, 'Personal Quest', 'daily', 'dad', 10, 5)"
            )

        game_system = GameSystem()
        data = game_system.get_all_view_data()

        targeted = next(q for q in data["quests"] if q["quest_id"] == 101)
        assert "bonus_gold" in targeted
        assert "bonus_exp" in targeted
