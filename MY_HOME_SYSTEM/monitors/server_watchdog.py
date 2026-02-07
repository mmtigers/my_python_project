# MY_HOME_SYSTEM/monitors/server_watchdog.py
import subprocess
import time
import traceback
from pathlib import Path
import sys
import os
from typing import Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging
from services.notification_service import send_push

# === 設定 ===
WATCH_SERVICE_NAME: str = "home_system.service"
WATCH_PROCESS_NAME: str = "unified_server.py"
REMINDER_INTERVAL_SEC: int = 6 * 3600  # 6時間

LOCK_FILE: Path = Path(config.BASE_DIR) / "watchdog_alert_sent.lock"
logger = setup_logging("watchdog")

# === メッセージ (主婦向け) ===
MSG_STOPPED: str = (
    "あら、サーバーが止まっちゃったみたいです💦\n"
    "パパに確認してもらってくださいね🙇\n"
    "(自動監視システムより)"
)
MSG_RECOVERED: str = (
    "お待たせしました！\n"
    "サーバーが復活しました✨\n"
    "もう大丈夫ですよ😊"
)
MSG_REMINDER: str = (
    "まだサーバーが止まっているようです😢\n"
    "お時間ある時に確認お願いします💦"
)

def get_service_status(service_name: str) -> str:
    """
    systemctlを使ってサービスのステータスを確認する
    
    Returns:
        str: 'active', 'inactive', 'failed', or 'error'
    """
    try:
        res = subprocess.run(
            ["systemctl", "is-active", service_name], 
            capture_output=True, text=True, check=False
        )
        return res.stdout.strip()
    except Exception:
        return "error"

def is_process_alive(process_keyword: str) -> bool:
    """
    pgrepを使ってプロセスが起動しているか確認する。
    
    Args:
        process_keyword (str): 検索するプロセス名のキーワード
        
    Returns:
        bool: プロセスが存在すればTrue
    """
    try:
        # pgrep -f [pattern]
        res = subprocess.run(
            ["pgrep", "-f", process_keyword], 
            capture_output=True, text=True, check=False
        )
        # 終了コード0ならプロセスが存在する
        return res.returncode == 0
    except Exception:
        return False

def check_health() -> None:
    """
    サービスの生存確認を行い、異常があれば通知を送信する
    """
    try:
        logger.debug("🔍 Watchdog check started...")
        
        status = get_service_status(WATCH_SERVICE_NAME)
        process_alive = is_process_alive(WATCH_PROCESS_NAME)
        
        # サービスが active または activating で、かつプロセスが生きていれば正常
        is_healthy = (status in ["active", "activating"]) and process_alive
        
        process_status_str = 'OK' if process_alive else 'NG'

        if is_healthy:
            # Log Level Adjustment: DEBUG for healthy state
            logger.debug("Health Check: Service=%s, Process=%s", status, process_status_str)
            
            if LOCK_FILE.exists():
                # 復旧通知
                send_push(config.LINE_USER_ID, [{"type": "text", "text": MSG_RECOVERED}], target="discord", channel="notify")
                LOCK_FILE.unlink()
                logger.info("Recovery notification sent.")
        else:
            # 異常検知時は WARNING でログに残す
            logger.warning("⚠️ Unhealthy State Detected: Service=%s, Process=%s", status, process_status_str)

            current_time = time.time()
            should_notify = False
            
            if not LOCK_FILE.exists():
                should_notify = True
                # 異常時はDiscordのエラーチャンネルへ
                send_push(config.LINE_USER_ID, [{"type": "text", "text": MSG_STOPPED}], target="discord", channel="error")
                logger.info("Stop alert sent.")
            else:
                if current_time - LOCK_FILE.stat().st_mtime > REMINDER_INTERVAL_SEC:
                    should_notify = True
                    send_push(config.LINE_USER_ID, [{"type": "text", "text": MSG_REMINDER}], target="discord", channel="error")
                    logger.info("Reminder alert sent.")

            if should_notify:
                LOCK_FILE.touch()

    except Exception:
        err = traceback.format_exc()
        logger.error("Watchdog Crashed: %s", err)

if __name__ == "__main__":
    check_health()