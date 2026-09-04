# MY_HOME_SYSTEM/tests/test_quest_service_edge_cases.py
"""
services/quest_service.py の未テストだった分岐を補うテスト:
- process_reject_quest の成功パス(親による却下)
- is_within_reset_period の daily/weekly/不明種別/不正日付文字列
- calculate_quest_boost のボーナス計算・上限クランプ
- get_all_view_data の対象者限定クエストにおけるボーナス計算分岐
"""
import datetime
import os
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
from services import notification_service, switchbot_service
from services import quest_service as quest_service_module
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
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
                "('daughter', 'Daughter', 'Novice', 1, 0, 0, 'role_child')"
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
        # 却下しても履歴行は削除されず、status='rejected' として残ること。
        # (以前はDELETEしていたため status='rejected' は実際には生成されず、
        # process_complete_quest のスパムチェック `status != 'rejected'` が
        # 常に成立する死に条件になっていた)
        assert row is not None
        assert row["status"] == "rejected"

    def test_reject_nonexistent_history_returns_404(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult')"
            )
        quest_service = QuestService()
        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_reject_quest("dad", 999999)
        assert exc_info.value.status_code == 404

    def test_reject_already_processed_history_returns_400(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
                "('daughter', 'Daughter', 'Novice', 1, 0, 0, 'role_child')"
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

    def test_rejected_history_is_excluded_from_family_chronicle_total_quests(self, isolated_db):
        """process_reject_quest が却下履歴を残す(DELETEではなくUPDATE)ようになった
        ことで、UserService.get_family_chronicle の totalQuests(COUNT(*) FROM
        quest_history)が却下された申請まで「達成したクエスト数」として誤集計しない
        よう明示的な除外が必要になった。"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
                "('daughter', 'Daughter', 'Novice', 1, 0, 0, 'role_child')"
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
                "(101, 'Test', 'daily', 10, 5)"
            )
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('daughter', 101, 'Test', 10, 5, '2026-01-01T00:00:00', 'approved')
            """)
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('daughter', 101, 'Test', 10, 5, '2026-01-02T00:00:00', 'pending')
            """)
            history_id = cur.lastrowid

        quest_service = QuestService()
        quest_service.process_reject_quest("dad", history_id)

        game_system = GameSystem()
        chronicle = game_system.user_service.get_family_chronicle()
        # approved 1件のみが「達成したクエスト数」に含まれ、却下された1件は含まれない
        assert chronicle["stats"]["totalQuests"] == 1


class TestGetLockUserIdsForHistory:
    """QuestService._get_lock_user_ids_for_history() のテスト(Issue #293)。

    process_approve_quest/process_reject_quest/process_cancel_questが個別に
    実装していた「対象履歴をpeekして兄妹連携クエストの相方を辿り、ロック対象
    ユーザーをまとめる」ロジックをこのヘルパーへ一元化した。挙動そのものは
    変更していないため、3つの呼び出し元それぞれの従来の契約(404を送出する/
    しない)を個別に確認する。
    """

    def _seed_users(self, cur):
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
            "('son', 'Son', 'Novice', 1, 0, 0, 'role_child'), "
            "('daughter', 'Daughter', 'Novice', 1, 0, 0, 'role_child')"
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
            "(101, 'Test', 'daily', 10, 5)"
        )

    def test_no_linked_history_returns_only_the_owner(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            self._seed_users(cur)
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('son', 101, 'Test', 10, 5, '2026-01-01T00:00:00', 'pending')
            """)
            history_id = cur.lastrowid

        quest_service = QuestService()
        lock_user_ids = quest_service._get_lock_user_ids_for_history(history_id)

        assert lock_user_ids == ['son']

    def test_linked_history_includes_the_partner(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            self._seed_users(cur)
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('daughter', 101, 'Test', 10, 5, '2026-01-01T00:00:00', 'pending')
            """)
            partner_history_id = cur.lastrowid
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status, linked_history_id)
                VALUES ('son', 101, 'Test', 10, 5, '2026-01-01T00:00:00', 'pending', ?)
            """, (partner_history_id,))
            history_id = cur.lastrowid

        quest_service = QuestService()
        lock_user_ids = quest_service._get_lock_user_ids_for_history(history_id)

        assert lock_user_ids == ['son', 'daughter']

    def test_missing_history_without_primary_user_id_raises_404(self, isolated_db):
        """process_approve_quest/process_reject_quest の従来の挙動: primary_user_idを
        指定しない呼び出しでは、存在しないhistory_idに対して404を送出する。"""
        with common.get_db_cursor(commit=True) as cur:
            self._seed_users(cur)

        quest_service = QuestService()
        with pytest.raises(HTTPException) as exc_info:
            quest_service._get_lock_user_ids_for_history(999999)
        assert exc_info.value.status_code == 404

    def test_missing_history_with_primary_user_id_does_not_raise(self, isolated_db):
        """process_cancel_questの従来の挙動: primary_user_idを指定した場合、
        history_idの存在確認自体は_process_cancel_quest_locked側に委ねるため、
        ここでは404を送出せずprimary_user_idのみを返す。"""
        with common.get_db_cursor(commit=True) as cur:
            self._seed_users(cur)

        quest_service = QuestService()
        lock_user_ids = quest_service._get_lock_user_ids_for_history(999999, primary_user_id='son')

        assert lock_user_ids == ['son']


