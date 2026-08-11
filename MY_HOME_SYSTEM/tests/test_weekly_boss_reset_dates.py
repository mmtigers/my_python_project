# MY_HOME_SYSTEM/tests/test_weekly_boss_reset_dates.py
"""
services/quest_service.py の _check_and_reset_weekly_boss (週次ボスリセット判定)の
日付境界テスト。現在時刻を freezegun で固定することで、実行タイミングに依存せず
再現可能なテストにする。

対象ロジック: 「今週の月曜日」を week_start_date と比較し、異なれば週替わりとして
ボスをリセットする。日曜日から月曜日への切り替わり、年末年始、うるう年をまたぐ
週境界で計算式が壊れていないかを確認する。
"""
import os
import sys

import pytest
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import QuestService


def _seed_party_state(week_start_date: str, current_boss_id: int = 1, current_hp: int = 300):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO party_state (id, current_boss_id, current_hp, max_hp, week_start_date, is_defeated, total_damage, charge_gauge, updated_at)
            VALUES (1, ?, ?, 1000, ?, 0, 500, 3, '2026-01-01T00:00:00')
        """, (current_boss_id, current_hp, week_start_date))


def _get_party_state():
    with common.get_db_cursor() as cur:
        row = cur.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
    return dict(row)


class TestSundayToMondayBoundary:
    def test_sunday_23_59_59_same_week_does_not_reset(self, isolated_db):
        _seed_party_state(week_start_date="2026-08-10", current_hp=300)  # 2026-08-10は月曜日
        quest_service = QuestService()

        with freeze_time("2026-08-16 23:59:59"):  # 同じ週の日曜日23:59:59
            with common.get_db_cursor(commit=True) as cur:
                quest_service._check_and_reset_weekly_boss(cur)

        state = _get_party_state()
        assert state["week_start_date"] == "2026-08-10"
        assert state["current_hp"] == 300  # リセットされていない

    def test_monday_00_00_01_new_week_resets_boss(self, isolated_db):
        _seed_party_state(week_start_date="2026-08-10", current_boss_id=1, current_hp=300)
        quest_service = QuestService()

        with freeze_time("2026-08-17 00:00:01"):  # 翌週月曜日の直後
            with common.get_db_cursor(commit=True) as cur:
                quest_service._check_and_reset_weekly_boss(cur)

        state = _get_party_state()
        assert state["week_start_date"] == "2026-08-17"
        assert state["current_hp"] == 1000  # リセットされた
        assert state["current_boss_id"] == 2  # 次のボスへ進む
        assert state["is_defeated"] == 0
        assert state["total_damage"] == 0


class TestBossIdCycling:
    def test_boss_id_wraps_around_after_last_boss(self, isolated_db):
        """ボスリストの最後まで到達したら1体目に戻ること"""
        import quest_data
        total_bosses = len(quest_data.BOSSES)

        _seed_party_state(week_start_date="2026-08-10", current_boss_id=total_bosses, current_hp=1)
        quest_service = QuestService()

        with freeze_time("2026-08-17 00:00:01"):
            with common.get_db_cursor(commit=True) as cur:
                quest_service._check_and_reset_weekly_boss(cur)

        state = _get_party_state()
        assert state["current_boss_id"] == 1


class TestYearBoundary:
    def test_new_years_eve_and_new_years_day_are_same_week(self, isolated_db):
        """2026-12-31(木)と2027-01-01(金)は同じ週(月曜起算 2026-12-28)であり、リセットされない"""
        _seed_party_state(week_start_date="2026-12-28", current_hp=400)
        quest_service = QuestService()

        with freeze_time("2027-01-01 12:00:00"):
            with common.get_db_cursor(commit=True) as cur:
                quest_service._check_and_reset_weekly_boss(cur)

        state = _get_party_state()
        assert state["week_start_date"] == "2026-12-28"
        assert state["current_hp"] == 400

    def test_first_monday_of_new_year_resets(self, isolated_db):
        _seed_party_state(week_start_date="2026-12-28", current_hp=400)
        quest_service = QuestService()

        with freeze_time("2027-01-04 09:00:00"):  # 年明け最初の月曜日
            with common.get_db_cursor(commit=True) as cur:
                quest_service._check_and_reset_weekly_boss(cur)

        state = _get_party_state()
        assert state["week_start_date"] == "2027-01-04"
        assert state["current_hp"] == 1000


class TestLeapDay:
    def test_leap_day_feb_29_does_not_crash_week_calculation(self, isolated_db):
        """2028-02-29(火, うるう日)の週計算が例外なく行われ、月曜起算日が正しく2028-02-28になること"""
        _seed_party_state(week_start_date="2028-02-21", current_hp=200)
        quest_service = QuestService()

        with freeze_time("2028-02-29 10:00:00"):
            with common.get_db_cursor(commit=True) as cur:
                quest_service._check_and_reset_weekly_boss(cur)

        state = _get_party_state()
        assert state["week_start_date"] == "2028-02-28"
        assert state["current_hp"] == 1000
