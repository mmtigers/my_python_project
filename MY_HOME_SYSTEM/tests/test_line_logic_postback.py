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
        async def _failing_save_logs_batch_async(*args, **kwargs):
            return False

        monkeypatch.setattr(line_logic, "save_logs_batch_async", _failing_save_logs_batch_async)
        event = fake_postback_event("action=all_genki")

        line_logic.handle_postback(event, mock_line_api)

        texts = _texts_from_reply(mock_line_api)
        assert any("失敗" in t for t in texts)
        assert not any("記録しました" in t for t in texts)

    def test_partial_failure_rolls_back_so_retry_does_not_duplicate(
        self, isolated_db, mock_line_api, monkeypatch
    ):
        """Issue #231の回帰テスト: TARGET_MEMBERSのうち一部だけ保存に失敗する
        場合でも、save_logs_batch_asyncが単一トランザクションで実行するため
        既に成功していたメンバー分も含めて全件ロールバックされる
        (all-or-nothing)。以前はsave_log_asyncをメンバーごとに独立に呼んで
        いたため、この状況で成功済み分だけがコミット済みのまま残り、失敗
        通知を受けて再試行すると重複して保存されていた。"""
        from core import database as db_module

        real_batch_generic = db_module.save_logs_batch_generic

        def _batch_generic_with_broken_second_row(table, columns_list, values_list):
            # 1件目は正常なまま、2件目だけ列数を不正にしてINSERTを失敗させる
            # (実際のDBロック競合等で一部だけ失敗する状況を模す)。
            broken_values = list(values_list)
            broken_values[1] = broken_values[1][:2]
            return real_batch_generic(table, columns_list, broken_values)

        monkeypatch.setattr(db_module, "save_logs_batch_generic", _batch_generic_with_broken_second_row)

        line_logic.handle_postback(fake_postback_event("action=all_genki"), mock_line_api)

        texts = _texts_from_reply(mock_line_api)
        assert any("失敗" in t for t in texts)

        with common.get_db_cursor() as cur:
            count = cur.execute(
                f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_CHILD} WHERE user_id='U1'"
            ).fetchone()["c"]
        assert count == 0, "一部失敗時に成功済み分がコミットされたまま残っている(重複の温床)"

        # 案内どおりユーザーが再試行すると、今度は(修正が入り)正常に全員分が保存され、
        # 失敗時に残った分との重複も一切発生しないこと。
        monkeypatch.setattr(db_module, "save_logs_batch_generic", real_batch_generic)
        line_logic.handle_postback(fake_postback_event("action=all_genki"), mock_line_api)

        with common.get_db_cursor() as cur:
            rows = cur.execute(
                f"SELECT child_name FROM {config.SQLITE_TABLE_CHILD} WHERE user_id='U1'"
            ).fetchall()
        saved_names = [row["child_name"] for row in rows]
        assert sorted(saved_names) == sorted(config.FAMILY_SETTINGS["members"]), (
            "再試行後の保存件数がメンバー数と一致しない(重複または欠落)"
        )


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


class TestCheckStatusUsesJstDisplayDate:
    def test_uses_get_display_date_not_naive_now(self, isolated_db, mock_line_api, monkeypatch):
        """L-L2 (#410) の回帰テスト: 記録確認画面の日付表示にnaive
        datetime.datetime.now()(サーバーのローカルタイムゾーン依存)ではなく、
        JST基準のcore.utils.get_display_date()を使うこと。"""
        spy = MagicMock(return_value="09/04")
        monkeypatch.setattr(line_logic, "get_display_date", spy)
        event = fake_postback_event("action=check_status")

        line_logic.handle_postback(event, mock_line_api)

        spy.assert_called_once()
        mock_line_api.reply_message.assert_called_once()


class TestPydanticFallbackRemovalDoesNotCrash:
    """保守性(#410)の回帰テスト: LinePostbackData構築の到達不能フォールバックを
    削除した後も、actionキーを欠いた不正な postback.data で例外が外へ漏れず、
    handle_postback全体のtry/exceptで安全に握り潰されること
    (LinePostbackDataはaction必須なのでValidationErrorになる想定)。"""

    def test_postback_data_without_action_key_does_not_raise(self, isolated_db, mock_line_api):
        event = fake_postback_event("child=智矢&status=genki")  # actionキーが無い

        line_logic.handle_postback(event, mock_line_api)  # 例外が外に漏れないこと

        mock_line_api.reply_message.assert_not_called()

    def test_postback_data_with_action_and_extra_unknown_fields_is_accepted(
        self, isolated_db, mock_line_api
    ):
        """モデルに定義の無い余分なフィールドがあっても(pydanticの既定挙動で無視され)
        正常に処理されること。"""
        event = fake_postback_event("action=all_genki&unexpected_field=xyz")

        line_logic.handle_postback(event, mock_line_api)

        mock_line_api.reply_message.assert_called_once()
