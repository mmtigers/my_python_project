# MY_HOME_SYSTEM/tests/test_switchbot_webhook_fix.py
"""
Issue #166: switchbot_webhook_fix.py の回帰防止テスト。

update_switchbot_webhook は「旧Webhook削除→新規登録」という構成で、旧設定の
削除に成功した後に新規登録が失敗すると、戻り値が「変更なし」(既に設定済みの
場合)と同じ False になっていた。呼び出し元 fix_all_webhooks は戻り値が
True のときしか通知を送らないため、SwitchBotのWebhookが未設定のまま残る
危険な状態が無通知でログにしか残らなかった。

修正では、旧設定削除後に新規登録が失敗した場合は False ではなく None を返す
ようにし、fix_all_webhooks 側でこの状態を(更新の成否に関わらず)必ず通知
するようにした。
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import switchbot_webhook_fix as wf

BASE_URL = "https://example.com"
TARGET_URL = f"{BASE_URL}/webhook/switchbot"


def _mock_response(json_data=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = str(json_data)
    return resp


class TestUpdateSwitchbotWebhook:
    def test_returns_false_when_already_configured(self):
        query_resp = _mock_response({"body": {"urls": [TARGET_URL]}})
        with patch.object(wf.requests, "post", return_value=query_resp) as mock_post:
            result = wf.update_switchbot_webhook(BASE_URL)

        assert result is False
        # 照会のみで削除・登録は一切呼ばれないこと
        mock_post.assert_called_once()

    def test_returns_false_when_query_itself_fails(self):
        with patch.object(wf.requests, "post", side_effect=Exception("network error")):
            result = wf.update_switchbot_webhook(BASE_URL)

        assert result is False

    def test_returns_true_when_registration_succeeds(self):
        query_resp = _mock_response({"body": {"urls": ["https://old.example.com/webhook/switchbot"]}})
        delete_resp = _mock_response({"statusCode": 100})
        setup_resp = _mock_response({"statusCode": 100})

        with patch.object(wf.requests, "post", side_effect=[query_resp, delete_resp, setup_resp]), \
             patch.object(wf.time, "sleep"):
            result = wf.update_switchbot_webhook(BASE_URL)

        assert result is True

    def test_returns_none_when_setup_fails_with_bad_status_after_delete(self):
        """Issue #166の核心: 旧URL削除後に新規登録がstatusCode!=100で失敗した場合、
        Falseではなく None を返すこと(=変更なしと区別できること)。"""
        query_resp = _mock_response({"body": {"urls": ["https://old.example.com/webhook/switchbot"]}})
        delete_resp = _mock_response({"statusCode": 100})
        setup_resp = _mock_response({"statusCode": 190, "message": "some error"})

        with patch.object(wf.requests, "post", side_effect=[query_resp, delete_resp, setup_resp]), \
             patch.object(wf.time, "sleep"):
            result = wf.update_switchbot_webhook(BASE_URL)

        assert result is None

    def test_returns_none_when_setup_raises_exception_after_delete(self):
        query_resp = _mock_response({"body": {"urls": ["https://old.example.com/webhook/switchbot"]}})
        delete_resp = _mock_response({"statusCode": 100})

        with patch.object(wf.requests, "post", side_effect=[query_resp, delete_resp, Exception("timeout")]), \
             patch.object(wf.time, "sleep"):
            result = wf.update_switchbot_webhook(BASE_URL)

        assert result is None


class TestFixAllWebhooksNotifiesOnDangerousState:
    def test_sends_error_alert_when_switchbot_registration_fails_after_delete(self, monkeypatch):
        # #405: WEBHOOK_BASE_URL は config 経由で読むようになったため config 属性を差し替える
        monkeypatch.setattr(wf.config, "WEBHOOK_BASE_URL", BASE_URL)
        monkeypatch.setattr(wf, "update_switchbot_webhook", lambda base_url: None)
        monkeypatch.setattr(wf, "update_line_webhook", lambda base_url: False)
        mock_send_push = MagicMock()
        monkeypatch.setattr(wf.common, "send_push", mock_send_push)

        wf.fix_all_webhooks()

        mock_send_push.assert_called_once()
        args, kwargs = mock_send_push.call_args
        assert kwargs.get("channel") == "error"
        assert "登録に失敗" in args[0][0]["text"]

    def test_sends_success_notification_when_registration_succeeds(self, monkeypatch):
        # #405: WEBHOOK_BASE_URL は config 経由で読むようになったため config 属性を差し替える
        monkeypatch.setattr(wf.config, "WEBHOOK_BASE_URL", BASE_URL)
        monkeypatch.setattr(wf, "update_switchbot_webhook", lambda base_url: True)
        monkeypatch.setattr(wf, "update_line_webhook", lambda base_url: False)
        mock_send_push = MagicMock()
        monkeypatch.setattr(wf.common, "send_push", mock_send_push)

        wf.fix_all_webhooks()

        mock_send_push.assert_called_once()
        args, kwargs = mock_send_push.call_args
        assert kwargs.get("channel") == "report"

    def test_sends_nothing_when_nothing_changed(self, monkeypatch):
        # #405: WEBHOOK_BASE_URL は config 経由で読むようになったため config 属性を差し替える
        monkeypatch.setattr(wf.config, "WEBHOOK_BASE_URL", BASE_URL)
        monkeypatch.setattr(wf, "update_switchbot_webhook", lambda base_url: False)
        monkeypatch.setattr(wf, "update_line_webhook", lambda base_url: False)
        mock_send_push = MagicMock()
        monkeypatch.setattr(wf.common, "send_push", mock_send_push)

        wf.fix_all_webhooks()

        mock_send_push.assert_not_called()
