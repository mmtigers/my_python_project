"""tools/check_switchbot_hub_api.py のテスト(Issue #881)。SwitchBot APIは呼ばずモックする。"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tools import check_switchbot_hub_api as tool


def _response(status, body):
    res = MagicMock()
    res.status_code = status
    res.json.return_value = body
    return res


DEVICE_LIST = {
    "statusCode": 100,
    "message": "success",
    "body": {"deviceList": [
        {"deviceId": "HUB1", "deviceType": "Hub Mini", "enableCloudService": True, "hubDeviceId": "HUB1"},
        {"deviceId": "METER1", "deviceType": "Meter", "enableCloudService": True, "hubDeviceId": "HUB1"},
    ]},
}


def test_collect_records_only_hubs_and_their_status():
    responses = {
        "/v1.1/devices": _response(200, DEVICE_LIST),
        "/v1.1/devices/HUB1/status": _response(200, {"statusCode": 100, "body": {"deviceType": "Hub Mini"}}),
    }

    def _fake_get(url, **kwargs):
        return responses[url.replace(tool.config.SWITCHBOT_API_HOST, "")]

    with patch.object(tool.requests, "get", side_effect=_fake_get), \
         patch.object(tool.switchbot_service, "create_switchbot_auth_headers", return_value={"Authorization": "secret-token"}):
        snapshot = tool.collect()

    assert list(snapshot["hubs"]) == ["HUB1"]
    assert snapshot["hubs"]["HUB1"]["status"]["http_status"] == 200
    assert "secret-token" not in json.dumps(snapshot)


def test_collect_records_request_failure_instead_of_raising():
    with patch.object(tool.requests, "get", side_effect=tool.requests.ConnectionError("boom")), \
         patch.object(tool.switchbot_service, "create_switchbot_auth_headers", return_value={}):
        snapshot = tool.collect()

    assert snapshot["devices_list"]["http_status"] is None
    assert "ConnectionError" in snapshot["devices_list"]["error"]
    assert snapshot["hubs"] == {}


def test_diff_reports_only_changed_fields_and_ignores_capture_time():
    before = {"captured_at": "a", "hubs": {"HUB1": {"status": {"http_status": 200, "body": {"x": 1}}}}}
    after = {"captured_at": "b", "hubs": {"HUB1": {"status": {"http_status": 500, "body": {"x": 1}}}}}

    assert tool.diff(before, after) == ["hubs.HUB1.status.http_status: 200 -> 500"]
    assert tool.diff(before, before) == []


def test_diff_detects_value_becoming_empty():
    before = {"hubs": {"HUB1": {"body": {"deviceType": "Hub Mini"}}}, "list": [{"id": 1}]}
    after = {"hubs": {"HUB1": {"body": {}}}, "list": []}

    assert tool.diff(before, after) == [
        "hubs.HUB1.body: '<なし>' -> {}",
        "hubs.HUB1.body.deviceType: 'Hub Mini' -> '<なし>'",
        "list: '<なし>' -> []",
        "list[0].id: 1 -> '<なし>'",
    ]


def test_main_diff_mode_does_not_call_api(tmp_path, capsys):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"hubs": {}, "v": 1}))
    b.write_text(json.dumps({"hubs": {}, "v": 2}))

    with patch.object(tool.requests, "get") as mock_get:
        assert tool.main(["--diff", str(a), str(b)]) == 0

    mock_get.assert_not_called()
    assert "v: 1 -> 2" in capsys.readouterr().out


def test_main_requires_credentials(monkeypatch, capsys):
    monkeypatch.setattr(tool.config, "SWITCHBOT_API_TOKEN", None)
    assert tool.main([]) == 1