class TestProcessRejectQuestConcurrentWithApprove:
    """Issue #228の回帰テスト。

    process_reject_quest は以前ロックを一切取得していなかったため、同一
    history_idに対する承認と却下がほぼ同時に実行されると、承認側が先に
    quest_usersへgold/expを加算・コミットした後に却下のUPDATEがコミットされ、
    quest_history.statusは'rejected'になるのに付与済みの報酬は一切ロール
    バックされないという不整合が実機で確認されていた。process_reject_quest を
    process_approve_quest と同じユーザー単位ロックに参加させることで、
    この2つの操作を直列化し、上記の不整合を防ぐ。
    """

    def test_reject_loses_race_cleanly_when_approve_completes_first(self, isolated_db, monkeypatch):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
                "('daughter', 'Daughter', 'Novice', 1, 0, 100, 'role_child')"
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
                "(101, 'Test', 'daily', 50, 50)"
            )
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('daughter', 101, 'Test', 50, 50, '2026-01-01T00:00:00', 'pending')
            """)
            history_id = cur.lastrowid

        quest_service = QuestService()

        # 承認処理がquest_usersへの報酬加算(_apply_quest_rewards)後、コミット前に
        # 少し待機するようにする。承認はロック取得後この時間だけロックを保持し
        # 続けるため、その間に却下側がロック取得を試みてブロックされることを
        # 利用し、確実に「承認が先にロックを取得して完走し終えてから却下が
        # 動き出す」という、Issueが報告した不整合が起きうる順序を再現する。
        original_apply_rewards = quest_service._apply_quest_rewards

        def _slow_apply_rewards(cur, user, quest, now_iso, history_id=None, override_rewards=None):
            result = original_apply_rewards(
                cur, user, quest, now_iso, history_id=history_id, override_rewards=override_rewards
            )
            time.sleep(0.2)
            return result

        monkeypatch.setattr(quest_service, "_apply_quest_rewards", _slow_apply_rewards)

        results = {}
        errors = {}

        def _approve():
            try:
                results["approve"] = quest_service.process_approve_quest("dad", history_id)
            except Exception as e:  # noqa: BLE001 - スレッド内例外を主スレッドへ伝える
                errors["approve"] = e

        def _reject():
            try:
                results["reject"] = quest_service.process_reject_quest("dad", history_id)
            except Exception as e:  # noqa: BLE001
                errors["reject"] = e

        t_approve = threading.Thread(target=_approve)
        t_reject = threading.Thread(target=_reject)
        t_approve.start()
        time.sleep(0.05)  # 承認が先にロックを取得できるよう、わずかに先行させる
        t_reject.start()
        t_approve.join(timeout=5)
        t_reject.join(timeout=5)

        assert not t_approve.is_alive() and not t_reject.is_alive()

        with common.get_db_cursor() as cur:
            hist = cur.execute("SELECT * FROM quest_history WHERE id=?", (history_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id='daughter'").fetchone()

        # 承認が確定した場合、却下はロック取得後の再チェックで「承認待ちでは
        # ありません」(400)として弾かれ、statusは'approved'のまま
        # (以前のバグのように'rejected'へ上書きされない)こと。
        assert "approve" not in errors
        assert "reject" in errors
        assert isinstance(errors["reject"], HTTPException)
        assert errors["reject"].status_code == 400

        assert hist["status"] == "approved"
        # 報酬が実際に加算されており、かつ却下によってロールバックされていないこと
        # (statusが'rejected'なのに報酬が残る、という以前のバグの逆に、
        # statusが'approved'であることと報酬が加算済みであることが一致している)
        assert user["gold"] == 150


class TestIsWithinResetPeriod:
    @pytest.fixture(autouse=True)
    def _frozen_now(self):
        # C-L3 (Issue #414): JST の日付/週境界をまたぐ数ミリ秒の窓で不安定になるため、
        # 現在時刻を固定する(2026-09-02(水) 10:00 JST = 01:00 UTC)。
        # freezegun のクラスデコレータは pytest 形式の setup_method(self) と
        # 相性が悪い(余分な引数を渡す)ため、autouse フィクスチャで包む。
        with freeze_time("2026-09-02 01:00:00"):
            yield

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

    def test_naive_timestamp_late_at_night_is_interpreted_as_jst_not_utc(self):
        """M-1-4回帰防止: tzinfoの無いレガシー完了時刻は、保存規約
        (common.get_now_iso)に合わせてJSTとして記録されているとみなす。
        以前はUTCとみなして変換していたため、日付境界付近(夜遅く)の
        naiveタイムスタンプが日付跨ぎで誤判定されていた
        (23:00をUTCとみなして+9hすると翌日05:00になってしまう)。"""
        today_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
        assert self.quest_service.is_within_reset_period(f"{today_jst}T23:00:00", "daily") is True


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
        """Issue #176回帰防止: このテストは以前 datetime.datetime.now()(naive、
        OSローカル時刻)で3日前を計算してseedしていたが、calculate_quest_boostは
        JST基準(datetime.datetime.now(JST))で「今日」を判定するため、CI実行環境
        (UTC)かつ実行時刻がJST 0時〜9時に相当する時間帯(UTC 15時〜24時)だと、
        OSローカルの日付とJSTの日付がずれてdays_diffが期待値と食い違い、決定的に
        失敗していた。calculate_quest_boostと同じJST基準のtimezone-awareな
        「今」を使ってseedすることで、実行環境・実行時刻に依存しないようにする。"""
        _seed_user_and_quest(gold_gain=100, exp_gain=100)
        JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')
        three_days_ago = (datetime.datetime.now(JST) - datetime.timedelta(days=3)).isoformat()
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

    def test_missed_days_is_computed_in_jst_not_os_local_timezone(self, isolated_db):
        """Issue #108回帰防止: is_within_reset_periodと異なり、以前は
        datetime.datetime.now()(OSローカル時刻、tzinfoなし)を「今日」の
        基準にしていたため、サーバーOSのタイムゾーンがJST以外だと
        JST 0時〜9時の間の判定でdays_diffが1小さくなっていた。

        サーバーOSがUTC(tz_offset=0)の状態で、JST基準では
        2026-08-20 03:00(=UTC 2026-08-19 18:00)を「現在時刻」として固定する。
        completed_atはJST 2026-08-17 12:00(3日前)。
        JST基準で正しく判定されれば days_diff=3 -> missed_days=2 -> ratio=0.2。
        OSローカル(UTC)の日付(08-19)を誤って使うと days_diff=2 ->
        missed_days=1 -> ratio=0.1 になってしまう(修正前の不具合)。
        """
        _seed_user_and_quest(gold_gain=100, exp_gain=100)
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES ('dad', 101, 'DailyQuest', 100, 100, '2026-08-17T12:00:00', 'approved')
            """)
        with freeze_time("2026-08-19 18:00:00", tz_offset=0):
            with common.get_db_cursor() as cur:
                quest = cur.execute("SELECT * FROM quest_master WHERE quest_id=101").fetchone()
                boost = self.quest_service.calculate_quest_boost(cur, "dad", quest)
        assert boost == {"gold": 20, "exp": 20}


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


