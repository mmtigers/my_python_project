# MY_HOME_SYSTEM/tests/test_line_service.py
"""
services/line_service.py (LINEボット経由のDB記録・ステータス応答生成)のテスト。
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
from services import line_service


@pytest.mark.asyncio
class TestLogChildHealth:
    async def test_saves_record_and_returns_confirmation_message(self, isolated_db):
        result = await line_service.log_child_health("U1", "太郎", "daughter", "元気")
        assert "daughter" in result.text or "元気" in result.text

        with common.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_CHILD} WHERE child_name='daughter'"
            ).fetchone()
        assert row is not None
        assert row["condition"] == "元気"


@pytest.mark.asyncio
class TestLogFoodRecord:
    async def test_saves_record_with_manual_flag_suffix(self, isolated_db):
        result = await line_service.log_food_record("U1", "太郎", "自炊", "カレー", is_manual=True)
        assert "カレー" in result.text

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_FOOD}").fetchone()
        assert "(手入力)" in row["menu_category"]


@pytest.mark.asyncio
class TestLogOhayo:
    async def test_does_not_raise_even_if_target_table_is_absent(self, isolated_db):
        """communication_logsテーブルが無くてもsave_log_asyncがFail-Softなので例外にならないこと"""
        await line_service.log_ohayo("U1", "太郎", "おはよう！", "おはよ")


class TestGetDailyHealthSummaryText:
    def test_reports_unrecorded_for_all_members_when_no_data(self, isolated_db):
        text = line_service.get_daily_health_summary_text()
        for member in config.FAMILY_SETTINGS["members"]:
            assert f"{member}: (未記録)" in text

    def test_shows_recorded_condition_with_healthy_icon(self, isolated_db):
        member = config.FAMILY_SETTINGS["members"][0]
        today = line_service.get_today_date_str()
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_CHILD} (child_name, condition, timestamp) VALUES (?, ?, ?)",
                (member, "元気いっぱい", f"{today}T09:00:00"),
            )
        text = line_service.get_daily_health_summary_text()
        assert "✅" in text
        assert "元気いっぱい" in text

    def test_shows_warning_icon_for_unwell_condition(self, isolated_db):
        member = config.FAMILY_SETTINGS["members"][0]
        today = line_service.get_today_date_str()
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_CHILD} (child_name, condition, timestamp) VALUES (?, ?, ?)",
                (member, "熱がある", f"{today}T09:00:00"),
            )
        text = line_service.get_daily_health_summary_text()
        assert "⚠️" in text


@pytest.mark.asyncio
class TestGetUserStatusMessage:
    async def test_returns_status_for_existing_user(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
                "('dad', 'Dad', 'Warrior', 3, 50, 200)"
            )
        result = await line_service.get_user_status_message("dad")
        assert "Dad" in result.text
        assert "Lv. 3" in result.text
        assert "200 G" in result.text

    async def test_returns_warning_for_unknown_user(self, isolated_db):
        result = await line_service.get_user_status_message("nobody")
        assert "見つかりません" in result.text


@pytest.mark.asyncio
class TestGetActiveQuestsMessage:
    async def test_lists_quests_available_to_all(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) "
                "VALUES (101, 'お皿洗い', 'daily', 'all', 10, 5)"
            )
        result = await line_service.get_active_quests_message("dad")
        assert "お皿洗い" in result.text

    async def test_returns_no_quest_message_when_empty(self, isolated_db):
        result = await line_service.get_active_quests_message("dad")
        assert "ありません" in result.text


@pytest.mark.asyncio
class TestProcessApprovalCommand:
    async def _seed_pending_history(self):
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
            return cur.lastrowid

    async def test_approve_command_grants_rewards(self, isolated_db):
        history_id = await self._seed_pending_history()
        result = await line_service.process_approval_command("dad", f"承認 {history_id}")
        assert "承認しました" in result.text
        assert "EXP" in result.text

    async def test_reject_command_removes_history(self, isolated_db):
        history_id = await self._seed_pending_history()
        result = await line_service.process_approval_command("dad", f"却下 {history_id}")
        assert "却下しました" in result.text

    async def test_non_parent_approver_returns_permission_error_message(self, isolated_db):
        history_id = await self._seed_pending_history()
        result = await line_service.process_approval_command("daughter", f"承認 {history_id}")
        assert "エラー" in result.text

    async def test_missing_id_returns_usage_hint(self, isolated_db):
        result = await line_service.process_approval_command("dad", "承認")
        assert "IDを指定してください" in result.text

    async def test_non_numeric_id_returns_format_error(self, isolated_db):
        result = await line_service.process_approval_command("dad", "承認 abc")
        assert "数字で指定してください" in result.text

    async def test_unrecognized_command_returns_unknown_message(self, isolated_db):
        result = await line_service.process_approval_command("dad", "こんにちは 123")
        assert "不明なコマンド" in result.text
