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
from core.logger import setup_logging
from core.utils import get_now_iso
from core.database import (
    get_db_cursor,
    execute_read_query
)

# --- Services ---
# notification_serviceから line_bot_api のインポートを削除しました
from services.notification_service import (
    send_push,
)

# Facadeとして再エクスポートしているだけで本ファイル内では未参照のため、
# ruffのF401(unused-import)には`__all__`で意図的な公開シンボルであることを明示する。
# 呼び出し元は`common.get_db_cursor`等の形で広く依存しているため削除しないこと。
__all__ = [
    "setup_logging",
    "get_now_iso",
    "get_db_cursor",
    "execute_read_query",
    "send_push",
    "logger",
]

# --- Global Logger for 'common' namespace (Backward Compatibility) ---
logger = setup_logging("common")