class TestGetAllViewDataUsersOrder:
    """
    回帰防止: "SELECT * FROM quest_users" にORDER BY句が無いと、user_idが
    TEXT PRIMARY KEYであるためSQLiteが主キーのアルファベット順
    (dad, daughter, mom, son)で行を返すことがある。family-quest側の App.tsx は
    users[currentUserIdx] という配列インデックスでタブと現在のユーザーを
    対応づけており、quest_data.USERSの宣言順(dad, mom, son, daughter)と一致しない
    順序で返ると、タブの位置と実際に表示される家族が入れ替わってしまう
    (例: 「ともや」のタブに寝かしつけ(mom/dad向け)クエストが表示される)。
    get_all_view_data は quest_data.USERS の宣言順に並べ替えて返すべき。
    """

    def test_users_are_ordered_by_quest_data_users_declaration_order(self, isolated_db):
        # あえて quest_data.USERS の宣言順(dad, mom, son, daughter)とは異なる
        # 順序でINSERTし、かつ user_id のアルファベット順(dad, daughter, mom, son)
        # とも異なる順序にすることで、DBの内部的な返却順に依存していないことを確認する。
        with common.get_db_cursor(commit=True) as cur:
            for user_id, name in [
                ("son", "Son"), ("daughter", "Daughter"), ("dad", "Dad"), ("mom", "Mom"),
            ]:
                cur.execute(
                    "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
                    "(?, ?, 'Job', 1, 0, 0)",
                    (user_id, name),
                )

        game_system = GameSystem()
        data = game_system.get_all_view_data()

        assert [u["user_id"] for u in data["users"]] == ["dad", "mom", "son", "daughter"]


