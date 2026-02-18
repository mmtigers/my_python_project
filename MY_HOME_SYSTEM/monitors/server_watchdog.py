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

def check_throttling_status() -> None:
    """
    Raspberry Piのスロットリング状態（電圧・温度制限）を確認する。
    現在の異常と過去の履歴を分離し、現在の異常のみを通知する。
    """
    try:
        res = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, check=False
        )
        
        if res.returncode != 0:
            return

        output = res.stdout.strip()
        if "throttled=" not in output:
            return
            
        hex_str = output.split("=")[1]
        throttled_val = int(hex_str, 16)
        
        # 下位4ビットの抽出 (Bit 0-3: 現在発生中のエラー)
        # 0x01: Under-voltage, 0x02: ARM frequency capped, 0x04: Currently throttled, 0x08: Soft temperature limit
        current_issues = throttled_val & 0x0F
        
        if current_issues != 0:
            # 現在進行形の電圧低下・熱制限 (ERROR -> 即時通知対象)
            msg = f"System Alert\nCURRENT Throttling Detected: {hex_str}\n※Raspberry Piが高負荷・または電圧低下中です。"
            logger.error(msg.replace("\n", " "))
            
            # 通知バッファの汚染を防ぐため、リストはローカルで明示的に定義して渡す
            send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel="error")
            
        elif throttled_val != 0:
            # 過去の履歴のみ (WARNING -> ログのみ、通知しない)
            logger.warning(f"History Throttling Flag Detected (Code: {hex_str}). System recovered.")
            
        else:
            logger.debug("System voltage and temperature are normal (0x0).")

    except FileNotFoundError:
        # 開発環境（Mac/Windows等）で vcgencmd がない場合はスキップ
        logger.debug("vcgencmd command not found. Skipping hardware health check.")
    except Exception as e:
        logger.error(f"Failed to check throttling status: {e}")

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