# HOME_SYSTEM/server_watchdog.py
import subprocess
import time
import traceback
from pathlib import Path
import common
import config

# === 設定 ===
WATCH_SERVICE_NAME = "home_system.service"
WATCH_PROCESS_NAME = "unified_server.py"
REMINDER_INTERVAL_SEC = 6 * 3600  # 6時間

LOCK_FILE = Path(config.BASE_DIR) / "watchdog_alert_sent.lock"
logger = common.setup_logging("watchdog")

# === メッセージ (主婦向け) ===
MSG_STOPPED = (
    "あら、サーバーが止まっちゃったみたいです💦\n"
    "パパに確認してもらってくださいね🙇\n"
    "(自動監視システムより)"
)
MSG_RECOVERED = (
    "お待たせしました！\n"
    "サーバーが復活しました✨\n"
    "もう大丈夫ですよ😊"
)
MSG_REMINDER = (
    "まだサーバーが止まっているようです😢\n"
    "お時間ある時に確認お願いします💦"
)

def get_service_status(service_name: str) -> str:
    try:
        res = subprocess.run(["systemctl", "is-active", service_name], capture_output=True, text=True, check=False)
        return res.stdout.strip()
    except Exception:
        return "error"

def is_process_alive(process_keyword: str) -> bool:
    try:
        # 自分自身を除外
        cmd = f"ps aux | grep '{process_keyword}' | grep -v grep"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)
        return len(res.stdout.strip()) > 0
    except Exception:
        return False

def main():
    try:
        # チェック
        status = get_service_status(WATCH_SERVICE_NAME)
        process_alive = is_process_alive(WATCH_PROCESS_NAME)
        is_healthy = (status in ["active", "activating"]) and process_alive
        
        logger.info(f"Health Check: Service={status}, Process={'OK' if process_alive else 'NG'}")

        if is_healthy:
            if LOCK_FILE.exists():
                # 復旧通知 (target=Noneでconfigに従うが、緊急系はDiscordにも送ると良い)
                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": MSG_RECOVERED}], target="discord", channel="notify")
                LOCK_FILE.unlink()
                logger.info("Recovery notification sent.")
        else:
            current_time = time.time()
            should_notify = False
            
            if not LOCK_FILE.exists():
                should_notify = True
                # 異常時はDiscordのエラーチャンネルへ
                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": MSG_STOPPED}], target="discord", channel="error")
                logger.info("Stop alert sent.")
            else:
                if current_time - LOCK_FILE.stat().st_mtime > REMINDER_INTERVAL_SEC:
                    should_notify = True
                    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": MSG_REMINDER}], target="discord", channel="error")
                    logger.info("Reminder alert sent.")

            if should_notify:
                LOCK_FILE.touch()

    except Exception:
        err = traceback.format_exc()
        logger.error(f"Watchdog Crashed: {err}")
        # commonのロガーが自動でDiscordに飛ばすが、念のため
        pass

if __name__ == "__main__":
    main()