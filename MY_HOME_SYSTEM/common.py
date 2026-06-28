# MY_HOME_SYSTEM/common.py

"""
common.py (Facade Pattern)
Deprecated: This module is kept for backward compatibility.
Please import from 'core.*' or 'services.*' directly in future development.
"""
import sys
import os

# coreパッケージが見えるようにパス調整（必要であれば）
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# --- Core Modules ---
from core.logger import setup_logging, DiscordErrorHandler
from core.utils import get_now_iso, get_today_date_str, get_display_date
from core.network import (
    get_retry_session, 
    create_resilient_session, 
    retry_api_call
)
from core.database import (
    get_db_cursor, 
    execute_read_query, 
    save_log_generic, 
    save_log_async
)

# --- Services ---
# notification_serviceから line_bot_api のインポートを削除しました
from services.notification_service import (
    send_push,
    send_reply,
    get_line_message_quota,
    _send_discord_webhook, 
    _send_line_push        
)

# --- Global Logger for 'common' namespace (Backward Compatibility) ---
logger = setup_logging("common")