class TestGetAllViewDataSharedQuestBoostViewer:
    """
    quest_master.target_user は実在の quest_users.user_id (例:'dad')の他に
    'siblings' のようなグループ指定も取りうる。以前は target_user をそのまま
    calculate_quest_boost へ user_id として渡していたため、'siblings' 指定の
    共有クエストでは quest_history に一致するuser_id行が存在せず、実際の完了
    履歴に関わらずボーナスが常に0になっていた(実害はないが意味が誤り)。
    viewer_user_id(閲覧中のユーザー)を渡した場合は、そのユーザーの履歴を
    代表として使うことを検証する。
    """
    def _seed_shared_quest_with_history(self, cur):
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('son', 'Son', 'Novice', 1, 0, 0, 'role_child'), "
            "('daughter', 'Daughter', 'Novice', 1, 0, 0, 'role_child')"
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) "
            "VALUES (101, 'Shared Quest', 'daily', 'siblings', 100, 100)"
        )
        # calculate_quest_boostはJST基準(datetime.datetime.now(JST))で「今日」を
        # 判定するため、seed側もnaiveなOSローカル時刻ではなく同じJST基準の
        # timezone-awareな「今」を使う(Issue #176回帰防止)。
        JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')
        three_days_ago = (datetime.datetime.now(JST) - datetime.timedelta(days=3)).isoformat()
        cur.execute("""
            INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
            VALUES ('son', 101, 'Shared Quest', 100, 100, ?, 'approved')
        """, (three_days_ago,))

    def test_boost_is_always_zero_without_a_viewer(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            self._seed_shared_quest_with_history(cur)

        game_system = GameSystem()
        data = game_system.get_all_view_data()

        quest = next(q for q in data["quests"] if q["quest_id"] == 101)
        assert quest["bonus_gold"] == 0
        assert quest["bonus_exp"] == 0

    def test_boost_uses_viewers_own_history_when_target_user_is_not_a_real_user(self, isolated_db):
        """Issue #176回帰防止: _seed_shared_quest_with_historyは以前naiveなOS
        ローカル時刻で3日前をseedしており、calculate_quest_boostのJST基準の
        「今日」判定とずれてCI実行環境(UTC)かつ実行時刻がJST 0時〜9時に相当する
        時間帯だとdays_diffが決定的にずれていた。_seed_shared_quest_with_history
        自体をJST基準のtimezone-awareな「今」でseedするよう修正済み。"""
        with common.get_db_cursor(commit=True) as cur:
            self._seed_shared_quest_with_history(cur)

        game_system = GameSystem()
        data = game_system.get_all_view_data(viewer_user_id="son")

        # days_diff=3 -> missed_days=2 -> bonus_ratio=0.2 -> 100 * 0.2 = 20
        quest = next(q for q in data["quests"] if q["quest_id"] == 101)
        assert quest["bonus_gold"] == 20
        assert quest["bonus_exp"] == 20


class TestSyncMasterData:
    """
    GameSystem.sync_master_data() の未テストだった分岐:
    - quest_data モジュール不在時に HTTPException(500) を送出すること
    - reward_master側の対象idリストが空の場合に全件DELETEする分岐
    - quest_master側は対象idリストが空でも全件DELETEしない安全弁(Issue #242)
    実際の外部サービス呼び出しは無く、quest_data はリポジトリ同梱の静的データなので
    実データを使っても決定的(deterministic)である。

    Issue #330: 以前ここでテストしていた「SELECTを試して失敗したらALTER TABLE」式の
    レガシー実行時マイグレーション分岐(role/reset_period/description)は完全退役した。
    これらのカラムは migrations/ 配下(0000ベースライン+0001〜0003等)が供給し、
    sync_master_data はカラムの存在を前提とする(存在保証はマイグレーション経路の
    テスト test_migrations.py / test_empty_db_e2e.py 側で検証する)。
    """

    def _column_names(self, cur, table):
        return {row["name"] for row in cur.execute(f"PRAGMA table_info({table})")}

    def test_raises_http_exception_when_quest_data_module_missing(self, isolated_db, monkeypatch):
        monkeypatch.setattr(quest_service_module, "quest_data", None)
        game_system = GameSystem()

        with pytest.raises(HTTPException) as exc_info:
            game_system.sync_master_data()

        assert exc_info.value.status_code == 500

    def test_migration_provided_columns_exist_and_sync_populates_roles(self, isolated_db):
        """Issue #330の回帰テスト: レガシー実行時ALTERを退役させた後も、
        migrations経由で構築されたDB(isolated_db)には role/reset_period/description が
        最初から存在し、sync_master_data がrole値を正しく投入できること。"""
        with common.get_db_cursor() as cur:
            assert "role" in self._column_names(cur, "quest_users")
            assert "reset_period" in self._column_names(cur, "quest_master")
            assert "description" in self._column_names(cur, "reward_master")

        game_system = GameSystem()
        result = game_system.sync_master_data()

        assert result["status"] == "synced"
        with common.get_db_cursor() as cur:
            dad_role = cur.execute(
                "SELECT role FROM quest_users WHERE user_id='dad'"
            ).fetchone()["role"]
        assert dad_role == "role_adult"

    def test_second_sync_call_succeeds_without_error(self, isolated_db):
        """2回目以降の呼び出しでも(UPSERTのみで)通常通り同期が完了すること。"""
        game_system = GameSystem()
        game_system.sync_master_data()

        result = game_system.sync_master_data()

        assert result["status"] == "synced"

    def test_empty_reward_master_list_deletes_all_existing_rows(self, isolated_db, monkeypatch):
        """quest_data.REWARDSが空の場合、reward_masterはuser_inventoryの参照が
        残っていない限りテーブル全件を削除する既存の分岐を通ること(この安全弁は
        Issue #242以前から存在しており、quest_master側とは異なり据え置き)。"""
        fake_quest_data = types.SimpleNamespace(
            USERS=[{"user_id": "dad", "name": "Dad", "job_class": "Warrior"}],
            QUESTS=[],
            REWARDS=[],
        )
        monkeypatch.setattr(quest_service_module, "quest_data", fake_quest_data)
        monkeypatch.setattr(quest_service_module.importlib, "reload", lambda module: None)

        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (999, 'Stale Reward', 100)"
            )

        game_system = GameSystem()
        result = game_system.sync_master_data()

        assert result["status"] == "synced"
        with common.get_db_cursor() as cur:
            reward_count = cur.execute("SELECT COUNT(*) c FROM reward_master").fetchone()["c"]
        assert reward_count == 0

    def test_empty_quest_master_list_skips_deletion_and_preserves_existing_rows(
        self, isolated_db, monkeypatch
    ):
        """Issue #242の回帰テスト: 以前はquest_data.QUESTSが空の場合、
        quest_masterに対しても対象idによる絞り込みDELETEではなくテーブル全件を
        削除する分岐を通っていた。reward_master側にはuser_inventory参照チェック
        という安全弁があるのに対し、quest_master側には同種の安全弁が一切なく、
        QUESTSが空になった瞬間(コーディングミス等)に無条件で全クエストマスタが
        消えていた。修正後は削除自体をスキップし、既存行を保持する。"""
        fake_quest_data = types.SimpleNamespace(
            USERS=[{"user_id": "dad", "name": "Dad", "job_class": "Warrior"}],
            QUESTS=[],
            REWARDS=[],
        )
        monkeypatch.setattr(quest_service_module, "quest_data", fake_quest_data)
        monkeypatch.setattr(quest_service_module.importlib, "reload", lambda module: None)

        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) "
                "VALUES (999, 'Stale Quest', 'daily', 1, 1)"
            )

        game_system = GameSystem()
        with patch.object(quest_service_module, "logger") as mock_logger:
            result = game_system.sync_master_data()

        assert result["status"] == "synced"
        with common.get_db_cursor() as cur:
            quest_count = cur.execute("SELECT COUNT(*) c FROM quest_master").fetchone()["c"]
        assert quest_count == 1, "quest_data.QUESTSが空でもquest_masterの既存行を無条件削除してはならない"
        mock_logger.warning.assert_any_call(
            "⚠️ quest_data.QUESTSが空のため、quest_masterへの全削除操作を"
            "スキップしました(意図しない全消去を防ぐための安全弁)。"
        )

    def test_reward_still_owned_by_user_inventory_is_not_deleted(self, isolated_db, monkeypatch):
        """M-1-2: user_inventory(reward_master(reward_id)へのFK)が参照している報酬を
        マスタから削除しようとすると、以前はIntegrityErrorでsync_master_data全体が
        失敗していた。参照が残っている報酬は削除をスキップし、sync自体は成功すること。"""
        fake_quest_data = types.SimpleNamespace(
            USERS=[{"user_id": "dad", "name": "Dad", "job_class": "Warrior"}],
            QUESTS=[],
            REWARDS=[],  # マスタからは全報酬を削除する想定
        )
        monkeypatch.setattr(quest_service_module, "quest_data", fake_quest_data)
        monkeypatch.setattr(quest_service_module.importlib, "reload", lambda module: None)

        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (999, 'Owned Reward', 100)"
            )
            cur.execute(
                "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
                "VALUES ('dad', 999, 'owned', ?)",
                (common.get_now_iso(),),
            )

        game_system = GameSystem()
        result = game_system.sync_master_data()

        assert result["status"] == "synced"
        with common.get_db_cursor() as cur:
            reward_row = cur.execute(
                "SELECT reward_id FROM reward_master WHERE reward_id = 999"
            ).fetchone()
            inventory_row = cur.execute(
                "SELECT id FROM user_inventory WHERE reward_id = 999"
            ).fetchone()
        assert reward_row is not None, "参照が残っている報酬は削除されずに残ること"
        assert inventory_row is not None


