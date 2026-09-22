# MY_HOME_SYSTEM/tests/test_jp_holidays.py
"""core/jp_holidays.py と、祝日を「休日」として扱う各判定のテスト。

これまでファミクエには祝日という概念が無く、休日判定はどこも `weekday() >= 5`
(土日)だけで行っていたため、祝日はすべて平日として扱われていた。
ここでは暦そのものの計算に加え、祝日が実際に「休日」として効く3箇所
(デイリークエストの曜日判定・YouTubeごほうび券の日次上限・すごろくの
チェックポイント時刻)を固定する。すごろく側の詳細は
tests/test_routine_holiday.py を参照。
"""
import datetime

import config
import pytest
import routine_data
from core.jp_holidays import (
    EXTRA_HOLIDAY_NAME,
    get_holiday_name,
    get_national_holidays,
    get_offday_reason,
    is_national_holiday,
    is_offday,
)
from services.quest.locks import get_youtube_daily_limit_minutes
from services.quest.quest_service import matches_day_of_week


class TestNationalHolidayCalendar:
    """実際の暦(2026年)との突き合わせ。"""

    def test_2026_holidays_match_the_official_calendar(self):
        holidays = get_national_holidays(2026)
        assert holidays == {
            datetime.date(2026, 1, 1): "元日",
            datetime.date(2026, 1, 12): "成人の日",
            datetime.date(2026, 2, 11): "建国記念の日",
            datetime.date(2026, 2, 23): "天皇誕生日",
            datetime.date(2026, 3, 20): "春分の日",
            datetime.date(2026, 4, 29): "昭和の日",
            datetime.date(2026, 5, 3): "憲法記念日",
            datetime.date(2026, 5, 4): "みどりの日",
            datetime.date(2026, 5, 5): "こどもの日",
            datetime.date(2026, 5, 6): "振替休日",
            datetime.date(2026, 7, 20): "海の日",
            datetime.date(2026, 8, 11): "山の日",
            datetime.date(2026, 9, 21): "敬老の日",
            datetime.date(2026, 9, 22): "国民の休日",
            datetime.date(2026, 9, 23): "秋分の日",
            datetime.date(2026, 10, 12): "スポーツの日",
            datetime.date(2026, 11, 3): "文化の日",
            datetime.date(2026, 11, 23): "勤労感謝の日",
        }

    def test_happy_monday_holidays_track_the_nth_monday(self):
        # 成人の日=1月第2月曜 / 海の日=7月第3月曜 / 敬老の日=9月第3月曜 /
        # スポーツの日=10月第2月曜
        assert get_holiday_name(datetime.date(2027, 1, 11)) == "成人の日"
        assert get_holiday_name(datetime.date(2027, 7, 19)) == "海の日"
        assert get_holiday_name(datetime.date(2027, 9, 20)) == "敬老の日"
        assert get_holiday_name(datetime.date(2027, 10, 11)) == "スポーツの日"

    def test_equinox_days_move_by_year(self):
        """春分・秋分は年によって日付が動く(近似式の回帰テスト)。"""
        assert get_holiday_name(datetime.date(2025, 3, 20)) == "春分の日"
        assert get_holiday_name(datetime.date(2026, 3, 20)) == "春分の日"
        assert get_holiday_name(datetime.date(2027, 3, 21)) == "春分の日"
        assert get_holiday_name(datetime.date(2025, 9, 23)) == "秋分の日"
        assert get_holiday_name(datetime.date(2027, 9, 23)) == "秋分の日"
        assert get_holiday_name(datetime.date(2028, 9, 22)) == "秋分の日"

    def test_substitute_holiday_skips_consecutive_holidays(self):
        """祝日が日曜と重なったら、その後で最も近い祝日でない日が振替休日になる。"""
        # 2026-05-03(憲法記念日)が日曜。5/4・5/5も祝日なので振替は5/6。
        assert get_holiday_name(datetime.date(2026, 5, 6)) == "振替休日"
        # 2025-11-23(勤労感謝の日)が日曜 → 翌24日(月)が振替休日
        assert get_holiday_name(datetime.date(2025, 11, 24)) == "振替休日"

    def test_citizens_holiday_between_two_holidays(self):
        """敬老の日と秋分の日に挟まれた平日は「国民の休日」になる(シルバーウィーク)。"""
        assert get_holiday_name(datetime.date(2026, 9, 22)) == "国民の休日"
        # 挟まれていない年には作られない
        assert get_holiday_name(datetime.date(2025, 9, 16)) is None

    def test_plain_weekday_is_not_a_holiday(self):
        assert get_holiday_name(datetime.date(2026, 9, 28)) is None
        assert is_national_holiday(datetime.date(2026, 9, 28)) is False


