# MY_HOME_SYSTEM/tests/test_routine_holiday.py
"""祝日のすごろく(ルーティン)が土日と同じ「休日」として動くことのテスト。

祝日という概念が無かった頃は `weekday() >= 5` だけで休日を判定していたため、
祝日は完全に平日扱いで「朝の締切が7:50のまま」「休日にだけ出るステップが出ない」
「パパのお仕事ステップが祝日にも出る」といった状態だった。
判定は `core.jp_holidays.is_offday`(土日 + 国民の祝日 + config.EXTRA_HOLIDAY_DATES)
に集約されており、ここではその結果がすごろくの各分岐に効いていることを固定する。

基準日は 2026-09 のシルバーウィーク(19土・20日・21月=敬老の日・22火=国民の休日・
23水=秋分の日)と、祝日でない平日 2026-09-28(月)。
"""
import datetime

import config
import pytest
from core.database import get_db_cursor
from services.routine_service import routine_service

JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')

FRIDAY = datetime.date(2026, 9, 18)           # 平日(連休の直前)
SATURDAY = datetime.date(2026, 9, 19)
SUNDAY = datetime.date(2026, 9, 20)
HOLIDAY_MONDAY = datetime.date(2026, 9, 21)   # 敬老の日
CITIZENS_TUESDAY = datetime.date(2026, 9, 22)  # 国民の休日
PLAIN_MONDAY = datetime.date(2026, 9, 28)     # 祝日でない月曜


def _at(day: datetime.date, hour: int, minute: int) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)


def _seed_user(user_id='daughter', gold=0, exp=0, level=1, role='role_child'):
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, 'テスト子', 'Novice', level, exp, gold, role),
        )


def _statuses(state, flow_key):
    return {s['key']: s['status'] for s in state['flows'][flow_key]['steps']}


class TestHolidayMorningDeadline:
    """朝(am)の締切は平日7:50・休日9:30。祝日は9:30側になる。"""

    def test_checkpoint_time_is_930_on_a_holiday(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(HOLIDAY_MONDAY, 6, 0))
        assert state['flows']['am']['checkpoint_time'] == '09:30'

    def test_checkpoint_time_is_750_on_a_plain_monday(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(PLAIN_MONDAY, 6, 0))
        assert state['flows']['am']['checkpoint_time'] == '07:50'

    def test_not_forced_past_at_800_on_a_holiday(self, isolated_db):
        """平日なら7:50超えで強制通過する8:00でも、祝日は自由時間のまま。"""
        _seed_user(gold=0, exp=0)
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(HOLIDAY_MONDAY, 6, 0))
        state = routine_service.process_deadlines(now=_at(HOLIDAY_MONDAY, 8, 0))
        assert state['transitions'] == 0

        after = routine_service.get_today_state('daughter', now=_at(HOLIDAY_MONDAY, 8, 0))
        assert after['flows']['am']['in_free_time'] is True

    def test_forced_past_at_931_on_a_holiday(self, isolated_db):
        _seed_user(gold=0, exp=0)
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(HOLIDAY_MONDAY, 6, 0))
        routine_service.process_deadlines(now=_at(HOLIDAY_MONDAY, 9, 31))
        state = routine_service.get_today_state('daughter', now=_at(HOLIDAY_MONDAY, 9, 31))
        assert state['flows']['am']['in_free_time'] is False
        assert state['flows']['am']['bonus_gold'] == 150

    def test_citizens_holiday_is_treated_the_same(self, isolated_db):
        """国民の休日(2026-09-22)も休日として扱う。"""
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(CITIZENS_TUESDAY, 6, 0))
        assert state['flows']['am']['checkpoint_time'] == '09:30'


class TestHolidayEveningFlow:
    def test_pm_starts_from_snack_on_a_holiday(self, isolated_db):
        """休日は手洗い・うがい(weekend_skip)を飛ばしておやつ休憩から始まる。"""
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(HOLIDAY_MONDAY, 14, 0))
        statuses = _statuses(state, 'pm')
        assert statuses['handwash'] == 'done'
        assert statuses['snack'] == 'current'
        assert state['flows']['pm']['current_step_index'] == 1

    def test_pm_starts_from_handwash_on_a_plain_monday(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(PLAIN_MONDAY, 14, 0))
        statuses = _statuses(state, 'pm')
        assert statuses['handwash'] == 'current'