class TestTriggerTvUnlock:
    """
    QuestService._trigger_tv_unlock() のテスト。
    実装は threading.Thread(daemon=True) でバックグラウンド実行するため、
    そのままでは実スレッドが絡みテストが非決定的(flaky)になる。
    threading.Thread.start を threading.Thread.run に差し替え、
    start()呼び出し時にターゲット関数を「同じスレッドで同期的に」実行させることで、
    実スレッド生成を避けつつ決定的にテストする。
    switchbot_service/notification_serviceは全てモックし、実際のAPI呼び出しは行わない。
    """

    @pytest.fixture(autouse=True)
    def _run_background_thread_synchronously(self, monkeypatch):
        monkeypatch.setattr(threading.Thread, "start", threading.Thread.run)

    def test_success_status_code_does_not_notify_parents(self, monkeypatch):
        monkeypatch.setattr(
            switchbot_service, "send_device_command", MagicMock(return_value={"statusCode": 100})
        )
        mock_send_push = MagicMock()
        monkeypatch.setattr(notification_service, "send_push", mock_send_push)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "group123")

        quest_service = QuestService()
        quest_service._trigger_tv_unlock(quest_id=101)

        mock_send_push.assert_not_called()

    def test_non_success_status_code_notifies_parents_group(self, monkeypatch):
        monkeypatch.setattr(
            switchbot_service,
            "send_device_command",
            MagicMock(return_value={"statusCode": 190, "message": "Invalid auth"}),
        )
        mock_send_push = MagicMock()
        monkeypatch.setattr(notification_service, "send_push", mock_send_push)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "group123")

        quest_service = QuestService()
        quest_service._trigger_tv_unlock(quest_id=101)

        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args.kwargs
        assert call_kwargs["user_id"] == "group123"
        assert "失敗" in call_kwargs["messages"][0]["text"]

    def test_no_response_from_switchbot_is_treated_as_failure(self, monkeypatch):
        """switchbot_service側がFail-Soft設計上Noneを返すケース(未設定/通信失敗)でも
        例外として扱われ、親グループへの通知分岐に入ること。"""
        monkeypatch.setattr(switchbot_service, "send_device_command", MagicMock(return_value=None))
        mock_send_push = MagicMock()
        monkeypatch.setattr(notification_service, "send_push", mock_send_push)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "group123")

        quest_service = QuestService()
        quest_service._trigger_tv_unlock(quest_id=101)

        mock_send_push.assert_called_once()

    def test_failure_without_parents_group_configured_skips_notification(self, monkeypatch):
        """LINE_PARENTS_GROUP_ID が未設定の場合は、失敗しても通知を試みない
        (通知失敗で二重に例外を出さないためのFail-Soft分岐)。"""
        monkeypatch.setattr(
            switchbot_service, "send_device_command", MagicMock(return_value={"statusCode": 190})
        )
        mock_send_push = MagicMock()
        monkeypatch.setattr(notification_service, "send_push", mock_send_push)
        monkeypatch.setattr(config, "LINE_PARENTS_GROUP_ID", "")

        quest_service = QuestService()
        quest_service._trigger_tv_unlock(quest_id=101)

        mock_send_push.assert_not_called()

    def test_does_not_spawn_a_real_background_thread(self, monkeypatch):
        """daemon=Trueのスレッドとして起動されることの回帰確認(実装の意図を固定する)。"""
        monkeypatch.setattr(
            switchbot_service, "send_device_command", MagicMock(return_value={"statusCode": 100})
        )
        captured_threads = []
        real_thread_cls = threading.Thread

        class _CapturingThread(real_thread_cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured_threads.append(self)

        monkeypatch.setattr(threading, "Thread", _CapturingThread)

        quest_service = QuestService()
        quest_service._trigger_tv_unlock(quest_id=101)

        assert len(captured_threads) == 1
        assert captured_threads[0].daemon is True


class TestProcessApproveQuestWithDeletedMasterQuest:
    """
    M-1-1: sync_master_data のDELETE(マスタから削除されたクエスト)後も
    quest_history の pending 行は残るため、そのクエストを承認しようとすると
    quest_master 側の行が見つからず quest=None になる。
    process_approve_quest はこの状態でも quest['quest_id'] のsubscriptで
    落ちず(TypeError→500にならず)、承認自体は成立すること。
    """

    def _seed_child_and_adult(self, cur):
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
            "('son', 'Son', 'Novice', 1, 0, 0, 'role_child')"
        )

    def test_approve_succeeds_when_quest_was_removed_from_master(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            self._seed_child_and_adult(cur)
            # quest_masterには一切登録せず、削除済みクエストのpending履歴のみを再現する
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, "
                "completed_at, status) VALUES ('son', 9999, 'DeletedQuest', 10, 20, ?, 'pending')",
                (common.get_now_iso(),),
            )
            history_id = cur.lastrowid

        quest_service = QuestService()
        result = quest_service.process_approve_quest("dad", history_id)

        assert result["status"] == "success"
        with common.get_db_cursor() as cur:
            hist_after = cur.execute(
                "SELECT status FROM quest_history WHERE id = ?", (history_id,)
            ).fetchone()
            son_gold = cur.execute(
                "SELECT gold FROM quest_users WHERE user_id = 'son'"
            ).fetchone()["gold"]
        assert hist_after["status"] == "approved"
        assert son_gold == 20


