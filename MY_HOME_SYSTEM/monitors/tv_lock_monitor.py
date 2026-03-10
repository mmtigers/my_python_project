# MY_HOME_SYSTEM/monitors/tv_lock_monitor.py
import sys
import os
from datetime import datetime

# プロジェクトルートへのパス解決
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import config
from core.logger import setup_logging
from services import switchbot_service

logger = setup_logging("monitor.tv_lock")

# 重複実行を防ぐため、実行状態を記録するファイル
LAST_RUN_FILE = os.path.join(config.FALLBACK_ROOT, "last_tv_lock.txt")

def main():
    if not config.TV_PLUG_DEVICE_ID:
        logger.debug("TV_PLUG_DEVICE_ID is not set. Skipping.")
        return

    now = datetime.now()
    
    # 毎日 深夜 2:00 〜 2:05 の間に実行する
    if now.hour == 2 and 0 <= now.minute <= 5:
        today_str = now.strftime("%Y-%m-%d")
        
        # すでに本日実行済みかチェック
        if os.path.exists(LAST_RUN_FILE):
            with open(LAST_RUN_FILE, "r") as f:
                last_run = f.read().strip()
            if last_run == today_str:
                return # すでに実行済み

        logger.info("📺 [TV Lock] Executing midnight TV lock (Turn OFF).")
        try:
            res = switchbot_service.send_device_command(config.TV_PLUG_DEVICE_ID, "turnOff")
            if res and res.get("statusCode") == 100:
                logger.info("✅ [TV Lock] Successfully turned off TV plug.")
                
                # 実行完了の記録を保存
                os.makedirs(os.path.dirname(LAST_RUN_FILE), exist_ok=True)
                with open(LAST_RUN_FILE, "w") as f:
                    f.write(today_str)
            else:
                logger.error(f"❌ [TV Lock] API Error: {res}")
        except Exception as e:
            logger.error(f"❌ [TV Lock] Exception during turn off: {e}")

if __name__ == "__main__":
    main()