class TestHolidayHomeworkCarryover:
    """宿題(weekend_carryover)の遡りが、祝日を含む連休の長さに追従すること。"""

    def test_homework_done_friday_skips_the_whole_three_day_weekend(self, isolated_db):
        _seed_user()
        for key in ('handwash', 'snack', 'homework'):
            routine_service.complete_step('daughter', 'pm', key, now=_at(FRIDAY, 14, 0))

        for day in (SATURDAY, SUNDAY, HOLIDAY_MONDAY):
            state = routine_service.get_today_state('daughter', now=_at(day, 14, 0))
            assert _statuses(state, 'pm')['homework'] == 'done', day

    def test_homework_is_required_again_on_the_next_school_day(self, isolated_db):
        """連休明けの平日(2026-09-24 木)は繰越の対象外で、宿題が必要になる。"""
        _seed_user()
        for key in ('handwash', 'snack', 'homework'):
            routine_service.complete_step('daughter', 'pm', key, now=_at(FRIDAY, 14, 0))

        next_school_day = datetime.date(2026, 9, 24)
        state = routine_service.get_today_state('daughter', now=_at(next_school_day, 14, 0))
        assert _statuses(state, 'pm')['homework'] == 'locked'

    def test_lookback_stops_at_the_last_school_day(self, isolated_db):
        """連休直前の平日(金)で未完了なら、連休中はスキップされない。"""
        _seed_user()
        routine_service.complete_step('daughter', 'pm', 'handwash', now=_at(FRIDAY, 14, 0))
        routine_service.complete_step('daughter', 'pm', 'snack', now=_at(FRIDAY, 14, 5))

        state = routine_service.get_today_state('daughter', now=_at(HOLIDAY_MONDAY, 14, 0))
        assert _statuses(state, 'pm')['homework'] == 'locked'


class TestDadHolidayFlow:
    """パパのpmは平日「お仕事」のみ、休日はキッチン/リビングリセット。"""

    def test_work_step_is_skipped_on_a_holiday(self, isolated_db):
        _seed_user(user_id='dad', role='role_adult')
        state = routine_service.get_today_state('dad', now=_at(HOLIDAY_MONDAY, 14, 0))
        statuses = _statuses(state, 'pm')
        assert statuses['work'] == 'done'         # weekend_skip
        assert statuses['kitchen_reset'] == 'current'
        assert statuses['living_reset'] == 'locked'

    def test_reset_steps_are_skipped_on_a_plain_monday(self, isolated_db):
        _seed_user(user_id='dad', role='role_adult')
        state = routine_service.get_today_state('dad', now=_at(PLAIN_MONDAY, 14, 0))
        statuses = _statuses(state, 'pm')
        assert statuses['work'] == 'current'
        assert statuses['kitchen_reset'] == 'done'  # weekday_skip
        assert statuses['living_reset'] == 'done'

    def test_work_step_cannot_be_completed_on_a_holiday(self, isolated_db):
        """休日はスキップ済みのため、お仕事ステップの完了報告は弾かれる。"""
        from fastapi import HTTPException
        _seed_user(user_id='dad', role='role_adult')
        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('dad', 'pm', 'work', now=_at(HOLIDAY_MONDAY, 14, 0))
        assert exc_info.value.status_code == 409


class TestExtraHolidayDatesAffectRoutine:
    """config.EXTRA_HOLIDAY_DATES(年末年始・お盆など)もすごろくに効く。"""

    TARGET = datetime.date(2026, 12, 29)  # 火曜。祝日ではない

    def test_is_a_plain_weekday_without_the_setting(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(self.TARGET, 6, 0))
        assert state['flows']['am']['checkpoint_time'] == '07:50'
        pm = routine_service.get_today_state('daughter', now=_at(self.TARGET, 14, 0))
        assert _statuses(pm, 'pm')['handwash'] == 'current'

    def test_configured_family_offday_uses_the_weekend_rules(self, isolated_db, monkeypatch):
        _seed_user()
        monkeypatch.setattr(config, "EXTRA_HOLIDAY_DATES", frozenset({self.TARGET}))
        state = routine_service.get_today_state('daughter', now=_at(self.TARGET, 6, 0))
        assert state['flows']['am']['checkpoint_time'] == '09:30'
        pm = routine_service.get_today_state('daughter', now=_at(self.TARGET, 14, 0))
        assert _statuses(pm, 'pm')['handwash'] == 'done'
