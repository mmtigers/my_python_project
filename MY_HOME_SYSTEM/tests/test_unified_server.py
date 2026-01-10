# MY_HOME_SYSTEM/tests/test_unified_server.py
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import datetime
import sys
import os
import tempfile

# --- 1. パス解決の追加 ---
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- 2. Configのモック設定 ---
mock_config = MagicMock()
temp_dir = tempfile.mkdtemp()
mock_config.ASSETS_DIR = temp_dir
mock_config.QUEST_DIST_DIR = temp_dir
mock_config.UPLOAD_DIR = temp_dir
mock_config.LINE_USER_ID = "test_user_id"
mock_config.LINE_CHANNEL_SECRET = "dummy_secret" 
mock_config.LINE_CHANNEL_ACCESS_TOKEN = "dummy_token"
mock_config.SQLITE_DB_PATH = ":memory:"
mock_config.MONITOR_DEVICES = [
    {"id": "mac_motion", "name": "Motion1", "location": "Living", "type": "Motion Sensor"},
    {"id": "mac_contact", "name": "Door1", "location": "Entrance", "type": "Contact Sensor"}
]
mock_config.SQLITE_TABLE_SENSOR = "sensor_logs"
mock_config.CORS_ORIGINS = ["*"] 

sys.modules["config"] = mock_config

# 依存モジュールのモック化
sys.modules["common"] = MagicMock()
sys.modules["services.switchbot_service"] = MagicMock()
sys.modules["handlers"] = MagicMock()
sys.modules["handlers.line_logic"] = MagicMock()
sys.modules["services.backup_service"] = MagicMock()
sys.modules["routers"] = MagicMock()
sys.modules["routers.quest_router"] = MagicMock()

# --- 3. サーバーモジュール & モデルのインポート ---
import unified_server
from models.switchbot import SwitchBotWebhookBody, SwitchBotContext

@pytest.fixture
def mock_common():
    """commonモジュールのモックを返すフィクスチャ（テスト毎にリセット）"""
    mock = sys.modules["common"]
    mock.reset_mock()  # ★修正: 前のテストの呼び出し履歴を消去
    return mock

@pytest.mark.asyncio
async def test_switchbot_motion_detected(mock_common):
    """
    SwitchBot: 人感センサー 'detected' 受信時の挙動確認
    """
    # 状態リセット
    unified_server.IS_ACTIVE.clear()
    unified_server.MOTION_TASKS.clear()
    
    body = SwitchBotWebhookBody(
        eventType="changeReport",
        eventVersion="1.0",
        context=SwitchBotContext(
            deviceMac="mac_motion",
            detectionState="DETECTED",
            brightness="bright"
        ),
        deviceType="Motion Sensor"
    )

    # 実行
    await unified_server.callback_switchbot(body)

    # 検証
    assert unified_server.IS_ACTIVE["mac_motion"] is True
    mock_common.send_push.assert_called()
    args = mock_common.send_push.call_args[0]
    assert "動きがありました" in args[1][0]["text"]

@pytest.mark.asyncio
async def test_switchbot_motion_not_detected_timer(mock_common):
    """
    SwitchBot: 人感センサー 'not_detected' 受信時のタイマーセット確認
    """
    unified_server.IS_ACTIVE["mac_motion"] = True
    
    body = SwitchBotWebhookBody(
        eventType="changeReport",
        eventVersion="1.0",
        context=SwitchBotContext(
            deviceMac="mac_motion",
            detectionState="NOT_DETECTED"
        ),
        deviceType="Motion Sensor"
    )

    with patch("asyncio.create_task") as mock_create_task:
        await unified_server.callback_switchbot(body)
        assert "mac_motion" in unified_server.MOTION_TASKS
        mock_create_task.assert_called()

@pytest.mark.asyncio
async def test_switchbot_contact_cooldown(mock_common):
    """
    SwitchBot: 開閉センサーの連打防止 (Cooldown) 確認
    """
    unified_server.LAST_NOTIFY_TIME.clear()
    
    body = SwitchBotWebhookBody(
        eventType="changeReport",
        eventVersion="1.0",
        context=SwitchBotContext(
            deviceMac="mac_contact",
            detectionState="OPEN"
        ),
        deviceType="Contact Sensor"
    )

    # 1回目: 通知される
    await unified_server.callback_switchbot(body)
    assert mock_common.send_push.call_count == 1
    
    # 2回目 (直後): 通知されない
    mock_common.send_push.reset_mock() # ここでのリセットも有効だが、テスト開始時のリセットが重要
    await unified_server.callback_switchbot(body)
    mock_common.send_push.assert_not_called()