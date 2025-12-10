# HOME_SYSTEM/server_watchdog.py
import subprocess
import time
import traceback
import logging
from pathlib import Path
from datetime import datetime
import common
import config

# === 設定定数 ===
WATCH_SERVICE_NAME = "home_system.service"
WATCH_PROCESS_NAME = "unified_server.py"
REMINDER_INTERVAL_SEC = 6 * 3600  # 6時間おきにリマインド

# ロックファイル (通知済みフラグ)
LOCK_FILE = Path(config.BASE_DIR) / "watchdog_alert_sent.lock"

# ロガー設定
logger = common.setup_logging("watchdog")

# === メッセージ設定 (主婦向け表現) ===
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
    """Systemdのサービス状態を取得する"""
    try:
        res = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            check=False
        )
        return res.stdout.strip()
    except Exception as e:
        logger.error(f"Systemd check error: {e}")
        return "error"

def is_process_alive(process_keyword: str) -> bool:
    """プロセスが存在するか確認する"""
    try:
        # 自分自身(grep)を除外して検索
        cmd = f"ps aux | grep '{process_keyword}' | grep -v grep"
        res = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=False
        )
        # 結果行が存在すればプロセスは生きている
        return len(res.stdout.strip()) > 0
    except Exception as e:
        logger.error(f"Process check error: {e}")
        return False

def notify_user(text: str, target: str = None, channel: str = "notify"):
    """ユーザーに通知を送る"""
    if target is None:
        target = getattr(config, "NOTIFICATION_TARGET", "line")
    
    # channel引数を渡す
    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": text}], target=target, channel=channel)
    
    # 共通モジュールを使って送信
    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": text}], target=target)

def notify_error_to_admin(error_msg: str):
    """管理者(Discord)にエラーログを送る"""
    common.send_push(
        config.LINE_USER_ID, 
        [{"type": "text", "text": f"😰 **Watchdog Error**\n```{error_msg}```"}], 
        target="discord",
        channel="error"  # ★エラーチャンネルへ
    )

def main():
    try:
        # 1. 状態チェック
        status = get_service_status(WATCH_SERVICE_NAME)
        process_alive = is_process_alive(WATCH_PROCESS_NAME)
        
        # 正常判定: activeまたはactivating、かつプロセスが存在すること
        is_healthy = (status in ["active", "activating"]) and process_alive
        
        logger.info(f"Health Check: Service={status}, Process={'OK' if process_alive else 'NG'}")

        # 2. アクション分岐
        if is_healthy:
            # --- 正常時 ---
            if LOCK_FILE.exists():
                # 前回まで停止していた -> 復旧通知
                notify_user(MSG_RECOVERED, target="discord", channel="notify")
                LOCK_FILE.unlink() # ロック削除
                logger.info("Recovery notification sent.")
        
        else:
            # --- 異常時 ---
            current_time = time.time()
            should_notify = False
            
            if not LOCK_FILE.exists():
                # 新規停止
                should_notify = True
                notify_user(MSG_STOPPED, target="discord", channel="error")
                logger.info("Stop alert sent.")
            else:
                # 継続停止 -> リマインド判定
                last_alert_time = LOCK_FILE.stat().st_mtime
                if current_time - last_alert_time > REMINDER_INTERVAL_SEC:
                    should_notify = True
                    notify_user(MSG_REMINDER, target="discord", channel="error")
                    logger.info("Reminder alert sent.")

            # 通知した場合、ロックファイルのタイムスタンプを更新
            if should_notify:
                LOCK_FILE.touch()

    except Exception:
        # 想定外のエラーはDiscordに投げる
        err_trace = traceback.format_exc()
        logger.error(f"Watchdog crashed: {err_trace}")
        notify_error_to_admin(err_trace)

if __name__ == "__main__":
    main()