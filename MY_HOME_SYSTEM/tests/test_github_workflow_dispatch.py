# MY_HOME_SYSTEM/tests/test_github_workflow_dispatch.py
"""tools/github_workflow_dispatch.py のテスト。

GitHub Actions の schedule は混雑時に数時間遅れるため、ラズパイの cron から
workflow_dispatch で時刻どおりに起動する。失敗は終了コード1で返し、run_task.sh の
失敗通知(tools/notify_task_failure.py)に乗せる。
"""
import os
import sys
from unittest.mock import MagicMock

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from tools import github_workflow_dispatch as gwd


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_DISPATCH_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(config, "GITHUB_DISPATCH_REPO", "owner/repo", raising=False)
    monkeypatch.setattr(config, "GITHUB_DISPATCH_WORKFLOW", "post.yml", raising=False)
    monkeypatch.setattr(config, "GITHUB_DISPATCH_REF", "main", raising=False)
    monkeypatch.setattr(config, "GITHUB_DISPATCH_TIMEOUT_SEC", 20, raising=False)


@pytest.fixture()
def fake_logger(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(gwd, "logger", logger)
    return logger


def _fake_post(monkeypatch, status=204, text="", exc=None):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if exc:
            raise exc
        res = MagicMock()
        res.status_code = status
        res.text = text
        return res

    monkeypatch.setattr(gwd.requests, "post", post)
    return calls


def test_parse_inputs():
    assert gwd.parse_inputs(["dry_run=false", "slot=morning"]) == {"dry_run": "false", "slot": "morning"}
    assert gwd.parse_inputs(["note=a=b"]) == {"note": "a=b"}
    assert gwd.parse_inputs([]) == {}
    with pytest.raises(ValueError):
        gwd.parse_inputs(["no_equal"])
    with pytest.raises(ValueError):
        gwd.parse_inputs(["=value"])


def test_success_posts_expected_request(monkeypatch, fake_logger):
    calls = _fake_post(monkeypatch, status=204)
    assert gwd.main(["--input", "dry_run=false", "--input", "slot=noon"]) == 0
    url, kwargs = calls[0]
    assert url == "https://api.github.com/repos/owner/repo/actions/workflows/post.yml/dispatches"
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert kwargs["json"] == {"ref": "main", "inputs": {"dry_run": "false", "slot": "noon"}}
    assert kwargs["timeout"] == 20
    fake_logger.error.assert_not_called()


def test_non_204_returns_1(monkeypatch, fake_logger):
    _fake_post(monkeypatch, status=401, text="Bad credentials")
    assert gwd.main([]) == 1
    fake_logger.error.assert_called_once()


def test_network_error_returns_1(monkeypatch, fake_logger):
    _fake_post(monkeypatch, exc=requests.ConnectionError("down"))
    assert gwd.main([]) == 1
    fake_logger.error.assert_called_once()


@pytest.mark.parametrize("name,value", [
    ("GITHUB_DISPATCH_TOKEN", None),
    ("GITHUB_DISPATCH_REPO", ""),
    ("GITHUB_DISPATCH_REPO", "no-slash"),
    ("GITHUB_DISPATCH_WORKFLOW", ""),
])
def test_missing_settings_returns_1_without_request(monkeypatch, fake_logger, name, value):
    monkeypatch.setattr(config, name, value, raising=False)
    calls = _fake_post(monkeypatch)
    assert gwd.main([]) == 1
    assert calls == []
    fake_logger.error.assert_called_once()


def test_bad_input_returns_1_without_request(monkeypatch, fake_logger):
    calls = _fake_post(monkeypatch)
    assert gwd.main(["--input", "oops"]) == 1
    assert calls == []