class TestFilterActiveQuestsDateParseErrorLogging:
    """
    Low: filter_active_quests の日付パースエラー時のログが、実在しない 'id' キーを
    参照していたため常に None を出力していた("Date parse error for quest None: ...")。
    実際のキーは 'quest_id'。
    """

    def test_date_parse_error_log_includes_the_actual_quest_id(self, monkeypatch):
        quest_service = QuestService()
        bad_quest = {
            "quest_id": 4242,
            "quest_type": "limited",
            "start_date": "not-a-date",
            "end_date": None,
            "target_user": "all",
            "day_of_week": None,
        }

        with patch.object(quest_service_module, "logger") as mock_logger:
            result = quest_service.filter_active_quests([bad_quest])

        assert result == []  # パースエラー時は除外される(既存挙動)
        mock_logger.warning.assert_called_once()
        logged_message = mock_logger.warning.call_args[0][0]
        assert "4242" in logged_message, (
            f"log message should reference the quest_id (4242), got: {logged_message!r}"
        )
        assert "None" not in logged_message


class TestIsQuestCurrentlyActiveRandomOccurrenceChanceNone:
    """Issue #241の回帰テスト: quest_type='random'のクエストでoccurrence_chanceが
    Noneの場合、random.Random(seed).random() > None の比較でTypeErrorになり、
    filter_active_quests/process_complete_quest側の検証が例外で落ちていた
    (start_date/end_dateパース失敗のValueErrorのみ捕捉しており、この経路は無防備だった)。
    occurrence_chanceがNoneの場合はDBスキーマ(DEFAULT 1.0)・models/quest.pyの
    既定値(Optional[float] = 1.0)と同じ「常に出現」扱いにする。"""

    def _make_random_quest(self, occurrence_chance):
        return {
            "quest_id": 9001,
            "quest_type": "random",
            "occurrence_chance": occurrence_chance,
            "start_time": None,
            "end_time": None,
            "day_of_week": None,
            "target_user": "all",
            "icon_key": "🎲",
        }

    def test_none_occurrence_chance_does_not_raise_type_error(self):
        quest_service = QuestService()
        quest = self._make_random_quest(None)

        # 例外を送出せず完走すること自体が回帰確認の対象
        result = quest_service._is_quest_currently_active(quest)

        assert result is True, "occurrence_chance=Noneは既定値1.0(常に出現)として扱うべき"

    def test_filter_active_quests_does_not_raise_for_random_quest_with_none_chance(self):
        quest_service = QuestService()
        quest = self._make_random_quest(None)

        result = quest_service.filter_active_quests([quest])

        assert len(result) == 1

    def test_explicit_chance_still_behaves_as_before(self):
        quest_service = QuestService()
        quest_never = self._make_random_quest(0.0)

        assert quest_service._is_quest_currently_active(quest_never) is False, (
            "occurrence_chance=0.0(既存の明示的な値)は従来通り出現しないこと"
        )


