import time
import subprocess
import logging
import config
# import common <-- 削除
from core.logger import setup_logging
from services.notification_service import send_push

# === 設定 ===
TARGET_MAC = "F4:4E:FC:B6:65:D4"
CHECK_INTERVAL_HEALTHY = 60
MAX_BACKOFF_SECONDS = 3600

# ロガーの設定
logger = setup_logging("bluetooth")

class BluetoothMonitor:
    def __init__(self):
        self.consecutive_failures = 0
        self.last_status = "UNKNOWN"

    def is_connected(self) -> bool:
        # (変更なし)
        try:
            result = subprocess.run(
                ["bluetoothctl", "info", TARGET_MAC], 
                capture_output=True, text=True, timeout=10
            )
            return "Connected: yes" in result.stdout
        except Exception as e:
            logger.error(f"Status check failed: {e}")
            return False

    def attempt_connect(self) -> bool:
        # (変更なし)
        logger.info(f"Attempting to connect to {TARGET_MAC}...")
        subprocess.run(["bluetoothctl", "trust", TARGET_MAC], capture_output=True)
        ret = subprocess.run(["bluetoothctl", "connect", TARGET_MAC], capture_output=True, text=True)
        
        if ret.returncode == 0:
            logger.info("✅ Connection successful. Setting PulseAudio sink...")
            subprocess.run(["pacmd", "set-default-sink", f"bluez_sink.{TARGET_MAC.replace(':', '_')}.a2dp_sink"], capture_output=True)
            return True
        return False

    def run(self):
        logger.info("Bluetooth Monitor Started")
        while True:
            try:
                if self.is_connected():
                    if self.last_status != "CONNECTED":
                        logger.info(f"✅ {TARGET_MAC} is connected.")
                        self.last_status = "CONNECTED"
                        self.consecutive_failures = 0
                    time.sleep(CHECK_INTERVAL_HEALTHY)
                else:
                    if self.last_status == "CONNECTED":
                        logger.warning(f"⚠️ {TARGET_MAC} disconnected!")
                        # common.send_push -> send_push
                        send_push(
                            config.LINE_USER_ID, 
                            [{"type": "text", "text": f"⚠️ Bluetoothスピーカー切断\n{TARGET_MAC}"}],
                            target="discord"
                        )
                        self.last_status = "DISCONNECTED"

                    success = self.attempt_connect()
                    
                    if success:
                        continue 
                    else:
                        self.consecutive_failures += 1
                        wait_seconds = min(30 * (2 ** (self.consecutive_failures - 1)), MAX_BACKOFF_SECONDS)
                        logger.warning(f"❌ Connection failed (Attempt {self.consecutive_failures}). Waiting {wait_seconds}s...")
                        time.sleep(wait_seconds)

            except Exception as e:
                logger.error(f"Unexpected error in monitor loop: {e}")
                time.sleep(60)

if __name__ == "__main__":
    monitor = BluetoothMonitor()
    monitor.run()