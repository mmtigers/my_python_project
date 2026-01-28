import time
import subprocess
import os
import sys
from typing import Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import config
from core.logger import setup_logging
from services.notification_service import send_push

# === 設定 ===
TARGET_MAC: str = "F4:4E:FC:B6:65:D4"
CHECK_INTERVAL_HEALTHY: int = 60
MAX_BACKOFF_SECONDS: int = 3600

# ロガーの設定
logger = setup_logging("bluetooth")

class BluetoothMonitor:
    def __init__(self) -> None:
        self.consecutive_failures: int = 0
        self.last_status: str = "UNKNOWN"

    def is_connected(self) -> bool:
        """Bluetoothデバイスが接続されているか確認する"""
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
        """接続を試行し、成功すればPulseAudioのシンクを設定する"""
        logger.info(f"Attempting to connect to {TARGET_MAC}...")
        try:
            subprocess.run(["bluetoothctl", "trust", TARGET_MAC], capture_output=True, timeout=10)
            ret = subprocess.run(["bluetoothctl", "connect", TARGET_MAC], capture_output=True, text=True, timeout=20)
            
            if ret.returncode == 0:
                logger.info("✅ Connection successful. Setting PulseAudio sink...")
                # 音声出力先をBluetoothスピーカーに切り替え
                sink_name = f"bluez_sink.{TARGET_MAC.replace(':', '_')}.a2dp_sink"
                subprocess.run(["pacmd", "set-default-sink", sink_name], capture_output=True)
                return True
            return False
        except subprocess.TimeoutExpired:
            logger.error("Connection attempt timed out.")
            return False

    def run(self) -> None:
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