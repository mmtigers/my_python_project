# MY_HOME_SYSTEM/tests/test_nature_remo_service.py
"""services/nature_remo_service.py のテスト(テレビの電源信号をNature Remoで送る)。実通信はしない。"""
import os
import sys
from unittest.mock import MagicMock

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import nature_remo_service


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(config, "NATURE_REMO_ACCESS_TOKEN", "token-itami", raising=False)
    monkeypatch.setattr(config, "NATURE_REMO_ACCESS_TOKEN_TAKASAGO", "token-takasago", raising=False)
    monkeypatch.setattr(config, "TV_NATURE_REMO_LOCATION", "takasago", raising=False)
    monkeypatch.setattr(config, "TV_REMO_SIGNAL_ID", None, raising=False)
    monkeypatch.setattr(config, "TV_REMO_APPLIANCE_ID", None, raising=False)


def _ok_response():
    res = MagicMock()
    res.raise_for_status.return_value = None
    return res


class TestIsTvRemoteConfigured:
    def test_false_without_signal_or_appliance(self):
        assert nature_remo_service.is_tv_remote_configured() is False

    def test_true_with_signal_id(self, monkeypatch):
        monkeypatch.setattr(config, "TV_REMO_SIGNAL_ID", "sig-1", raising=False)
        assert nature_remo_service.is_tv_remote_configured() is True

    def test_true_with_appliance_id(self, monkeypatch):
        monkeypatch.setattr(config, "TV_REMO_APPLIANCE_ID", "app-1", raising=False)
        assert nature_remo_service.is_tv_remote_configured() is True

    def test_false_when_location_is_unknown(self, monkeypatch):
        monkeypatch.setattr(config, "TV_REMO_SIGNAL_ID", "sig-1", raising=False)
        monkeypatch.setattr(config, "TV_NATURE_REMO_LOCATION", "", raising=False)
        assert nature_remo_service.is_tv_remote_configured() is False


class TestSendTvPower:
    def test_sends_signal_with_the_location_token(self, monkeypatch):
        monkeypatch.setattr(config, "TV_REMO_SIGNAL_ID", "sig-1", raising=False)
        post = MagicMock(return_value=_ok_response())
        monkeypatch.setattr(nature_remo_service.requests, "post", post)

        assert nature_remo_service.send_tv_power() is True

        url = post.call_args.args[0]
        assert url == "https://api.nature.global/1/signals/sig-1/send"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer token-takasago"

    def test_uses_the_itami_token_for_itami(self, monkeypatch):
        monkeypatch.setattr(config, "TV_NATURE_REMO_LOCATION", "itami", raising=False)
        monkeypatch.setattr(config, "TV_REMO_SIGNAL_ID", "sig-1", raising=False)
        post = MagicMock(return_value=_ok_response())
        monkeypatch.setattr(nature_remo_service.requests, "post", post)

        nature_remo_service.send_tv_power()

        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer token-itami"

    def test_sends_power_button_for_a_tv_appliance(self, monkeypatch):
        monkeypatch.setattr(config, "TV_REMO_APPLIANCE_ID", "app-1", raising=False)
        post = MagicMock(return_value=_ok_response())
        monkeypatch.setattr(nature_remo_service.requests, "post", post)

        assert nature_remo_service.send_tv_power() is True

        assert post.call_args.args[0] == "https://api.nature.global/1/appliances/app-1/tv"
        assert post.call_args.kwargs["data"] == {"button": "power"}

    def test_signal_takes_priority_over_appliance(self, monkeypatch):
        monkeypatch.setattr(config, "TV_REMO_SIGNAL_ID", "sig-1", raising=False)
        monkeypatch.setattr(config, "TV_REMO_APPLIANCE_ID", "app-1", raising=False)
        post = MagicMock(return_value=_ok_response())
        monkeypatch.setattr(nature_remo_service.requests, "post", post)

        nature_remo_service.send_tv_power()

        assert "/signals/sig-1/send" in post.call_args.args[0]

    def test_false_when_not_configured_and_no_request(self, monkeypatch):
        post = MagicMock()
        monkeypatch.setattr(nature_remo_service.requests, "post", post)

        assert nature_remo_service.send_tv_power() is False
        post.assert_not_called()

    def test_false_when_the_request_fails(self, monkeypatch):
        monkeypatch.setattr(config, "TV_REMO_SIGNAL_ID", "sig-1", raising=False)
        monkeypatch.setattr(
            nature_remo_service.requests, "post", MagicMock(side_effect=requests.ConnectionError("down"))
        )

        assert nature_remo_service.send_tv_power() is False

    def test_false_on_an_http_error(self, monkeypatch):
        monkeypatch.setattr(config, "TV_REMO_SIGNAL_ID", "sig-1", raising=False)
        res = MagicMock()
        res.raise_for_status.side_effect = requests.HTTPError("401")
        monkeypatch.setattr(nature_remo_service.requests, "post", MagicMock(return_value=res))

        assert nature_remo_service.send_tv_power() is False
