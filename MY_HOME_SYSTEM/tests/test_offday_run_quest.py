"""reset_period='offday_run'(「土日の宿題」id=1023)の表示・完了判定のテスト。

要件: 休日(土日・祝日・家の休み)と「翌日が休日の登校日」(金曜・祝前日)だけ表示し、
完了するまで休日区間の間は出し続ける。次の登校日には出さない。完了済みかどうかは
週(月曜起点)ではなく「休日区間に入る前の登校日」以降の履歴で判定する。
"""
import datetime

import config
import pytest
from core.jp_holidays import offday_run_anchor
from freezegun import freeze_time
from services.quest.quest_service import QuestService

service = QuestService()

QUEST = {
    'quest_id': 1023, 'quest_type': 'daily', 'start_time': None, 'end_time': None,
    'day_of_week': None, 'reset_period': 'offday_run', 'occurrence_chance': 1.0,
}

# 2026-09: 21(月)敬老の日・22(火)国民の休日・23(水)秋分の日 の5連休(19土〜23水)
THU = datetime.date(2026, 9, 17)
FRI = datetime.date(2026, 9, 18)
SAT = datetime.date(2026, 9, 19)
SUN = datetime.date(2026, 9, 20)
HOLIDAY_MON = datetime.date(2026, 9, 21)
NEXT_SCHOOL_DAY = datetime.date(2026, 9, 24)
PLAIN_MON = datetime.date(2026, 9, 28)
PLAIN_TUE = datetime.date(2026, 9, 29)


def _active(day: datetime.date) -> bool:
    now = datetime.datetime(day.year, day.month, day.day, 10, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    return service._is_quest_currently_active(QUEST, now)


def _done(completed: datetime.date, today: datetime.date) -> bool:
    with freeze_time(f"{today.isoformat()}T10:00:00+09:00"):
        return service.is_within_reset_period(f"{completed.isoformat()}T10:00:00+09:00", 'offday_run')


class TestVisibility:
    def test_hidden_on_plain_weekdays(self):
        # 平日のうち翌日も平日の日(月〜木)は完全に非表示。
        assert not _active(PLAIN_MON)
        assert not _active(PLAIN_TUE)
        assert not _active(THU)  # 翌日(金)は登校日

    def test_shown_on_friday_and_weekend(self):
        assert _active(FRI)
        assert _active(SAT)
        assert _active(SUN)

    def test_shown_through_a_holiday_run_and_hidden_on_the_next_school_day(self):
        assert _active(HOLIDAY_MON)
        assert _active(datetime.date(2026, 9, 23))
        assert not _active(NEXT_SCHOOL_DAY)

    def test_shown_on_the_eve_of_a_weekday_holiday(self, monkeypatch):
        """祝前日(翌日が平日の祝日・家の休み)は金曜でなくても表示する。"""
        eve = datetime.date(2026, 12, 28)  # 月曜
        monkeypatch.setattr(config, "EXTRA_HOLIDAY_DATES", frozenset({datetime.date(2026, 12, 29)}))
        assert _active(eve)
        assert not _active(datetime.date(2026, 12, 22))  # 火曜。翌日(水)も登校日


class TestAnchor:
    def test_school_day_is_its_own_anchor(self):
        assert offday_run_anchor(FRI) == FRI
        assert offday_run_anchor(PLAIN_MON) == PLAIN_MON

    def test_offday_run_points_to_the_last_school_day_before_it(self):
        assert offday_run_anchor(SAT) == FRI
        assert offday_run_anchor(SUN) == FRI
        assert offday_run_anchor(HOLIDAY_MON) == FRI

    def test_lookback_is_capped(self, monkeypatch):
        days = frozenset(datetime.date(2026, 12, 20) + datetime.timedelta(days=i) for i in range(20))
        monkeypatch.setattr(config, "EXTRA_HOLIDAY_DATES", days)
        target = datetime.date(2026, 12, 31)
        assert (target - offday_run_anchor(target)).days == 7


class TestCompletion:
    def test_done_on_friday_stays_done_through_the_run(self):
        for today in (FRI, SAT, SUN, HOLIDAY_MON):
            assert _done(FRI, today), today

    def test_done_on_saturday_is_still_done_on_a_monday_holiday(self):
        """週(月曜起点)判定だと月曜祝日に未完了へ戻り、再表示・二重報酬になっていた回帰。"""
        assert _done(SAT, HOLIDAY_MON)

    def test_not_done_when_completed_before_the_run_started(self):
        assert not _done(THU, SAT)
        assert not _done(THU, FRI)  # 木曜の完了は金曜の宿題には数えない

    def test_not_done_without_history(self):
        with freeze_time("2026-09-19T10:00:00+09:00"):
            assert service.is_within_reset_period("", 'offday_run') is False


@pytest.mark.parametrize("reset_period", ['daily', 'weekly', 'monthly', None])
def test_other_reset_periods_are_not_gated_by_offday(reset_period):
    quest = dict(QUEST, reset_period=reset_period)
    now = datetime.datetime(2026, 9, 28, 10, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    assert service._is_quest_currently_active(quest, now) is True
