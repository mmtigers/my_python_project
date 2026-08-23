# MY_HOME_SYSTEM/tests/test_line_logic_postback.py
"""
handlers/line_logic.py の handle_postback() (LINE Postbackディスパッチ) のテスト。

実際のLINE API・AIサービスへは一切アクセスしない。line_bot_apiはMagicMockで代替する。

自由文フォローアップ入力(旧 USER_INPUT_STATE ステートマシン)は、line_handler.py の
AIフォールバック(services/ai_service.py)経由の一本経路に統合済みのため、ここでは
postbackが「状態を持たずに案内文だけを返す」ことのみを検証する。
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
from handlers import line_logic


@pytest.fixture
def mock_line_api():
    api = MagicMock()
    api.get_profile.return_value = MagicMock(display_name="テストユーザー")
    api.get_group_member_profile.return_value = MagicMock(display_name="テストグループ")
    return api


def fake_postback_event(data: str, user_id="U1", reply_token="tok", source_type="user"):
    event = MagicMock()
    event.source.user_id = user_id
    event.source.type = source_type
    event.reply_token = reply_token
    event.postback.data = data
    return event


def _texts_from_reply(mock_api):
    """reply_message呼び出しに渡されたTextMessageのテキスト一覧を返す"""
    call = mock_api.reply_message.call_args
    req = call[0][0]
    return [m.text for m in req.messages if hasattr(m, "text")]


class TestAllGenki:
    def test_saves_log_for_every_target_member_and_replies_flex(self, isolated_db, mock_line_api):
        event = fake_postback_event("action=all_genki")

        line_logic.handle_postback(event, mock_line_api)

        with common.get_db_cursor() as cur:
            rows = cur.execute(
                f"SELECT child_name FROM {config.SQLITE_TABLE_CHILD} WHERE user_id='U1'"
            ).fetchall()
        saved_names = {row["child_name"] for row in rows}
        assert saved_names == set(config.FAMILY_SETTINGS["members"])
        mock_line_api.reply_message.assert_called_once()

    def test_save_failure_replies_with_failure_text_not_success(
        self, isolated_db, mock_line_api, monkeypatch
    ):
        """H-7: 保存が失敗した場合、成功の「✅ 記録しました」ではなく
        失敗を知らせる返信をすること。"""
        async def _failing_save_log_async(*args, **kwargs):
            return False

        monkeypatch.setattr(line_logic, "save_log_async", _failing_save_log_async)
        event = fake_postback_event("action=all_genki")

        line_logic.handle_postback(event, mock_line_api)

        texts = _texts_from_reply(mock_line_api)
        assert any("失敗" in t for t in texts)
        assert not any("記録しました" in t for t in texts)


class TestShowHealthInput:
    def test_replies_with_text_and_flex_carousel(self, isolated_db, mock_line_api):
        event = fake_postback_event("action=show_health_input")

        line_logic.handle_postback(event, mock_line_api)

        req = mock_line_api.reply_message.call_args[0][0]
        assert len(req.messages) == 2


class TestChildCheck:
    def test_status_other_prompts_for_free_text_without_saving(self, isolated_db, mock_line_api):
        event = fake_postback_event("action=child_check&child=智矢&status=other")

        line_logic.handle_postback(event, mock_line_api)

        assert "智矢" in _texts_from_reply(mock_line_api)[0]
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_CHILD}").fetchone()["c"]
        assert count == 0

    def test_status_genki_saves_directly(self, isolated_db, mock_line_api):
        event = fake_postback_event("action=child_check&child=智矢&status=genki")

        line_logic.handle_postback(event, mock_line_api)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_CHILD} WHERE child_name='智矢'"
            ).fetchone()
        assert row is not None
        assert "元気" in row["condition"]

    def test_save_failure_replies_with_failure_text_not_success(
        self, isolated_db, mock_line_api, monkeypatch
    ):
        """H-7: 個別記録保存に失敗した場合、成功メッセージを返さないこと。"""
        async def _failing_save_log_async(*args, **kwargs):
            return False

        monkeypatch.setattr(line_logic, "save_log_async", _failing_save_log_async)
        event = fake_postback_event("action=child_check&child=智矢&status=genki")

        line_logic.handle_postback(event, mock_line_api)

        texts = _texts_from_reply(mock_line_api)
        assert any("失敗" in t for t in texts)
        assert not any("記録しました" in t for t in texts)
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_CHILD}").fetchone()["c"]
        assert count == 0

    def test_missing_child_param_is_a_silent_noop(self, isolated_db, mock_line_api):
        """child パラメータが無い場合、既存実装ではどの分岐にも入らず
        DB保存も返信も一切行われない(サイレントな無反応)。この既存挙動を固定する。"""
        event = fake_postback_event("action=child_check&status=genki")

        line_logic.handle_postback(event, mock_line_api)

        mock_line_api.reply_message.assert_not_called()
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_CHILD}").fetchone()["c"]
        assert count == 0


class TestCheckStatus:
    def test_builds_summary_flex_from_existing_records(self, isolated_db, mock_line_api):
        today = line_logic.get_today_date_str()
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_CHILD} (child_name, condition, timestamp) VALUES (?, ?, ?)",
                ("智矢", "😊 元気いっぱい", f"{today}T08:00:00"),
            )
        event = fake_postback_event("action=check_status")

        line_logic.handle_postback(event, mock_line_api)

        mock_line_api.reply_message.assert_called_once()

    def test_db_read_error_falls_back_to_error_text_in_summary(self, isolated_db, mock_line_api, monkeypatch):
        monkeypatch.setattr(
            line_logic.sqlite3, "connect", MagicMock(side_effect=Exception("disk error"))
        )
        event = fake_postback_event("action=check_status")

        line_logic.handle_postback(event, mock_line_api)  # 例外が外に漏れないこと

        mock_line_api.reply_message.assert_called_once()


class TestFoodRecordDirect:
    def test_saves_record_and_replies_with_category_and_item(self, isolated_db, mock_line_api):
        event = fake_postback_event("action=food_record_direct&category=麺類&item=ラーメン")

        line_logic.handle_postback(event, mock_line_api)

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_FOOD}").fetchone()
        assert "麺類" in row["menu_category"]
        assert "ラーメン" in row["menu_category"]

    def test_missing_item_defaults_to_placeholder(self, isolated_db, mock_line_api):
        event = fake_postback_event("action=food_record_direct&category=麺類")

        line_logic.handle_postback(event, mock_line_api)

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_FOOD}").fetchone()
        assert "不明なメニュー" in row["menu_category"]

    def test_save_failure_replies_with_failure_text_not_success(
        self, isolated_db, mock_line_api, monkeypatch
    ):
        """H-7: 食事記録の保存に失敗した場合、成功メッセージを返さないこと。"""
        async def _failing_save_log_async(*args, **kwargs):
            return False

        monkeypatch.setattr(line_logic, "save_log_async", _failing_save_log_async)
        event = fake_postback_event("action=food_record_direct&category=麺類&item=ラーメン")

        line_logic.handle_postback(event, mock_line_api)

        texts = _texts_from_reply(mock_line_api)
        assert any("失敗" in t for t in texts)
        assert not any("記録しました" in t for t in texts)
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_FOOD}").fetchone()["c"]
        assert count == 0


class TestFoodManual:
    @pytest.mark.parametrize(
        "category,expected_fragment",
        [
            ("外食", "お店の名前"),
            ("自炊", "作ったメニュー"),
            ("その他", "食べたもの"),
        ],
    )
    def test_replies_with_category_specific_prompt(
        self, isolated_db, mock_line_api, category, expected_fragment
    ):
        event = fake_postback_event(f"action=food_manual&category={category}")

        line_logic.handle_postback(event, mock_line_api)

        assert expected_fragment in _texts_from_reply(mock_line_api)[0]


class TestUnknownAction:
    def test_unknown_action_replies_with_fallback_warning_text(self, isolated_db, mock_line_api):
        event = fake_postback_event("action=something_undefined")

        line_logic.handle_postback(event, mock_line_api)

        assert "不明な操作" in _texts_from_reply(mock_line_api)[0]


class TestHandlePostbackCrashIsolation:
    def test_exception_inside_handler_is_logged_only_no_reraise_no_extra_reply(
        self, isolated_db, mock_line_api
    ):
        """handle_postback全体はtry/exceptで包まれ、例外はログのみで外へは伝播しない
        (ユーザーへの追加返信もされない)。line_bot_api.reply_message自体を失敗させて確認する。"""
        mock_line_api.reply_message.side_effect = Exception("LINE API down")
        event = fake_postback_event("action=all_genki")

        line_logic.handle_postback(event, mock_line_api)  # 例外が外に漏れないこと