class TestSyncMasterDataSyncsRewardTarget:
    """
    Issue #95: sync_master_data の reward_master への UPSERT が target 列を
    含んでいなかったため、対象者制限(target='children'/'adults' 等)がDBへ
    一切反映されず、全報酬が全ユーザーに表示・購入可能になっていた。
    """

    def test_reward_target_is_synced_to_db(self, isolated_db):
        game_system = GameSystem()
        game_system.sync_master_data()

        with common.get_db_cursor() as cur:
            children_reward = cur.execute(
                "SELECT target FROM reward_master WHERE reward_id = 10"
            ).fetchone()
            adults_reward = cur.execute(
                "SELECT target FROM reward_master WHERE reward_id = 120"
            ).fetchone()

        assert children_reward["target"] == "children"
        assert adults_reward["target"] == "adults"

    def test_reward_target_is_updated_on_resync(self, isolated_db):
        game_system = GameSystem()
        game_system.sync_master_data()

        with common.get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE reward_master SET target = 'all' WHERE reward_id = 120")

        game_system.sync_master_data()

        with common.get_db_cursor() as cur:
            adults_reward = cur.execute(
                "SELECT target FROM reward_master WHERE reward_id = 120"
            ).fetchone()

        assert adults_reward["target"] == "adults"


class TestProcessPurchaseRewardTargetRestriction:
    """
    Issue #95: 対象者制限のある報酬(target != 'all')は、フロントの表示フィルタだけ
    でなくサーバー側の購入処理でも制限されるべき。API直叩きによるバイパスを防ぐ。
    """

    def _seed_users(self, cur):
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 10000, 'role_adult'), "
            "('son', 'Son', 'Novice', 1, 0, 10000, 'role_child')"
        )

    def _seed_reward(self, cur, target):
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) VALUES "
            "(500, 'Restricted Reward', 100, ?)",
            (target,),
        )

    def test_child_cannot_purchase_adults_only_reward(self, isolated_db):
        from services.quest_service import ShopService

        with common.get_db_cursor(commit=True) as cur:
            self._seed_users(cur)
            self._seed_reward(cur, "adults")

        with pytest.raises(HTTPException) as exc_info:
            ShopService().process_purchase_reward("son", 500)

        assert exc_info.value.status_code == 403

    def test_adult_can_purchase_adults_only_reward(self, isolated_db):
        from services.quest_service import ShopService

        with common.get_db_cursor(commit=True) as cur:
            self._seed_users(cur)
            self._seed_reward(cur, "adults")

        result = ShopService().process_purchase_reward("dad", 500)

        assert result["status"] == "purchased"

    def test_adult_cannot_purchase_children_only_reward(self, isolated_db):
        from services.quest_service import ShopService

        with common.get_db_cursor(commit=True) as cur:
            self._seed_users(cur)
            self._seed_reward(cur, "children")

        with pytest.raises(HTTPException) as exc_info:
            ShopService().process_purchase_reward("dad", 500)

        assert exc_info.value.status_code == 403

    def test_all_target_reward_is_purchasable_by_anyone(self, isolated_db):
        from services.quest_service import ShopService

        with common.get_db_cursor(commit=True) as cur:
            self._seed_users(cur)
            self._seed_reward(cur, "all")

        result = ShopService().process_purchase_reward("son", 500)

        assert result["status"] == "purchased"
