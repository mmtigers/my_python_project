# MY_HOME_SYSTEM/tests/test_line_conversation_flow.py
"""
postback起点の自由文フォローアップ(健康記録「その他」ボタン / 食事「手入力」
ボタン)が、実際のLINE Webhook経路(handlers/line_handler.py)の
AIフォールバック(services/ai_service.py)を通じてDBへ記録されるところまでを
通しで検証する回帰テスト。

以前は handlers/line_logic.py の handle_message() + USER_INPUT_STATE
ステートマシンがこのフォローアップを処理する設計だったが、本番のLINE
Webhook経路(handlers/line_handler.py)からは呼ばれておらず到達不能な
デッドコードだったため削除した(handle_postback の案内文はそのまま残る)。
案内文が約束する「メッセージを送ってください」という挙動が、削除後も
line_handler.py側の経路で実際に成立していることをGemini API呼び出しの
境界のみモックした結合テストで担保する。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
from handlers import line_handler
from services import ai_service


def make_response(function_call=None, text="OK"):
    resp = MagicMock()
    resp.text = text
    part = MagicMock()
    part.function_call = function_call
    resp.parts = [part]
    return resp


def make_function_call(name, args):
    fc = MagicMock()
    fc.name = name
    fc.args = args
    return fc


@pytest.fixture
def ai_configured(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(ai_service, "MODEL_NAME", "gemini-2.0-flash")
    monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))


@pytest.mark.asyncio
class TestPostbackPromptFollowedByFreeTextReachesDb:
    """handle_postback の案内文(「メッセージで送ってください」等)が約束する
    通りに、後続の自由文が実際に記録されることを確認する。"""

    async def test_child_health_free_text_after_other_button_is_recorded(
        self, isolated_db, ai_configured, monkeypatch
    ):
        fc = make_function_call(
            "record_child_health", {"child_name": "智矢", "condition": "少し元気がない"}
        )
        mock_retry = AsyncMock(
            side_effect=[
                make_response(function_call=fc),
                make_response(function_call=None, text="記録したよ"),
            ]
        )
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        # postbackで「その他」を選んだ後、ユーザーが自由文を送るのと同じ経路
        await line_handler._process_message_async("U1", "パパ", "少し元気がない", "tok")

        with common.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_CHILD} WHERE child_name='智矢'"
            ).fetchone()
        assert row is not None
        assert row["condition"] == "少し元気がない"

    async def test_meal_free_text_after_manual_button_is_recorded(
        self, isolated_db, ai_configured, monkeypatch
    ):
        fc = make_function_call("record_food", {"item": "オムライス", "category": "自炊"})
        mock_retry = AsyncMock(
            side_effect=[
                make_response(function_call=fc),
                make_response(function_call=None, text="記録したよ"),
            ]
        )
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", "オムライス作った", "tok")

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_FOOD}").fetchone()
        assert row is not None
        assert "オムライス" in row["menu_category"]


def test_ai_logic_module_removed_and_not_reintroduced():
    """到達不能だった handlers/ai_logic.py (declare_*未配線バグの所在)は
    削除済み。将来同じデッドコードクラスタが復活していないことをガードする。"""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("handlers.ai_logic")

    import handlers.line_logic as line_logic

    assert not hasattr(line_logic, "ai_logic")
    assert not hasattr(line_logic, "handle_message")
    assert not hasattr(line_logic, "USER_INPUT_STATE")