class TestIsOffday:
    def test_weekend_is_offday(self):
        assert is_offday(datetime.date(2026, 9, 19)) is True   # 土
        assert is_offday(datetime.date(2026, 9, 20)) is True   # 日

    def test_holiday_on_a_weekday_is_offday(self):
        assert is_offday(datetime.date(2026, 9, 21)) is True   # 月・敬老の日
        assert is_offday(datetime.date(2026, 9, 22)) is True   # 火・国民の休日

    def test_plain_weekday_is_not_offday(self):
        assert is_offday(datetime.date(2026, 9, 28)) is False  # 月

    def test_accepts_datetime_as_well_as_date(self):
        jst = datetime.timezone(datetime.timedelta(hours=9))
        assert is_offday(datetime.datetime(2026, 9, 21, 7, 0, tzinfo=jst)) is True
        assert is_offday(datetime.datetime(2026, 9, 28, 7, 0, tzinfo=jst)) is False

    def test_offday_reason_prefers_the_holiday_name(self):
        assert get_offday_reason(datetime.date(2026, 9, 21)) == "敬老の日"
        assert get_offday_reason(datetime.date(2026, 9, 19)) == "土曜"
        assert get_offday_reason(datetime.date(2026, 9, 20)) == "日曜"
        assert get_offday_reason(datetime.date(2026, 9, 28)) is None


class TestExtraHolidayDates:
    """config.EXTRA_HOLIDAY_DATES(年末年始・お盆・学校の振替休業日など)。"""

    def test_configured_date_becomes_an_offday(self, monkeypatch):
        target = datetime.date(2026, 12, 29)  # 火曜。祝日ではない
        assert is_offday(target) is False
        monkeypatch.setattr(config, "EXTRA_HOLIDAY_DATES", frozenset({target}))
        assert is_offday(target) is True
        assert get_holiday_name(target) == EXTRA_HOLIDAY_NAME
        assert get_offday_reason(target) == EXTRA_HOLIDAY_NAME
        # 国民の祝日そのものではない
        assert is_national_holiday(target) is False


