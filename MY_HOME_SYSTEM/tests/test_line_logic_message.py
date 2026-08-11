# MY_HOME_SYSTEM/tests/test_line_logic_message.py
"""
handlers/line_logic.py の handle_message() (LINE会話ステートマシンの
テキストメッセージディスパッチ側) のテスト。

実際のLINE API・AIサービスへは一切アクセスしない。line_bot_apiはMagicMockで、
handlers.ai_logic.analyze_text_and_execute はモックで代替する(同期関数なので
MagicMockで代替し、AsyncMockは使わない)。
グローバル可変状態 USER_INPUT_STATE はテスト前後でクリアし、他テストへの
汚染を防ぐ。
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
from handlers import line_logic
from models.line import InputMode, UserInputState


@pytest.fixture(autouse=True)
def _reset_user_input_state():
    line_logic.USER_INPUT_STATE.clear()
    yield
    line_logic.USER_INPUT_STATE.clear()


@pytest.fixture(autouse=True)
def _mock_ai_fallback(monkeypatch):
    """AIフォールバックは全テストで既定ではNoneを返すダミーに差し替える
    (実際のGemini呼び出しを一切行わないため)。個別テストで上書き可能。"""
    monkeypatch.setattr(line_logic.ai_logic, "analyze_text_and_execute", MagicMock(return_value=None))


@pytest.fixture
def mock_line_api():
    api = MagicMock()
    api.get_profile.return_value = MagicMock(display_name="テストユーザー")
    api.get_group_member_profile.return_value = MagicMock(display_name="テストグループ")
    return api


def fake_message_event(text: str, user_id="U1", reply_token="tok", source_type="user"):
    event = MagicMock()
    event.source.user_id = user_id
    event.source.type = source_type
    event.reply_token = reply_token
    event.message.text = text
    return event


def _texts_from_reply(mock_api):
    call = mock_api.reply_message.call_args
    req = call[0][0]
    return [m.text for m in req.messages if hasattr(m, "text")]


class TestUserInputStateModeDispatch:
    def test_cancel_keyword_clears_state_and_replies(self, isolated_db, mock_line_api):
        line_logic.USER_INPUT_STATE["U1"] = UserInputState(mode=InputMode.MEAL, category="夕食")
        event = fake_message_event("キャンセル")

        line_logic.handle_message(event, mock_line_api)

        assert "U1" not in line_logic.USER_INPUT_STATE
        assert "キャンセルしました" in _texts_from_reply(mock_line_api)[0]

    def test_interrupt_command_prefix_clears_state_and_is_processed_as_new_command(
        self, isolated_db, mock_line_api
    ):
        """入力待ち状態(MEAL)の最中に別コマンドprefixが来た場合、状態はクリアされ、
        メッセージはそのモードの自由入力としてではなく通常のコマンドとして処理される。"""
        line_logic.USER_INPUT_STATE["U1"] = UserInputState(mode=InputMode.MEAL, category="夕食")
        event = fake_message_event("子供選択_智矢")

        line_logic.handle_message(event, mock_line_api)

        assert "U1" not in line_logic.USER_INPUT_STATE
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_FOOD}").fetchone()["c"]
        assert count == 0  # MEALモードの自由入力としては保存されていない
        assert "智矢ちゃんの様子" in _texts_from_reply(mock_line_api)[0]

    def test_child_health_mode_saves_free_text_and_clears_state(self, isolated_db, mock_line_api):
        line_logic.USER_INPUT_STATE["U1"] = UserInputState(
            mode=InputMode.CHILD_HEALTH, target_name="智矢"
        )
        event = fake_message_event("鼻水が出ています")

        line_logic.handle_message(event, mock_line_api)

        assert "U1" not in line_logic.USER_INPUT_STATE
        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_CHILD}").fetchone()
        assert row["child_name"] == "智矢"
        assert row["condition"] == "鼻水が出ています"

    def test_meal_mode_saves_free_text_asks_outing_question_and_clears_state(
        self, isolated_db, mock_line_api
    ):
        line_logic.USER_INPUT_STATE["U1"] = UserInputState(mode=InputMode.MEAL, category="夕食")
        event = fake_message_event("カレーライス")

        line_logic.handle_message(event, mock_line_api)

        assert "U1" not in line_logic.USER_INPUT_STATE
        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_FOOD}").fetchone()
        assert "カレーライス" in row["menu_category"]
        assert "お出かけした" in _texts_from_reply(mock_line_api)[0]

    def test_meal_mode_over_50_chars_rejects_and_leaves_state_uncleared(
        self, isolated_db, mock_line_api
    ):
        """既存実装では50文字超入力時、エラー文言を返すだけで状態はクリアされない
        (ユーザーは再送信するまで入力待ち状態のまま詰まる)。この既存挙動を固定する。"""
        line_logic.USER_INPUT_STATE["U1"] = UserInputState(mode=InputMode.MEAL, category="夕食")
        long_text = "あ" * 51
        event = fake_message_event(long_text)

        line_logic.handle_message(event, mock_line_api)

        assert "長すぎるよ" in _texts_from_reply(mock_line_api)[0]
        assert "U1" in line_logic.USER_INPUT_STATE
        assert line_logic.USER_INPUT_STATE["U1"].mode == InputMode.MEAL
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_FOOD}").fetchone()["c"]
        assert count == 0

    def test_stomach_mode_is_a_noop_that_clears_state_without_reply(self, isolated_db, mock_line_api):
        line_logic.USER_INPUT_STATE["U1"] = UserInputState(mode=InputMode.STOMACH)
        event = fake_message_event("お腹痛い")

        line_logic.handle_message(event, mock_line_api)

        assert "U1" not in line_logic.USER_INPUT_STATE
        mock_line_api.reply_message.assert_not_called()


class TestCommandPrefixDispatch:
    def test_child_select_prefix_sends_quick_reply_with_symptom_options(self, isolated_db, mock_line_api):
        event = fake_message_event("子供選択_智矢")

        line_logic.handle_message(event, mock_line_api)

        assert "智矢ちゃんの様子" in _texts_from_reply(mock_line_api)[0]

    def test_child_record_prefix_saves_and_replies_with_condition_specific_text(
        self, isolated_db, mock_line_api
    ):
        event = fake_message_event("子供記録_智矢_🤒 お熱がある")

        line_logic.handle_message(event, mock_line_api)

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_CHILD}").fetchone()
        assert row["child_name"] == "智矢"
        assert "心配ですね" in _texts_from_reply(mock_line_api)[0]

    def test_child_record_all_members_saves_one_row_per_child(self, isolated_db, mock_line_api):
        event = fake_message_event("子供記録_全員_元気")

        line_logic.handle_message(event, mock_line_api)

        with common.get_db_cursor() as cur:
            rows = cur.execute(f"SELECT child_name FROM {config.SQLITE_TABLE_CHILD}").fetchall()
        assert {r["child_name"] for r in rows} == set(config.CHILDREN_NAMES)

    def test_meal_category_prefix_sends_quick_reply_with_menu_options(self, isolated_db, mock_line_api):
        event = fake_message_event("食事カテゴリ_麺類")

        line_logic.handle_message(event, mock_line_api)

        mock_line_api.reply_message.assert_called_once()

    def test_meal_manual_entry_prefix_sets_meal_input_state(self, isolated_db, mock_line_api):
        event = fake_message_event("食事手入力_麺類")

        line_logic.handle_message(event, mock_line_api)

        assert line_logic.USER_INPUT_STATE["U1"].mode == InputMode.MEAL
        assert line_logic.USER_INPUT_STATE["U1"].category == "麺類"

    def test_meal_record_prefix_saves_and_asks_outing_question(self, isolated_db, mock_line_api):
        event = fake_message_event("食事記録_麺類_ラーメン")

        line_logic.handle_message(event, mock_line_api)

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_FOOD}").fetchone()
        assert "ラーメン" in row["menu_category"]
        assert "お出かけした" in _texts_from_reply(mock_line_api)[0]

    def test_meal_record_prefix_with_too_few_parts_is_a_silent_noop(self, isolated_db, mock_line_api):
        """アンダースコアが1個しかない(区切りが足りない)メッセージは、
        既存実装ではDB保存も返信も行われないサイレントな無反応になる。"""
        event = fake_message_event("食事記録_麺類のみ")

        line_logic.handle_message(event, mock_line_api)

        mock_line_api.reply_message.assert_not_called()
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_FOOD}").fetchone()["c"]
        assert count == 0

    def test_meal_skip_replies_with_acknowledgement(self, isolated_db, mock_line_api):
        event = fake_message_event("食事_スキップ")

        line_logic.handle_message(event, mock_line_api)

        assert "U1" not in line_logic.USER_INPUT_STATE
        assert "了解です" in _texts_from_reply(mock_line_api)[0]

    def test_meal_skip_while_in_meal_state_is_swallowed_as_free_text_input(
        self, isolated_db, mock_line_api
    ):
        """既存実装では「食事_スキップ」は割り込みprefix一覧(食事カテゴリ_/食事記録_等)に
        含まれていないため、MEAL入力待ち状態の最中に送られると「スキップ」コマンドとしてではなく
        自由入力テキストそのものとして保存されてしまう。この意図しない可能性がある既存挙動を固定する。"""
        line_logic.USER_INPUT_STATE["U1"] = UserInputState(mode=InputMode.MEAL, category="夕食")
        event = fake_message_event("食事_スキップ")

        line_logic.handle_message(event, mock_line_api)

        assert "U1" not in line_logic.USER_INPUT_STATE
        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_FOOD}").fetchone()
        assert "食事_スキップ" in row["menu_category"]
        assert "お出かけした" in _texts_from_reply(mock_line_api)[0]

    def test_outing_prefix_saves_to_daily_logs_and_asks_visit_question(self, isolated_db, mock_line_api):
        """config.SQLITE_TABLE_DAILY (存在しない属性) を参照していたバグの修正を確認する回帰テスト。
        修正前はAttributeErrorでクラッシュしていた。"""
        event = fake_message_event("外出_はい")

        line_logic.handle_message(event, mock_line_api)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_DAILY_LOGS} WHERE category='外出'"
            ).fetchone()
        assert row is not None
        assert row["detail"] == "はい"
        assert "会ったりした" in _texts_from_reply(mock_line_api)[0]

    def test_visit_prefix_saves_to_daily_logs_and_replies_thanks(self, isolated_db, mock_line_api):
        """外出_と同じ config.SQLITE_TABLE_DAILY バグの修正を確認する回帰テスト。"""
        event = fake_message_event("面会_いいえ")

        line_logic.handle_message(event, mock_line_api)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_DAILY_LOGS} WHERE category='面会'"
            ).fetchone()
        assert row is not None
        assert row["detail"] == "いいえ"
        assert "ありがとう" in _texts_from_reply(mock_line_api)[0]

    def test_stomach_record_prefix_saves_and_adds_warning_for_serious_condition(
        self, isolated_db, mock_line_api
    ):
        event = fake_message_event("お腹記録_下痢_腹痛あり")

        line_logic.handle_message(event, mock_line_api)

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_DEFECATION}").fetchone()
        assert row["record_type"] == "下痢"
        assert "お大事に" in _texts_from_reply(mock_line_api)[0]


class TestOhayoAndAiFallback:
    def test_ohayo_keyword_short_message_saves_and_replies_without_calling_ai(
        self, isolated_db, mock_line_api
    ):
        event = fake_message_event("おはよう")

        line_logic.handle_message(event, mock_line_api)

        line_logic.ai_logic.analyze_text_and_execute.assert_not_called()
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_OHAYO}").fetchone()["c"]
        assert count == 1
        assert "おはようございます" in _texts_from_reply(mock_line_api)[0]

    def test_unmatched_message_falls_back_to_ai_and_replies_with_its_response(
        self, isolated_db, mock_line_api, monkeypatch
    ):
        monkeypatch.setattr(
            line_logic.ai_logic, "analyze_text_and_execute", MagicMock(return_value="AIからの返答です")
        )
        event = fake_message_event("今日の天気は？")

        line_logic.handle_message(event, mock_line_api)

        line_logic.ai_logic.analyze_text_and_execute.assert_called_once_with(
            "今日の天気は？", "U1", "テストユーザー"
        )
        assert "AIからの返答です" in _texts_from_reply(mock_line_api)[0]

    def test_ai_returns_none_sends_no_reply(self, isolated_db, mock_line_api):
        event = fake_message_event("今日の天気は？")

        line_logic.handle_message(event, mock_line_api)

        mock_line_api.reply_message.assert_not_called()

    def test_ai_exception_propagates_uncaught_unlike_handle_postback(
        self, isolated_db, mock_line_api, monkeypatch
    ):
        """handle_message にはトップレベルのtry/exceptが無いため、AI呼び出しで例外が
        起きると外へ伝播する。handle_postback(全体をtry/exceptで保護)との非対称性を
        明示する回帰テスト。"""
        monkeypatch.setattr(
            line_logic.ai_logic,
            "analyze_text_and_execute",
            MagicMock(side_effect=Exception("Gemini API down")),
        )
        event = fake_message_event("今日の天気は？")

        with pytest.raises(Exception, match="Gemini API down"):
            line_logic.handle_message(event, mock_line_api)


class TestHelperFunctions:
    def test_sync_run_returns_coroutine_result(self):
        async def _coro():
            return 42

        assert line_logic.sync_run(_coro()) == 42

    def test_sync_run_swallows_exception_and_returns_none(self):
        async def _raising_coro():
            raise ValueError("boom")

        assert line_logic.sync_run(_raising_coro()) is None

    def test_get_user_name_returns_user_profile_display_name(self, mock_line_api):
        event = MagicMock()
        event.source.user_id = "U1"
        event.source.type = "user"

        name = line_logic.get_user_name(event, mock_line_api)

        assert name == "テストユーザー"

    def test_get_user_name_returns_group_profile_display_name(self, mock_line_api):
        event = MagicMock()
        event.source.user_id = "U1"
        event.source.type = "group"
        event.source.group_id = "G1"

        name = line_logic.get_user_name(event, mock_line_api)

        assert name == "テストグループ"

    def test_get_user_name_falls_back_to_default_on_api_exception(self):
        api = MagicMock()
        api.get_profile.side_effect = Exception("LINE API error")
        event = MagicMock()
        event.source.user_id = "U1"
        event.source.type = "user"

        name = line_logic.get_user_name(event, api)

        assert name == "家族のみんな"

    def test_create_quick_reply_truncates_labels_over_20_chars(self):
        long_label = "あ" * 25
        qr = line_logic.create_quick_reply([(long_label, "text")])

        assert len(qr.items[0].action.label) == 20


class TestGetDailyHealthSummary:
    def test_lists_unrecorded_member_as_unknown(self, isolated_db):
        summary = line_logic.get_daily_health_summary()
        for member in config.FAMILY_SETTINGS["members"]:
            assert f"❓ {member}: (未記録)" in summary

    def test_uses_warning_icon_for_non_genki_condition(self, isolated_db):
        today = line_logic.get_today_date_str()
        member = config.FAMILY_SETTINGS["members"][0]
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_CHILD} (child_name, condition, timestamp) VALUES (?, ?, ?)",
                (member, "🤒 お熱がある", f"{today}T08:30:00"),
            )
        summary = line_logic.get_daily_health_summary()
        assert "⚠️" in summary
        assert "08:30" in summary

    def test_handles_unparsable_timestamp_gracefully(self, isolated_db):
        today = line_logic.get_today_date_str()
        member = config.FAMILY_SETTINGS["members"][0]
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_CHILD} (child_name, condition, timestamp) VALUES (?, ?, ?)",
                (member, "😊 元気いっぱい", f"{today}not-a-real-timestamp"),
            )
        summary = line_logic.get_daily_health_summary()
        assert "??:??" in summary
