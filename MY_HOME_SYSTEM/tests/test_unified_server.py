# MY_HOME_SYSTEM/tests/test_unified_server.py
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import datetime
import sys
import os
import tempfile

# --- 1. パス解決の追加 ---
# テストファイルのある場所から一つ上のディレクトリ（ルート）をパスに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- 2. Configのモック設定 (import前に実施) ---
mock_config = MagicMock()

# StaticFiles は実在するディレクトリパスを要求するため、一時フォルダを割り当てる
temp_dir = tempfile.mkdtemp()
mock_config.ASSETS_DIR = temp_dir
mock_config.QUEST_DIST_DIR = temp_dir
mock_config.UPLOAD_DIR = temp_dir # ★追加: これがないと StaticFiles でエラーになる

# その他の設定値
mock_config.LINE_USER_ID = "test_user_id"
mock_config.LINE_CHANNEL_SECRET = "dummy_secret" 
mock_config.LINE_CHANNEL_ACCESS_TOKEN = "dummy_token"
mock_config.SQLITE_DB_PATH = ":memory:"
mock_config.MONITOR_DEVICES = [
    {"id": "mac_motion", "name": "Motion1", "location": "Living", "type": "Motion Sensor"},
    {"id": "mac_contact", "name": "Door1", "location": "Entrance", "type": "Contact Sensor"}
]
mock_config.SQLITE_TABLE_SENSOR = "sensor_logs"

# モックをシステムモジュールとして登録
sys.modules["config"] = mock_config

# 依存モジュールのモック化
sys.modules["common"] = MagicMock()
sys.modules["switchbot_get_device_list"] = MagicMock()
sys.modules["handlers"] = MagicMock()
sys.modules["handlers.line_logic"] = MagicMock()
sys.modules["backup_database"] = MagicMock()
sys.modules["routers"] = MagicMock()
sys.modules["routers.quest_router"] = MagicMock()

# --- 3. サーバーモジュールのインポート ---
# ここで初めて import することで、上記のモック設定が適用されます
import unified_server

@pytest.fixture
def mock_common():
    return sys.modules["common"]

@pytest.mark.asyncio
async def test_switchbot_motion_detected(mock_common):
    """
    SwitchBot: 人感センサー 'detected' 受信時の挙動確認
    """
    # 状態リセット
    unified_server.IS_ACTIVE.clear()
    unified_server.MOTION_TASKS.clear()
    
    # リクエストボディのモック作成 (Pydanticモデル導入前の辞書形式)
    # ※Step 2のリファクタリング後はここを修正する必要がありますが、まずは現状確認
    request = MagicMock()
    request.json = AsyncMock(return_value={
        "context": {
            "deviceMac": "mac_motion",
            "detectionState": "DETECTED",
            "brightness": "bright"
        }
    })

    # 実行
    await unified_server.callback_switchbot(request)

    # 検証
    assert unified_server.IS_ACTIVE["mac_motion"] is True
    mock_common.send_push.assert_called()
    args, _ = mock_common.send_push.call_args
    assert "動きがありました" in args[1][0]["text"]

@pytest.mark.asyncio
async def test_switchbot_motion_not_detected_timer(mock_common):
    """
    SwitchBot: 人感センサー 'not_detected' 受信時のタイマーセット確認
    """
    unified_server.IS_ACTIVE["mac_motion"] = True
    
    request = MagicMock()
    request.json = AsyncMock(return_value={
        "context": {
            "deviceMac": "mac_motion",
            "detectionState": "NOT_DETECTED"
        }
    })

    with patch("asyncio.create_task") as mock_create_task:
        await unified_server.callback_switchbot(request)
        assert "mac_motion" in unified_server.MOTION_TASKS
        mock_create_task.assert_called()

@pytest.mark.asyncio
async def test_switchbot_contact_cooldown(mock_common):
    """
    SwitchBot: 開閉センサーの連打防止 (Cooldown) 確認
    """
    unified_server.LAST_NOTIFY_TIME.clear()
    request = MagicMock()
    request.json = AsyncMock(return_value={
        "context": {
            "deviceMac": "mac_contact",
            "detectionState": "OPEN"
        }
    })

    # 1回目: 通知される
    await unified_server.callback_switchbot(request)
    assert mock_common.send_push.call_count == 1
    
    # 2回目 (直後): 通知されない
    mock_common.send_push.reset_mock()
    await unified_server.callback_switchbot(request)
    mock_common.send_push.assert_not_called()