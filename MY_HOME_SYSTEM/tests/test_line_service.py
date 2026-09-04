# MY_HOME_SYSTEM/tests/test_line_service.py
"""
services/line_service.py (LINEボット経由のDB記録・ステータス応答生成)のテスト。
"""
import os
import sys

import pytest
from unittest.mock import AsyncMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
from services import line_service
from linebot.v3.messaging import TextMessage


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


# 保守性(#410): log_ohayo / get_daily_health_summary_text は本番から未参照の
# 未使用関数だったため services/line_service.py から削除した(それに伴い、
# ここにあった TestLogOhayo / TestGetDailyHealthSummaryText も削除)。


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

    async def test_siblings_target_quest_is_shown_to_child_user(self, isolated_db):
        """Issue #109回帰防止: target='siblings'は特定のuser_idと一致しない
        ため、旧実装(target != 'all' and target != user_id)では兄妹連携
        クエストが常にスキップされ、どの子供にも表示されなかった。"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('son', 'Son', 'Novice', 1, 0, 0, 'role_child')"
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) "
                "VALUES (1040, 'いっしょにおかたづけ', 'daily', 'siblings', 40, 30)"
            )
        result = await line_service.get_active_quests_message("son")
        assert "いっしょにおかたづけ" in result.text

    async def test_siblings_target_quest_is_hidden_from_adult_user(self, isolated_db):
        """兄妹連携クエストの対象は子供(role_child)全員であり、親には
        表示されないこと(家族画面側の対象判定と同じ意味付け)。"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult')"
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, target_user, exp_gain, gold_gain) "
                "VALUES (1040, 'いっしょにおかたづけ', 'daily', 'siblings', 40, 30)"
            )
        result = await line_service.get_active_quests_message("dad")
        assert "いっしょにおかたづけ" not in result.text


@pytest.mark.asyncio
class TestProcessApprovalCommand:
    async def _seed_pending_history(self):
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

    async def test_no_boss_effect_reference_remains(self):
        """Issue #181の回帰テスト: quest_service.process_approve_questの戻り値dictに
        bossEffectキーは存在せず(2026年8月のリファクタリングでボス戦機能は削除済み、
        CLAUDE.md参照)、`res.get('bossEffect')`は常にNoneとなる死に分岐だった。
        CLAUDE.mdの規約(削除済み機能を復活/参照しないこと)の回帰防止として、
        ソース上にこの参照が再度混入していないことを直接確認する。"""
        import inspect
        source = inspect.getsource(line_service.process_approval_command)
        assert "bossEffect" not in source


@pytest.mark.asyncio
class TestSaveFailureIsReportedToUser:
    """Issue #373: save_log_async は Fail-Soft で False を返すが、log_child_health /
    log_food_record は戻り値を無視して成功メッセージを返していた(無言のデータ欠損)。
    失敗時は SAVE_FAILED_PREFIX で始まる失敗メッセージを返し、「記録しました」と
    言わないこと。"""

    async def test_log_child_health_reports_failure_when_save_returns_false(self, isolated_db, monkeypatch):
        monkeypatch.setattr(line_service, "save_log_async", AsyncMock(return_value=False))

        result = await line_service.log_child_health("U1", "太郎", "智矢", "元気")

        assert result.text.startswith(line_service.SAVE_FAILED_PREFIX)
        assert "智矢" in result.text
        assert "記録しました" not in result.text

    async def test_log_food_record_reports_failure_when_save_returns_false(self, isolated_db, monkeypatch):
        monkeypatch.setattr(line_service, "save_log_async", AsyncMock(return_value=False))

        result = await line_service.log_food_record("U1", "太郎", "夕食", "カレー", is_manual=True)

        assert result.text.startswith(line_service.SAVE_FAILED_PREFIX)
        assert "カレー" in result.text
        assert "記録しました" not in result.text

    async def test_log_child_health_reports_failure_on_real_db_error(self, isolated_db):
        """モックではなく実際のDBエラー(テーブル欠落)経由でも失敗メッセージになること"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(f"DROP TABLE {config.SQLITE_TABLE_CHILD}")

        result = await line_service.log_child_health("U1", "太郎", "智矢", "元気")

        assert result.text.startswith(line_service.SAVE_FAILED_PREFIX)

    async def test_log_food_record_reports_failure_on_real_db_error(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(f"DROP TABLE {config.SQLITE_TABLE_FOOD}")

        result = await line_service.log_food_record("U1", "太郎", "夕食", "カレー")

        assert result.text.startswith(line_service.SAVE_FAILED_PREFIX)

    async def test_success_message_does_not_start_with_failure_prefix(self, isolated_db):
        """成功時のメッセージが失敗プレフィックスと誤判定されないこと(ai_service側の判定の前提)"""
        health = await line_service.log_child_health("U1", "太郎", "智矢", "元気")
        food = await line_service.log_food_record("U1", "太郎", "夕食", "カレー")
        assert not health.text.startswith(line_service.SAVE_FAILED_PREFIX)
        assert not food.text.startswith(line_service.SAVE_FAILED_PREFIX)


class TestSplitTextIntoLineMessages:
    """Issue #377: LINEの5000字制限対策のテキスト分割ヘルパー"""

    def test_short_text_returns_single_text_message_unwrapped(self):
        result = line_service.split_text_into_line_messages("短いテキスト")
        assert isinstance(result, TextMessage)
        assert result.text == "短いテキスト"

    def test_text_at_exact_limit_is_not_split(self):
        text = "a" * line_service.LINE_TEXT_MAX_CHARS
        result = line_service.split_text_into_line_messages(text)
        assert isinstance(result, TextMessage)
        assert result.text == text

    def test_text_over_limit_is_split_into_multiple_messages(self):
        text = "a" * (line_service.LINE_TEXT_MAX_CHARS + 10)
        result = line_service.split_text_into_line_messages(text)
        assert isinstance(result, list)
        assert len(result) == 2
        assert all(len(m.text) <= line_service.LINE_TEXT_MAX_CHARS for m in result)
        assert "".join(m.text for m in result) == text

    def test_no_message_exceeds_5000_chars(self):
        text = "b" * (line_service.LINE_TEXT_MAX_CHARS * 3 + 500)
        result = line_service.split_text_into_line_messages(text)
        for m in result:
            assert len(m.text) < 5000

    def test_never_exceeds_max_messages_per_reply(self):
        """極端に長い応答でも5件を超えて送ろうとしないこと(末尾は切り詰め注記付き)"""
        text = "c" * (line_service.LINE_TEXT_MAX_CHARS * 20)
        result = line_service.split_text_into_line_messages(text)
        assert isinstance(result, list)
        assert len(result) == line_service.LINE_MAX_MESSAGES_PER_REPLY
        assert "省略" in result[-1].text
        assert len(result[-1].text) <= line_service.LINE_TEXT_MAX_CHARS

    def test_all_but_last_chunk_are_full_when_truncated(self):
        text = "d" * (line_service.LINE_TEXT_MAX_CHARS * 20)
        result = line_service.split_text_into_line_messages(text)
        for m in result[:-1]:
            assert len(m.text) == line_service.LINE_TEXT_MAX_CHARS
