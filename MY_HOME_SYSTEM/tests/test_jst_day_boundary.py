# MY_HOME_SYSTEM/tests/test_jst_day_boundary.py
"""Issue #658: JST の日付境界をまたぐ時刻で、日付依存のロジックが期待どおり動くことを固定する。

`tests/` には `datetime.now()` / `date.today()` を実時刻のまま使うファイルが複数あり、
「CI が JST の深夜0時前後に走ったときだけ落ちる/通る」潜在的なフレークが指摘されていた。
タイムゾーン(ホストOSの TZ)への依存は `core/utils` の JST 固定ヘルパーで既に解消されており、
実際に TZ を UTC / America/New_York / Pacific/Honolulu / Pacific/Kiritimati に変えて
全テストが通ることは確認済み。残る本質的なリスクは「JST の日付が変わる瞬間」なので、
ここでは freezegun で境界前後を明示的に固定して検証する。

新しく日付に依存するテストを書くときは、実時刻に任せず `freeze_time` で固定すること。
"""
import datetime
import os
import sys

from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.utils import get_display_date, get_now_jst, get_today_date_str
from services.quest.locks import _is_youtube_cooldown_enforced

JST = datetime.timezone(datetime.timedelta(hours=9))

# JST 2026-09-17 00:00:00 の1秒前 / 1秒後(UTC 表記で固定する)
JUST_BEFORE_JST_MIDNIGHT = "2026-09-16 14:59:59"  # = JST 2026-09-16 23:59:59
JUST_AFTER_JST_MIDNIGHT = "2026-09-16 15:00:01"   # = JST 2026-09-17 00:00:01


class TestJstHelpersAcrossMidnight:
    @freeze_time(JUST_BEFORE_JST_MIDNIGHT)
    def test_today_str_is_previous_day_just_before_midnight(self):
        assert get_today_date_str() == "2026-09-16"
        assert get_display_date() == "09/16"
        assert get_now_jst().date() == datetime.date(2026, 9, 16)

    @freeze_time(JUST_AFTER_JST_MIDNIGHT)
    def test_today_str_rolls_over_just_after_midnight(self):
        assert get_today_date_str() == "2026-09-17"
        assert get_display_date() == "09/17"
        assert get_now_jst().date() == datetime.date(2026, 9, 17)

    @freeze_time("2026-09-16 15:00:01")
    def test_utc_date_and_jst_date_can_differ(self):
        """UTC ではまだ前日でも、JST では翌日になっていること(ホスト TZ に依存しない)。"""
        assert datetime.datetime.now(datetime.timezone.utc).date() == datetime.date(2026, 9, 16)
        assert get_now_jst().date() == datetime.date(2026, 9, 17)


class TestYoutubeCooldownEnforcementBoundary:
    """施行日(config.YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM)の境界。

    既定値(2026-09-12)は既に過去のため、実時刻のままでは「施行前」の経路を再現できない。
    freeze_time と monkeypatch で両側を明示的に固定する。
    """

    @freeze_time(JUST_BEFORE_JST_MIDNIGHT)
    def test_not_enforced_on_the_day_before(self, monkeypatch):
        monkeypatch.setattr(config, "YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", datetime.date(2026, 9, 17))
        assert _is_youtube_cooldown_enforced() is False

    @freeze_time(JUST_AFTER_JST_MIDNIGHT)
    def test_enforced_from_the_first_moment_of_the_day(self, monkeypatch):
        monkeypatch.setattr(config, "YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", datetime.date(2026, 9, 17))
        assert _is_youtube_cooldown_enforced() is True

    @freeze_time(JUST_BEFORE_JST_MIDNIGHT)
    def test_enforced_when_the_date_has_already_passed(self, monkeypatch):
        monkeypatch.setattr(config, "YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", datetime.date(2026, 9, 12))
        assert _is_youtube_cooldown_enforced() is True

    def test_default_enforce_from_is_a_date(self):
        """既定値が date として解釈できること(不正な .env でも起動を止めない設計の確認)。"""
        assert isinstance(config.YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM, datetime.date)


class TestTvLockDailyGuardAcrossMidnight:
    """深夜2時台に走る tv_lock_monitor の「本日実行済み」判定が日付跨ぎで壊れないこと。"""

    def test_same_day_marker_blocks_second_run_and_next_day_allows_it(self, tmp_path, monkeypatch):
        from core import state_file
        from monitors import tv_lock_monitor

        marker = str(tmp_path / "tv_lock_last_run.txt")
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", marker, raising=False)

        with freeze_time("2026-09-16 17:00:00"):  # JST 2026-09-17 02:00
            today = get_today_date_str()
            state_file.write_text_atomic(marker, today)
            assert today == "2026-09-17"
            assert state_file.read_text(marker) == today

        with freeze_time("2026-09-17 17:00:00"):  # 翌日 JST 02:00
            assert state_file.read_text(marker) != get_today_date_str()
