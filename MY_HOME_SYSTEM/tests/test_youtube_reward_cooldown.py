# MY_HOME_SYSTEM/tests/test_youtube_reward_cooldown.py
"""
YouTube系ごほうび券の視聴制限(連続使用防止クールダウン・1日の合計分数上限)の回帰テスト。

子供の目の負担を防ぐため、config.YOUTUBE_REWARD_IDSに含まれるreward_idの
ごほうび券には2つの制限がかかる。

1. クールダウン: 1枚使うと「その券の視聴分数 + YOUTUBE_REWARD_BREAK_SECONDS(15分)」
   経過するまで次の1枚を使えない。起点は使い始めた時刻(used_at)なので、券の
   視聴分数を足さないと30分券・60分券では見終わる前にクールダウンが明けてしまい、
   休憩が実質ゼロになる(この退行を防ぐのが本ファイルの主目的の1つ)。
2. 日次上限: JSTの1日で使える合計視聴分数の上限(平日/休日で別値)。
"""
import datetime
import os
import sys

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.utils import get_now_iso
from core.database import get_db_cursor
from services import quest_service as qs_module
from services.quest_service import JST, ROLE_CHILD

YOUTUBE_REWARD_IDS = [701, 702]
OTHER_REWARD_ID = 703
# 701 = 10分券、702 = 30分券 (実データの 10:00 / 30:00 券に対応させたテスト用の対応表)
YOUTUBE_REWARD_DURATION_MINUTES = {701: 10, 702: 30}
WEEKDAY_LIMIT_MINUTES = 60
HOLIDAY_LIMIT_MINUTES = 90
# 日次上限を延長できる「プリント」相当のクエスト(テスト用のID)
EXTENSION_QUEST_ID = 901
EXTENSION_MINUTES_PER_QUEST = 30
EXTENSION_MAX_PER_DAY = 2


def _seed_user(user_id: str) -> None:
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES (?, ?, 'Warrior', 1, 0, 0, ?)",
            (user_id, user_id, ROLE_CHILD),
        )


def _seed_reward_master() -> None:
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (701, 'Youtube (10:00)', 50, 'children')"
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (702, 'Youtube (30:00)', 150, 'children')"
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (703, '好きなおやつ', 100, 'children')"
        )


def _seed(user_id: str = "son") -> None:
    _seed_user(user_id)
    _seed_reward_master()


def _grant_item(user_id: str, reward_id: int, used_at: str = None, status: str = 'owned') -> int:
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at, used_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, reward_id, status, get_now_iso(), used_at),
        )
        return cur.execute("SELECT id FROM user_inventory ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _iso_seconds_ago(seconds: int) -> str:
    return (datetime.datetime.now(JST) - datetime.timedelta(seconds=seconds)).isoformat()


def _complete_extension_quest(
    user_id: str,
    completed_at: str,
    status: str = 'approved',
    quest_id: int = EXTENSION_QUEST_ID,
) -> None:
    """延長対象クエスト(プリント)の完了履歴を1件作る。"""
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_history "
            "(user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
            "VALUES (?, ?, 'プリント', 0, 0, ?, ?)",
            (user_id, quest_id, completed_at, status),
        )


@pytest.fixture(autouse=True)
def _youtube_reward_ids(monkeypatch):
    monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_IDS", YOUTUBE_REWARD_IDS)
    monkeypatch.setattr(
        qs_module.config, "YOUTUBE_REWARD_DURATION_MINUTES", dict(YOUTUBE_REWARD_DURATION_MINUTES)
    )
    monkeypatch.setattr(
        qs_module.config, "YOUTUBE_DAILY_LIMIT_MINUTES_WEEKDAY", WEEKDAY_LIMIT_MINUTES
    )
    monkeypatch.setattr(
        qs_module.config, "YOUTUBE_DAILY_LIMIT_MINUTES_HOLIDAY", HOLIDAY_LIMIT_MINUTES
    )
    monkeypatch.setattr(qs_module.config, "YOUTUBE_EXTENSION_QUEST_IDS", [EXTENSION_QUEST_ID])
    monkeypatch.setattr(
        qs_module.config, "YOUTUBE_EXTENSION_MINUTES_PER_QUEST", EXTENSION_MINUTES_PER_QUEST
    )
    monkeypatch.setattr(qs_module.config, "YOUTUBE_EXTENSION_MAX_PER_DAY", EXTENSION_MAX_PER_DAY)
    # 猶予期間(施行前の予告のみ)のテストはこれらの日付を未来に上書きする。それ以外の
    # テストは「既に施行済み」を前提とするため、常に過去日をデフォルトにしておく。
    monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", datetime.date(2000, 1, 1))
    monkeypatch.setattr(qs_module.config, "YOUTUBE_DAILY_LIMIT_ENFORCE_FROM", datetime.date(2000, 1, 1))
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: None)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)


