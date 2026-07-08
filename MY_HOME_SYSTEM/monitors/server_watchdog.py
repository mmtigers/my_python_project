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
    """
    try:
        res = subprocess.run(
            ["pgrep", "-f", process_keyword], 
            capture_output=True, text=True, check=False
        )
        return res.returncode == 0
    except Exception:
        return False

def check_throttling_status():
    """
    Raspberry Piのハードウェア健全性（スロットリングや電圧低下）を確認する。
    """
    try:
        # 【修正点1】 check=True を外し、コマンド自体の失敗でPythonをクラッシュさせない
        result = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True)
        
        # コマンドが失敗した場合（OSビジー状態など）は安全にスキップ
        if result.returncode != 0:
            logger.debug(f"vcgencmd returned non-zero exit status: {result.returncode}")
            return

        if 'throttled=' in result.stdout:
            val_str = result.stdout.split('=')[1].strip()
            val = int(val_str, 16)
            
            if val == 0:
                return  # 完全に正常

            # ビットマスクの定義
            ACTIVE_MASK = 0x0000F   # 現在発生中 (Bit 0-3)
            HISTORY_MASK = 0xF0000  # 過去の履歴 (Bit 16-19)
            
            active_issues = val & ACTIVE_MASK
            history_issues = val & HISTORY_MASK
            
            # 1. 現在発生中の異常がある場合
            if active_issues != 0:
                msg = f"Active Hardware Throttling/Under-voltage Detected! Code: {hex(val)}"
                # 【修正点2】 core/logger.py の仕様上 logger.error だけでDiscordに自動送信されるため、
                # send_push を削除して二重通知のスパムを防ぎます。
                logger.error(f"⚠️ System Alert: {msg}")
                
            # 2. 過去の履歴のみの場合 (自動復旧済み)
            elif history_issues != 0:
                logger.warning(f"Hardware Throttling History (Recovered): {hex(val)}")
                
    except FileNotFoundError:
        logger.debug("vcgencmd not found, skipping throttling check.")
    except Exception as e:
        # 万が一の予期せぬエラーも、無限ループを防ぐためにWARNINGに落とす
        logger.warning(f"Throttling Check failed (Non-critical): {e}")

def check_health() -> None:
    """
    サービスの生存確認を行い、異常があれば通知を送信する
    """
    try:
        logger.debug("🔍 Watchdog check started...")
        
        status = get_service_status(WATCH_SERVICE_NAME)
        process_alive = is_process_alive(WATCH_PROCESS_NAME)
        
        is_healthy = (status in ["active", "activating"]) and process_alive
        process_status_str = 'OK' if process_alive else 'NG'

        if is_healthy:
            logger.debug("Health Check: Service=%s, Process=%s", status, process_status_str)
            
            if LOCK_FILE.exists():
                send_push(config.LINE_USER_ID, [{"type": "text", "text": MSG_RECOVERED}], target="discord", channel="notify")
                LOCK_FILE.unlink()
                logger.info("Recovery notification sent.")
        else:
            logger.warning("⚠️ Unhealthy State Detected: Service=%s, Process=%s", status, process_status_str)

            current_time = time.time()
            should_notify = False
            
            if not LOCK_FILE.exists():
                should_notify = True
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
    # ハードウェアの健全性確認（スロットリング監視）
    check_throttling_status()
    # ソフトウェアの健全性確認（プロセス死活監視）
    check_health()