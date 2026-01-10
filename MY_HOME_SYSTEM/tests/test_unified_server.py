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

# --- 3. 依存モジュールのモック化 (★ここを修正) ---
# unified_server.py が直接インポートしているモジュールを全てモックにする
sys.modules["common"] = MagicMock()
sys.modules["core"] = MagicMock()
sys.modules["core.logger"] = MagicMock()
sys.modules["core.utils"] = MagicMock()
sys.modules["core.database"] = MagicMock()
sys.modules["services.notification_service"] = MagicMock() # ★重要: これを監視対象にする

sys.modules["services.switchbot_service"] = MagicMock()
sys.modules["handlers"] = MagicMock()
sys.modules["handlers.line_logic"] = MagicMock()
sys.modules["services.backup_service"] = MagicMock()
sys.modules["routers"] = MagicMock()
sys.modules["routers.quest_router"] = MagicMock()

# --- 4. サーバーモジュール & モデルのインポート ---
import unified_server
from models.switchbot import SwitchBotWebhookBody, SwitchBotContext

@pytest.fixture
def mock_notification():
    """通知サービスのモックを返すフィクスチャ"""
    mock = sys.modules["services.notification_service"]
    mock.reset_mock()
    return mock

@pytest.mark.asyncio
async def test_switchbot_motion_detected(mock_notification):
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
    
    # ★修正: commonではなくmock_notificationをチェック
    mock_notification.send_push.assert_called()
    args = mock_notification.send_push.call_args[0]
    assert "動きがありました" in args[1][0]["text"]

@pytest.mark.asyncio
async def test_switchbot_motion_not_detected_timer(mock_notification):
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
async def test_switchbot_contact_cooldown(mock_notification):
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
    assert mock_notification.send_push.call_count == 1
    
    # 2回目 (直後): 通知されない
    mock_notification.send_push.reset_mock()
    await unified_server.callback_switchbot(body)
    mock_notification.send_push.assert_not_called()