def test_second_youtube_ticket_is_blocked_within_cooldown(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    first_id = _grant_item("son", 701)
    second_id = _grant_item("son", 702)  # 別の尺(30:00)でも同じYouTube系として扱う

    assert service.use_item("son", first_id)["status"] == "consumed"

    with pytest.raises(HTTPException) as exc:
        service.use_item("son", second_id)
    assert exc.value.status_code == 429
    assert "分" in str(exc.value.detail)

    with get_db_cursor() as cur:
        assert cur.execute(
            "SELECT status FROM user_inventory WHERE id=?", (second_id,)
        ).fetchone()["status"] == "owned"


def test_non_youtube_ticket_is_not_affected_by_cooldown(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    youtube_id = _grant_item("son", 701)
    other_id = _grant_item("son", OTHER_REWARD_ID)

    assert service.use_item("son", youtube_id)["status"] == "consumed"
    # YouTube系を使った直後でも、無関係な報酬は即座に使える
    assert service.use_item("son", other_id)["status"] == "consumed"


def test_cooldown_expires_after_configured_duration(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    # 26分前に使用済みの10分券(視聴10分 + 休憩15分 = 25分のクールダウンは終了している)
    _grant_item("son", 701, used_at=_iso_seconds_ago(26 * 60), status='consumed')
    second_id = _grant_item("son", 702)

    assert service.use_item("son", second_id)["status"] == "consumed"


def test_cooldown_is_scoped_per_user(isolated_db):
    _seed("son")
    _seed_user("daughter")
    service = qs_module.InventoryService()
    son_item = _grant_item("son", 701)
    daughter_item = _grant_item("daughter", 701)

    assert service.use_item("son", son_item)["status"] == "consumed"
    # 兄がクールダウン中でも、妹は別ユーザーなので影響を受けない
    assert service.use_item("daughter", daughter_item)["status"] == "consumed"


def test_get_user_inventory_reports_cooldown_and_youtube_flag(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    used_id = _grant_item("son", 701)
    still_owned_youtube_id = _grant_item("son", 702)
    still_owned_other_id = _grant_item("son", OTHER_REWARD_ID)

    service.use_item("son", used_id)

    result = service.get_user_inventory("son")
    assert set(result.keys()) == {
        "items",
        "youtube_cooldown_remaining_seconds",
        "youtube_cooldown_announcement",
        "youtube_daily_limit_minutes",
        "youtube_daily_used_minutes",
        "youtube_daily_limit_announcement",
        "youtube_extension",
    }
    # 使ったのは701(10分券)なので、待ち時間は 10分 + 休憩15分 = 25分
    assert 0 < result["youtube_cooldown_remaining_seconds"] <= 25 * 60
    # 施行済み(ENFORCE_FROMが過去)のため、予告バナー用の情報は出ない
    assert result["youtube_cooldown_announcement"] is None

    items_by_id = {item["id"]: item for item in result["items"]}
    # 消費済みのアイテムは(既存仕様どおり)一覧に含まれない
    assert used_id not in items_by_id
    assert items_by_id[still_owned_youtube_id]["is_youtube_reward"] is True
    assert items_by_id[still_owned_other_id]["is_youtube_reward"] is False


def test_get_user_inventory_reports_zero_cooldown_when_never_used(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    _grant_item("son", 701)

    result = service.get_user_inventory("son")
    assert result["youtube_cooldown_remaining_seconds"] == 0


class TestGracePeriodBeforeEnforcement:
    """
    ENFORCE_FROM(施行日)より前は、いきなり制限がかかると子どもが困惑するため、
    使用自体は拒否せず、family-quest側に予告バナーを表示するための情報だけを返す。
    """

    def _set_enforce_from_in_future(self, monkeypatch, days_from_now: int = 7):
        # #658: 施行日は実時刻からの相対ではなく、JST の現在日付を基準に明示的に置く。
        # (JST の日付境界そのものの検証は tests/test_jst_day_boundary.py が受け持つ。)
        future_date = (datetime.datetime.now(JST) + datetime.timedelta(days=days_from_now)).date()
        monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", future_date)
        return future_date

    def test_use_item_is_not_blocked_before_enforce_date(self, isolated_db, monkeypatch):
        self._set_enforce_from_in_future(monkeypatch)
        _seed()
        service = qs_module.InventoryService()
        first_id = _grant_item("son", 701)
        second_id = _grant_item("son", 702)

        # 施行前は連続使用しても拒否されない
        assert service.use_item("son", first_id)["status"] == "consumed"
        assert service.use_item("son", second_id)["status"] == "consumed"

    def test_get_user_inventory_returns_announcement_before_enforce_date(self, isolated_db, monkeypatch):
        future_date = self._set_enforce_from_in_future(monkeypatch, days_from_now=7)
        _seed()
        service = qs_module.InventoryService()
        used_id = _grant_item("son", 701)
        _grant_item("son", 702)
        service.use_item("son", used_id)

        result = service.get_user_inventory("son")
        # 施行前なので、実際に使用済みでも残りクールダウンは常に0
        assert result["youtube_cooldown_remaining_seconds"] == 0

        announcement = result["youtube_cooldown_announcement"]
        assert announcement is not None
        assert announcement["starts_on"] == future_date.isoformat()
        assert 0 <= announcement["days_remaining"] <= 7

    def test_no_announcement_when_no_youtube_reward_ids_configured(self, isolated_db, monkeypatch):
        self._set_enforce_from_in_future(monkeypatch)
        monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_IDS", [])
        _seed()
        service = qs_module.InventoryService()
        _grant_item("son", 701)

        result = service.get_user_inventory("son")
        assert result["youtube_cooldown_announcement"] is None

    def test_announcement_disappears_once_enforce_date_arrives(self, isolated_db, monkeypatch):
        # デフォルトのfixtureは既にENFORCE_FROMを過去日にしている(=施行済み)
        _seed()
        service = qs_module.InventoryService()
        _grant_item("son", 701)

        result = service.get_user_inventory("son")
        assert result["youtube_cooldown_announcement"] is None


class TestCooldownIncludesWatchingTime:
    """
    クールダウンの起点は「使い始めた時刻(used_at)」なので、券の視聴分数を
    足さずに固定15分だけ待たせると、30分券・60分券では**見ている途中で**
    クールダウンが明けてしまい、休憩が一切入らない。この退行を防ぐ。
    """

    def test_30min_ticket_still_cooling_down_right_after_watching(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        # 31分前に30分券を使用 = ちょうど見終わった直後。休憩15分はこれから。
        _grant_item("son", 702, used_at=_iso_seconds_ago(31 * 60), status='consumed')
        next_id = _grant_item("son", 701)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", next_id)
        assert exc.value.status_code == 429

    def test_30min_ticket_usable_after_watching_plus_break(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        # 46分前 = 視聴30分 + 休憩15分 を満たしている
        _grant_item("son", 702, used_at=_iso_seconds_ago(46 * 60), status='consumed')
        next_id = _grant_item("son", 701)

        assert service.use_item("son", next_id)["status"] == "consumed"

    def test_remaining_seconds_reflect_the_last_used_ticket_length(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        # 直前に使ったのが30分券なら、残りは「45分 - 経過1分」前後になる
        _grant_item("son", 702, used_at=_iso_seconds_ago(60), status='consumed')
        _grant_item("son", 701)

        remaining = service.get_user_inventory("son")["youtube_cooldown_remaining_seconds"]
        assert 43 * 60 < remaining <= 45 * 60

    def test_ticket_without_known_duration_falls_back_to_break_only(self, isolated_db, monkeypatch):
        # 対応表に無いreward_idは0分扱い(=休憩15分のみ)。券を増やしたのに
        # YOUTUBE_REWARD_DURATION_MINUTES への追記を忘れても使用自体は壊れない。
        monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_DURATION_MINUTES", {701: 10})
        _seed()
        service = qs_module.InventoryService()
        _grant_item("son", 702, used_at=_iso_seconds_ago(16 * 60), status='consumed')
        next_id = _grant_item("son", 701)

        assert service.use_item("son", next_id)["status"] == "consumed"


class TestDailyLimit:
    """
    1日の合計視聴分数の上限。クールダウンは「間隔」しか縛らないため、ゴールドが
    続く限り何枚でも使えてしまう。総量はこちらで制限する。

    クールダウンと独立に検証したいので、既に使用済みの券はいずれも十分過去の
    used_at を持たせ、クールダウン側では弾かれないようにしている。
    """

    # 平日(月)のJST午前10時。曜日で上限が変わるため、日付は固定する(#658)。
    # 祝日も休日の上限が適用される(core/jp_holidays.py)ようになったため、
    # 平日側は祝日でない月曜(2026-09-21は敬老の日なので2026-09-28)を使う。
    WEEKDAY_NOON_UTC = "2026-09-28T01:00:00"   # JST 2026-09-28(月) 10:00
    HOLIDAY_NOON_UTC = "2026-09-19T01:00:00"   # JST 2026-09-19(土) 10:00

    def _used_today(self, user_id: str, reward_id: int, minutes_ago: int) -> None:
        _grant_item(
            user_id, reward_id, used_at=_iso_seconds_ago(minutes_ago * 60), status='consumed'
        )

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_ticket_exceeding_remaining_minutes_is_blocked(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        # 今日すでに 30分 + 10分 = 40分。上限60分なので残りは20分。
        self._used_today("son", 702, minutes_ago=240)
        self._used_today("son", 701, minutes_ago=120)
        thirty_min_ticket = _grant_item("son", 702)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", thirty_min_ticket)
        assert exc.value.status_code == 429
        assert "あと20分" in str(exc.value.detail)

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_ticket_that_fits_in_remaining_minutes_is_allowed(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        # 40分使用済み・残り20分なら、10分券はちょうど収まる
        self._used_today("son", 702, minutes_ago=240)
        self._used_today("son", 701, minutes_ago=120)
        ten_min_ticket = _grant_item("son", 701)

        assert service.use_item("son", ten_min_ticket)["status"] == "consumed"

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_message_says_come_back_tomorrow_when_no_extension_is_left(self, isolated_db, monkeypatch):
        # 延長機能を無効にしたうえで使い切ると、「また明日」で締める
        monkeypatch.setattr(qs_module.config, "YOUTUBE_EXTENSION_QUEST_IDS", [])
        _seed()
        service = qs_module.InventoryService()
        # 30分 × 2 = 60分ちょうど。残り0分。
        self._used_today("son", 702, minutes_ago=240)
        self._used_today("son", 702, minutes_ago=120)
        ten_min_ticket = _grant_item("son", 701)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", ten_min_ticket)
        assert exc.value.status_code == 429
        assert "また明日" in str(exc.value.detail)

    @freeze_time(HOLIDAY_NOON_UTC)
    def test_holiday_uses_the_holiday_limit(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        # 土曜は上限90分。平日上限(60分)を超える60分使用済みでも、まだ30分残っている。
        self._used_today("son", 702, minutes_ago=240)
        self._used_today("son", 702, minutes_ago=120)
        thirty_min_ticket = _grant_item("son", 702)

        assert service.use_item("son", thirty_min_ticket)["status"] == "consumed"

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_non_youtube_rewards_do_not_count_toward_the_limit(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._used_today("son", 702, minutes_ago=240)
        self._used_today("son", OTHER_REWARD_ID, minutes_ago=120)
        thirty_min_ticket = _grant_item("son", 702)

        # YouTube系は30分しか使っていないので、もう1枚の30分券で上限ちょうど
        assert service.use_item("son", thirty_min_ticket)["status"] == "consumed"

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_yesterdays_usage_does_not_count(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        # 前日(JST 2026-09-27)に上限いっぱい使っていても、今日は最初から60分使える
        yesterday = "2026-09-27T20:00:00+09:00"
        _grant_item("son", 702, used_at=yesterday, status='consumed')
        _grant_item("son", 702, used_at=yesterday, status='consumed')
        thirty_min_ticket = _grant_item("son", 702)

        assert service.use_item("son", thirty_min_ticket)["status"] == "consumed"

    # JST 2026-09-28(月) 08:30。UTCへ直すと前日(2026-09-27)になる時刻。
    @freeze_time("2026-09-27T23:30:00")
    def test_morning_usage_is_counted_on_the_jst_date(self, isolated_db):
        """
        used_at はJSTオフセット付きISO文字列で保存されるため、SQLiteの date() で
        日付を切り出すとUTCへ変換され、JSTの朝9時より前の使用が「前日」に
        数えられてしまう。文字列の先頭一致で判定していることの回帰テスト。
        """
        _seed()
        service = qs_module.InventoryService()
        used_id = _grant_item("son", 702)
        service.use_item("son", used_id)  # JSTでは 2026-09-28 08:30 の使用

        result = service.get_user_inventory("son")
        assert result["youtube_daily_used_minutes"] == 30

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_zero_limit_means_unlimited(self, isolated_db, monkeypatch):
        monkeypatch.setattr(qs_module.config, "YOUTUBE_DAILY_LIMIT_MINUTES_WEEKDAY", 0)
        _seed()
        service = qs_module.InventoryService()
        self._used_today("son", 702, minutes_ago=240)
        self._used_today("son", 702, minutes_ago=120)
        thirty_min_ticket = _grant_item("son", 702)

        assert service.use_item("son", thirty_min_ticket)["status"] == "consumed"
        assert service.get_user_inventory("son")["youtube_daily_limit_minutes"] is None

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_limit_is_scoped_per_user(self, isolated_db):
        _seed("son")
        _seed_user("daughter")
        service = qs_module.InventoryService()
        self._used_today("son", 702, minutes_ago=240)
        self._used_today("son", 702, minutes_ago=120)
        daughter_ticket = _grant_item("daughter", 702)

        # 兄が上限に達していても、妹は別ユーザーなので影響を受けない
        assert service.use_item("daughter", daughter_ticket)["status"] == "consumed"

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_get_user_inventory_reports_daily_limit_fields(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._used_today("son", 702, minutes_ago=240)
        _grant_item("son", 701)
        _grant_item("son", OTHER_REWARD_ID)

        result = service.get_user_inventory("son")
        assert result["youtube_daily_limit_minutes"] == WEEKDAY_LIMIT_MINUTES
        assert result["youtube_daily_used_minutes"] == 30
        # 施行済み(ENFORCE_FROMが過去)のため予告バナーは出ない
        assert result["youtube_daily_limit_announcement"] is None

        items_by_reward = {item["reward_id"]: item for item in result["items"]}
        # フロントが「残り分数に収まらない券」を出し分けられるよう、券の長さも返す
        assert items_by_reward[701]["youtube_duration_minutes"] == 10
        assert items_by_reward[OTHER_REWARD_ID]["youtube_duration_minutes"] is None


class TestDailyLimitGracePeriod:
    """
    日次上限もクールダウンと同じく、施行日までは拒否せず予告バナーのみ表示する。
    施行日はクールダウンとは別管理(YOUTUBE_DAILY_LIMIT_ENFORCE_FROM)である。
    """

    def _set_enforce_from_in_future(self, monkeypatch, days_from_now: int = 7):
        future_date = (datetime.datetime.now(JST) + datetime.timedelta(days=days_from_now)).date()
        monkeypatch.setattr(qs_module.config, "YOUTUBE_DAILY_LIMIT_ENFORCE_FROM", future_date)
        return future_date

    def test_use_item_is_not_blocked_before_enforce_date(self, isolated_db, monkeypatch):
        self._set_enforce_from_in_future(monkeypatch)
        # クールダウン側で弾かれないよう、こちらも施行前にしておく
        self_enforce = (datetime.datetime.now(JST) + datetime.timedelta(days=7)).date()
        monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", self_enforce)
        _seed()
        service = qs_module.InventoryService()
        # 上限60分を超える3枚(30+30+30=90分)を連続で使っても拒否されない
        ids = [_grant_item("son", 702) for _ in range(3)]
        for inventory_id in ids:
            assert service.use_item("son", inventory_id)["status"] == "consumed"

    def test_announcement_is_returned_before_enforce_date(self, isolated_db, monkeypatch):
        future_date = self._set_enforce_from_in_future(monkeypatch, days_from_now=7)
        _seed()
        service = qs_module.InventoryService()
        _grant_item("son", 701)

        result = service.get_user_inventory("son")
        announcement = result["youtube_daily_limit_announcement"]
        assert announcement is not None
        assert announcement["starts_on"] == future_date.isoformat()
        assert 0 <= announcement["days_remaining"] <= 7
        # 施行前でも「今日はあと何分」を表示して慣れてもらうため、上限自体は返す
        assert result["youtube_daily_limit_minutes"] is not None

    def test_no_announcement_when_limit_is_disabled(self, isolated_db, monkeypatch):
        self._set_enforce_from_in_future(monkeypatch)
        monkeypatch.setattr(qs_module.config, "YOUTUBE_DAILY_LIMIT_MINUTES_WEEKDAY", 0)
        monkeypatch.setattr(qs_module.config, "YOUTUBE_DAILY_LIMIT_MINUTES_HOLIDAY", 0)
        _seed()
        service = qs_module.InventoryService()
        _grant_item("son", 701)

        assert service.get_user_inventory("son")["youtube_daily_limit_announcement"] is None


class TestDailyLimitExtensionByQuest:
    """
    日次上限を使い切った後に「追加で」プリントをやると上限が延びる仕組み。

    朝の日課としてやったプリントで最初から上限が伸びていては「もっと見たいから
    もう1枚やる」という交換にならないため、**上限に達した後に完了した**プリント
    だけが延長になる。また、やっていないプリントを自己申告するだけで延ばせては
    意味がないため、**親に承認された(status='approved')** ものだけを数える。
    """

    # 上のTestDailyLimitと同じ理由で、祝日でない月曜を使う。
    WEEKDAY_NOON_UTC = "2026-09-28T01:00:00"   # JST 2026-09-28(月) 10:00

    def _use_up_the_limit(self, user_id: str = "son") -> None:
        """30分券 × 2 = 60分(平日の上限ちょうど)を使った状態にする。"""
        for minutes_ago in (240, 180):
            _grant_item(
                user_id, 702, used_at=_iso_seconds_ago(minutes_ago * 60), status='consumed'
            )

    def _iso_minutes_ago(self, minutes: int) -> str:
        return _iso_seconds_ago(minutes * 60)

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_print_after_reaching_the_limit_extends_it(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()
        # 上限に達した(180分前)より後にプリントを1枚やって承認された
        _complete_extension_quest("son", self._iso_minutes_ago(120))
        thirty_min_ticket = _grant_item("son", 702)

        # 上限が 60 → 90 に延びているので、30分券がもう1枚使える
        assert service.use_item("son", thirty_min_ticket)["status"] == "consumed"

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_print_before_reaching_the_limit_does_not_extend(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        # 朝の日課としてのプリント(上限に達する前=300分前)は延長にならない
        _complete_extension_quest("son", self._iso_minutes_ago(300))
        self._use_up_the_limit()
        thirty_min_ticket = _grant_item("son", 702)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", thirty_min_ticket)
        assert exc.value.status_code == 429

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_unapproved_print_does_not_extend(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()
        # 完了報告しただけ(親の承認待ち)では延長されない
        _complete_extension_quest("son", self._iso_minutes_ago(120), status='pending')
        thirty_min_ticket = _grant_item("son", 702)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", thirty_min_ticket)
        assert exc.value.status_code == 429

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_other_quests_do_not_extend(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()
        # 対象外のクエストをいくら完了しても延長にはならない
        _complete_extension_quest("son", self._iso_minutes_ago(120), quest_id=999)
        thirty_min_ticket = _grant_item("son", 702)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", thirty_min_ticket)
        assert exc.value.status_code == 429

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_extension_is_capped_per_day(self, isolated_db):
        """使い切る→プリントを3回くり返しても、延長は MAX_PER_DAY(2回)で打ち止め。"""
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()                                       # 60分使用
        _complete_extension_quest("son", self._iso_minutes_ago(170))   # 60→90
        _grant_item("son", 702, used_at=self._iso_minutes_ago(160), status='consumed')  # 90分使用
        _complete_extension_quest("son", self._iso_minutes_ago(150))   # 90→120
        _grant_item("son", 702, used_at=self._iso_minutes_ago(140), status='consumed')  # 120分使用
        _complete_extension_quest("son", self._iso_minutes_ago(130))   # 上限到達済みだが3回目なので無効

        result = service.get_user_inventory("son")
        assert result["youtube_daily_limit_minutes"] == (
            WEEKDAY_LIMIT_MINUTES + EXTENSION_MINUTES_PER_QUEST * EXTENSION_MAX_PER_DAY
        )
        assert result["youtube_extension"]["granted_count"] == EXTENSION_MAX_PER_DAY
        assert result["youtube_extension"]["can_extend_now"] is False

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_second_print_needs_the_extended_limit_to_be_used_up_too(self, isolated_db):
        """
        1枚目のプリントで60→90分になった直後に2枚目をやっても、その時点では
        90分を使い切っていないので2枚目は延長にならない(延長の連続取得を防ぐ)。
        """
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()
        _complete_extension_quest("son", self._iso_minutes_ago(150))
        _complete_extension_quest("son", self._iso_minutes_ago(140))

        result = service.get_user_inventory("son")
        assert result["youtube_daily_limit_minutes"] == WEEKDAY_LIMIT_MINUTES + EXTENSION_MINUTES_PER_QUEST
        assert result["youtube_extension"]["granted_count"] == 1

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_second_extension_after_using_up_the_extended_limit(self, isolated_db):
        """使い切る→プリント→使い切る→プリント、と交互なら2回とも延長される。"""
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()                                  # 60分使用(240分前・180分前)
        _complete_extension_quest("son", self._iso_minutes_ago(150))   # 60→90
        _grant_item("son", 702, used_at=self._iso_minutes_ago(120), status='consumed')  # 90分使用
        _complete_extension_quest("son", self._iso_minutes_ago(90))    # 90→120

        result = service.get_user_inventory("son")
        assert result["youtube_daily_limit_minutes"] == (
            WEEKDAY_LIMIT_MINUTES + EXTENSION_MINUTES_PER_QUEST * 2
        )
        assert result["youtube_extension"]["granted_count"] == 2

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_yesterdays_print_does_not_extend_today(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()
        _complete_extension_quest("son", "2026-09-20T20:00:00+09:00")
        thirty_min_ticket = _grant_item("son", 702)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", thirty_min_ticket)
        assert exc.value.status_code == 429

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_extension_is_scoped_per_user(self, isolated_db):
        _seed("son")
        _seed_user("daughter")
        service = qs_module.InventoryService()
        self._use_up_the_limit("son")
        # 妹がやったプリントで兄の上限は延びない
        _complete_extension_quest("daughter", self._iso_minutes_ago(120))
        thirty_min_ticket = _grant_item("son", 702)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", thirty_min_ticket)
        assert exc.value.status_code == 429

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_message_suggests_a_print_while_extension_is_available(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()
        ten_min_ticket = _grant_item("son", 701)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", ten_min_ticket)
        assert exc.value.status_code == 429
        assert f"プリントを1枚やると{EXTENSION_MINUTES_PER_QUEST}分ふえる" in str(exc.value.detail)

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_message_says_come_back_tomorrow_once_extensions_are_exhausted(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()
        # 2回ぶんの延長を取り切り、延長後の上限(120分)も使い切った状態を作る
        _complete_extension_quest("son", self._iso_minutes_ago(150))
        _grant_item("son", 702, used_at=self._iso_minutes_ago(140), status='consumed')
        _complete_extension_quest("son", self._iso_minutes_ago(130))
        _grant_item("son", 702, used_at=self._iso_minutes_ago(120), status='consumed')
        ten_min_ticket = _grant_item("son", 701)

        with pytest.raises(HTTPException) as exc:
            service.use_item("son", ten_min_ticket)
        assert exc.value.status_code == 429
        assert "また明日" in str(exc.value.detail)

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_get_user_inventory_reports_extension_state(self, isolated_db):
        _seed()
        service = qs_module.InventoryService()
        self._use_up_the_limit()
        _grant_item("son", 701)

        extension = service.get_user_inventory("son")["youtube_extension"]
        assert extension == {
            "minutes_per_quest": EXTENSION_MINUTES_PER_QUEST,
            "granted_count": 0,
            "max_per_day": EXTENSION_MAX_PER_DAY,
            "can_extend_now": True,
        }

    @freeze_time(WEEKDAY_NOON_UTC)
    def test_extension_is_null_when_disabled(self, isolated_db, monkeypatch):
        monkeypatch.setattr(qs_module.config, "YOUTUBE_EXTENSION_QUEST_IDS", [])
        _seed()
        service = qs_module.InventoryService()
        _grant_item("son", 701)

        result = service.get_user_inventory("son")
        assert result["youtube_extension"] is None
        assert result["youtube_daily_limit_minutes"] == WEEKDAY_LIMIT_MINUTES