class TestQuestDayOfWeekMatching:
    """デイリークエストの曜日指定と祝日の関係(services/quest/quest_service.py)。"""

    HOLIDAY_MONDAY = datetime.date(2026, 9, 21)   # 敬老の日
    PLAIN_MONDAY = datetime.date(2026, 9, 28)
    SATURDAY = datetime.date(2026, 9, 19)
    SUNDAY = datetime.date(2026, 9, 20)

    def test_weekday_only_quest_is_hidden_on_a_holiday(self):
        """「会社勤務(通常)」「小学校に行く」など days='0,1,2,3,4' のクエスト。"""
        weekday_only = [0, 1, 2, 3, 4]
        assert matches_day_of_week(self.PLAIN_MONDAY, weekday_only) is True
        assert matches_day_of_week(self.HOLIDAY_MONDAY, weekday_only) is False
        assert matches_day_of_week(self.SATURDAY, weekday_only) is False

    def test_weekend_quest_is_shown_on_a_holiday(self):
        """「朝の会 開催」「洗車」など days='5,6' のクエスト。"""
        weekend = [5, 6]
        assert matches_day_of_week(self.SATURDAY, weekend) is True
        assert matches_day_of_week(self.HOLIDAY_MONDAY, weekend) is True
        assert matches_day_of_week(self.PLAIN_MONDAY, weekend) is False

    def test_quest_spanning_friday_to_sunday_is_shown_on_a_holiday(self):
        """「土日の宿題」(days='4,5,6')も休日クエストとして扱う。"""
        assert matches_day_of_week(self.HOLIDAY_MONDAY, [4, 5, 6]) is True

    def test_specific_weekday_quest_is_unchanged_on_a_holiday(self):
        """ゴミ捨て(月・木/水/金)は祝日も通常どおり出す(ゴミ収集は祝日も動く)。"""
        assert matches_day_of_week(self.HOLIDAY_MONDAY, [0, 3]) is True
        assert matches_day_of_week(self.PLAIN_MONDAY, [0, 3]) is True
        # 指定曜日そのものでなければ、祝日でも出ない
        assert matches_day_of_week(self.HOLIDAY_MONDAY, [2]) is False

    def test_single_weekend_day_quest_is_not_widened_to_holidays(self):
        """日曜だけの指定(習い事の連絡帳記入など)は実際の日曜に紐づくので広げない。"""
        assert matches_day_of_week(self.SUNDAY, [6]) is True
        assert matches_day_of_week(self.HOLIDAY_MONDAY, [6]) is False

    def test_extra_holiday_date_behaves_like_a_holiday(self, monkeypatch):
        target = datetime.date(2026, 12, 29)  # 火曜
        monkeypatch.setattr(config, "EXTRA_HOLIDAY_DATES", frozenset({target}))
        assert matches_day_of_week(target, [0, 1, 2, 3, 4]) is False
        assert matches_day_of_week(target, [5, 6]) is True


class TestYoutubeDailyLimitOnHolidays:
    @pytest.fixture(autouse=True)
    def _limits(self, monkeypatch):
        monkeypatch.setattr(config, "YOUTUBE_DAILY_LIMIT_MINUTES_WEEKDAY", 60)
        monkeypatch.setattr(config, "YOUTUBE_DAILY_LIMIT_MINUTES_HOLIDAY", 90)

    def test_holiday_uses_the_holiday_limit(self):
        assert get_youtube_daily_limit_minutes(datetime.date(2026, 9, 21)) == 90

    def test_plain_weekday_uses_the_weekday_limit(self):
        assert get_youtube_daily_limit_minutes(datetime.date(2026, 9, 28)) == 60

    def test_weekend_still_uses_the_holiday_limit(self):
        assert get_youtube_daily_limit_minutes(datetime.date(2026, 9, 19)) == 90

    def test_zero_still_means_unlimited_on_a_holiday(self, monkeypatch):
        monkeypatch.setattr(config, "YOUTUBE_DAILY_LIMIT_MINUTES_HOLIDAY", 0)
        assert get_youtube_daily_limit_minutes(datetime.date(2026, 9, 21)) is None


class TestRoutineCheckpointTime:
    """すごろくの締切時刻(routine_data.get_effective_checkpoint_time)。"""

    JST = datetime.timezone(datetime.timedelta(hours=9))

    def _free_step(self):
        am = routine_data.ROUTINE_FLOWS['am']
        return next(s for s in am['steps'] if s['key'] == 'free')

    def test_holiday_uses_the_weekend_checkpoint_time(self):
        step = self._free_step()
        holiday = datetime.datetime(2026, 9, 21, 7, 0, tzinfo=self.JST)  # 敬老の日
        assert routine_data.get_effective_checkpoint_time(step, holiday) == '09:30'

    def test_plain_weekday_uses_the_weekday_checkpoint_time(self):
        step = self._free_step()
        weekday = datetime.datetime(2026, 9, 28, 7, 0, tzinfo=self.JST)
        assert routine_data.get_effective_checkpoint_time(step, weekday) == '07:50'
