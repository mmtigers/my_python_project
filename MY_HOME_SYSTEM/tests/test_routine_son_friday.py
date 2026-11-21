# MY_HOME_SYSTEM/tests/test_routine_son_friday.py
"""智矢の金曜(療育「あこーでぃおん」の日)だけ、夕方の自由時間の締切を19:00にするテスト。

智矢は金曜に療育へ行き17:30過ぎに帰宅するため、平日と同じ17:30締切だと帰宅時点で
寝る準備へ強制遷移済みになり、自由時間(=YouTube券・TV解錠)に一度も入れなかった。
"""
import datetime

from core.database import get_db_cursor
from routine_data import ROUTINE_FLOWS, SON_ROUTINE_FLOWS, get_effective_checkpoint_time
from services.quest.inventory_service import _is_user_in_youtube_free_time
from services.routine_service import routine_service

JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')

PLAIN_FRIDAY = datetime.date(2026, 11, 20)
PLAIN_THURSDAY = datetime.date(2026, 11, 19)
HOLIDAY_FRIDAY = datetime.date(2027, 1, 1)  # 元日(金)
SATURDAY = datetime.date(2026, 11, 21)


def _at(day: datetime.date, hour: int, minute: int) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)


def _seed_user(user_id: str) -> None:
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, 'テスト子', 'Novice', 1, 0, 0, 'role_child'),
        )


def _free_step(flows):
    return next(s for s in flows['pm']['steps'] if s['key'] == 'free')


class TestEffectiveCheckpointTime:
    def test_son_friday_is_1900(self):
        assert get_effective_checkpoint_time(_free_step(SON_ROUTINE_FLOWS), _at(PLAIN_FRIDAY, 14, 0)) == '19:00'

    def test_son_other_weekday_stays_1730(self):
        assert get_effective_checkpoint_time(_free_step(SON_ROUTINE_FLOWS), _at(PLAIN_THURSDAY, 14, 0)) == '17:30'

    def test_son_holiday_friday_uses_weekend_time(self):
        """金曜でも祝日なら休日扱い(18:00)を優先する。"""
        assert get_effective_checkpoint_time(_free_step(SON_ROUTINE_FLOWS), _at(HOLIDAY_FRIDAY, 14, 0)) == '18:00'

    def test_son_saturday_unchanged(self):
        assert get_effective_checkpoint_time(_free_step(SON_ROUTINE_FLOWS), _at(SATURDAY, 14, 0)) == '18:00'

    def test_daughter_friday_unchanged(self):
        """涼花(共通の子ども用フロー)は対象外。"""
        assert get_effective_checkpoint_time(_free_step(ROUTINE_FLOWS), _at(PLAIN_FRIDAY, 14, 0)) == '17:30'

    def test_morning_flow_unchanged_on_friday(self):
        am_free = next(s for s in SON_ROUTINE_FLOWS['am']['steps'] if s['key'] == 'free')
        assert get_effective_checkpoint_time(am_free, _at(PLAIN_FRIDAY, 6, 0)) == '07:50'


class TestSonFridayEvening:
    def test_displayed_checkpoint_is_1900(self, isolated_db):
        _seed_user('son')
        state = routine_service.get_today_state('son', now=_at(PLAIN_FRIDAY, 14, 0))
        assert state['flows']['pm']['checkpoint_time'] == '19:00'

    def test_not_forced_at_1745_and_can_enter_free_time(self, isolated_db):
        """17:45に帰宅しても寝る準備へ飛ばされず、宿題などを終えれば自由時間に入れる。"""
        _seed_user('son')
        routine_service.process_deadlines(now=_at(PLAIN_FRIDAY, 17, 45))
        state = routine_service.get_today_state('son', now=_at(PLAIN_FRIDAY, 17, 45))
        statuses = {s['key']: s['status'] for s in state['flows']['pm']['steps']}
        assert statuses['handwash'] == 'current'
        assert statuses['dinner'] != 'current'  # 寝る準備へは進んでいない

        for key in ('handwash', 'unpack', 'homework'):
            routine_service.complete_step('son', 'pm', key, now=_at(PLAIN_FRIDAY, 17, 50))
        state = routine_service.get_today_state('son', now=_at(PLAIN_FRIDAY, 18, 0))
        assert state['flows']['pm']['in_free_time'] is True
        assert routine_service.is_user_currently_in_free_time('son', now=_at(PLAIN_FRIDAY, 18, 0)) is True
        assert _is_user_in_youtube_free_time('son', now=_at(PLAIN_FRIDAY, 18, 0)) is True

    def test_forced_past_after_1900(self, isolated_db):
        _seed_user('son')
        for key in ('handwash', 'unpack', 'homework'):
            routine_service.complete_step('son', 'pm', key, now=_at(PLAIN_FRIDAY, 17, 50))
        routine_service.process_deadlines(now=_at(PLAIN_FRIDAY, 19, 1))
        state = routine_service.get_today_state('son', now=_at(PLAIN_FRIDAY, 19, 1))
        assert state['flows']['pm']['in_free_time'] is False

    def test_thursday_still_forced_after_1730(self, isolated_db):
        _seed_user('son')
        for key in ('handwash', 'unpack', 'homework'):
            routine_service.complete_step('son', 'pm', key, now=_at(PLAIN_THURSDAY, 16, 0))
        routine_service.process_deadlines(now=_at(PLAIN_THURSDAY, 17, 31))
        state = routine_service.get_today_state('son', now=_at(PLAIN_THURSDAY, 17, 31))
        assert state['flows']['pm']['in_free_time'] is False
