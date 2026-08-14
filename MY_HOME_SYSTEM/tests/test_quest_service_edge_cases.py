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
import types
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

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
        assert row is None

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


class TestSyncMasterData:
    """
    GameSystem.sync_master_data() の未テストだった分岐:
    - quest_data モジュール不在時に HTTPException(500) を送出すること
    - 新規DBに欠けている旧カラム(role/reset_period/description)を
      ALTER TABLEで自動追加するレガシーマイグレーション分岐
    - マスタ側の対象idリストが空の場合に全件DELETEする分岐
    実際の外部サービス呼び出しは無く、quest_data はリポジトリ同梱の静的データなので
    実データを使っても決定的(deterministic)である。
    """

    def _column_names(self, cur, table):
        return {row["name"] for row in cur.execute(f"PRAGMA table_info({table})")}

    def test_raises_http_exception_when_quest_data_module_missing(self, isolated_db, monkeypatch):
        monkeypatch.setattr(quest_service_module, "quest_data", None)
        game_system = GameSystem()

        with pytest.raises(HTTPException) as exc_info:
            game_system.sync_master_data()

        assert exc_info.value.status_code == 500

    def test_adds_missing_legacy_columns_on_fresh_db(self, isolated_db):
        """role/reset_period/description は現在 core/migrations.py 側のマイグレーションで
        新規DB作成時に追加されるため、通常のisolated_dbには既に存在する。
        sync_master_data内の同名ALTER TABLE分岐は、それより前に作られた旧スキーマDBのための
        後方互換コードであり、その状態を意図的に再現(DROP COLUMN)してテストする。"""
        with common.get_db_cursor(commit=True) as cur:
            # role の一括UPDATEはUPDATE文なので、既存ユーザー行が無いと対象0件になってしまう。
            # 旧スキーマ時代からの既存ユーザーが居る状態を再現するため事前に行を用意する。
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class) VALUES "
                "('dad', 'Dad', 'Warrior'), ('son', 'Son', 'Novice')"
            )
            cur.execute("ALTER TABLE quest_users DROP COLUMN role")
            cur.execute("ALTER TABLE quest_master DROP COLUMN reset_period")
            cur.execute("ALTER TABLE reward_master DROP COLUMN description")

        with common.get_db_cursor() as cur:
            assert "role" not in self._column_names(cur, "quest_users")
            assert "reset_period" not in self._column_names(cur, "quest_master")
            assert "description" not in self._column_names(cur, "reward_master")

        game_system = GameSystem()
        result = game_system.sync_master_data()

        assert result["status"] == "synced"
        with common.get_db_cursor() as cur:
            assert "role" in self._column_names(cur, "quest_users")
            assert "reset_period" in self._column_names(cur, "quest_master")
            assert "description" in self._column_names(cur, "reward_master")

            dad_role = cur.execute(
                "SELECT role FROM quest_users WHERE user_id='dad'"
            ).fetchone()["role"]
            son_role = cur.execute(
                "SELECT role FROM quest_users WHERE user_id='son'"
            ).fetchone()["role"]
        assert dad_role == "role_adult"
        assert son_role == "role_child"

    def test_second_sync_call_skips_migration_without_error(self, isolated_db):
        """カラムが既に存在する2回目以降の呼び出しでは、
        マイグレーションtry節が例外なく成功し(ALTER TABLEは実行されない)、
        通常通り同期が完了すること。"""
        game_system = GameSystem()
        game_system.sync_master_data()

        result = game_system.sync_master_data()

        assert result["status"] == "synced"

    def test_empty_master_lists_delete_all_existing_rows(self, isolated_db, monkeypatch):
        """quest_data側の各マスタが空の場合、対象idによる絞り込みDELETEではなく
        テーブル全件を削除する分岐(quest_master/reward_masterそれぞれ)を通ること。"""
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
            cur.execute(
                "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (999, 'Stale Reward', 100)"
            )

        game_system = GameSystem()
        result = game_system.sync_master_data()

        assert result["status"] == "synced"
        with common.get_db_cursor() as cur:
            quest_count = cur.execute("SELECT COUNT(*) c FROM quest_master").fetchone()["c"]
            reward_count = cur.execute("SELECT COUNT(*) c FROM reward_master").fetchone()["c"]
        assert quest_count == 0
        assert reward_count == 0


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
