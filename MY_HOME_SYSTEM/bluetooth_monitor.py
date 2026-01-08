import time
import subprocess
import logging
import common
import config

# === 設定 ===
TARGET_MAC = "F4:4E:FC:B6:65:D4"  # 対象のBluetoothスピーカー
CHECK_INTERVAL_HEALTHY = 60       # 正常時の確認間隔（秒）
MAX_BACKOFF_SECONDS = 3600        # 再接続失敗時の最大待機時間（1時間）

# ロガーの設定
logger = common.setup_logging("bluetooth")

class BluetoothMonitor:
    def __init__(self):
        self.consecutive_failures = 0
        self.last_status = "UNKNOWN" # 'CONNECTED', 'DISCONNECTED', 'UNKNOWN'

    def is_connected(self) -> bool:
        """Bluetoothctlを使用して接続状態を確認"""
        try:
            # bluetoothctl info <MAC> の出力を解析
            result = subprocess.run(
                ["bluetoothctl", "info", TARGET_MAC], 
                capture_output=True, text=True, timeout=10
            )
            return "Connected: yes" in result.stdout
        except Exception as e:
            logger.error(f"Status check failed: {e}")
            return False

    def attempt_connect(self) -> bool:
        """接続を試行し、成功したらPulseAudioのシンクを設定する"""
        logger.info(f"Attempting to connect to {TARGET_MAC}...")
        
        # 1. Trust (念のため)
        subprocess.run(["bluetoothctl", "trust", TARGET_MAC], capture_output=True)
        
        # 2. Connect
        ret = subprocess.run(["bluetoothctl", "connect", TARGET_MAC], capture_output=True)
        
        # 接続確立のネゴシエーション時間を考慮して少し待つ
        time.sleep(5)
        
        if self.is_connected():
            self._configure_audio_sink()
            return True
        return False

    def _configure_audio_sink(self):
        """音声出力先をBluetoothスピーカーに切り替える"""
        try:
            # MACアドレスをPulseAudio形式に変換 (xx:xx -> xx_xx)
            sink_name = f"bluez_output.{TARGET_MAC.replace(':', '_')}.1"
            
            # デフォルトシンクに設定
            subprocess.run(["pactl", "set-default-sink", sink_name], check=False)
            # 音量を100%に設定
            subprocess.run(["pactl", "set-sink-volume", sink_name, "100%"], check=False)
            logger.info(f"🔊 Audio sink set to {sink_name}")
        except Exception as e:
            logger.warning(f"Audio sink configuration failed: {e}")

    def run(self):
        logger.info("🎧 Bluetooth Monitor started (Daemon Mode).")
        
        # 起動直後の初回チェック
        if self.is_connected():
            self.last_status = "CONNECTED"
            logger.info("✅ Initial Status: Connected.")
        else:
            self.last_status = "DISCONNECTED"
            logger.warning("⚠️ Initial Status: Disconnected.")

        while True:
            try:
                currently_connected = self.is_connected()

                if currently_connected:
                    # --- 接続中 ---
                    if self.last_status != "CONNECTED":
                        logger.info("🎉 Speaker reconnected!")
                        common.send_push(
                            config.LINE_USER_ID, 
                            [{"type": "text", "text": "✅ Bluetoothスピーカーが復旧しました"}],
                            target="discord"
                        )
                        self._configure_audio_sink()
                        self.last_status = "CONNECTED"
                        self.consecutive_failures = 0 # カウンタリセット
                    
                    # 正常時は定期的(1分)にチェック
                    time.sleep(CHECK_INTERVAL_HEALTHY)

                else:
                    # --- 切断中 ---
                    if self.last_status == "CONNECTED":
                        logger.warning("⚠️ Speaker disconnected detected.")
                        common.send_push(
                            config.LINE_USER_ID, 
                            [{"type": "text", "text": "⚠️ Bluetoothスピーカー切断を検知"}],
                            target="discord"
                        )
                        self.last_status = "DISCONNECTED"

                    # 再接続トライ
                    success = self.attempt_connect()
                    
                    if success:
                        # ループの先頭に戻り、CONNECTED状態の処理を行う
                        continue 
                    else:
                        # 失敗 -> Backoff計算
                        self.consecutive_failures += 1
                        
                        # 指数バックオフ: 30秒, 60秒, 120秒 ... 最大1時間
                        # (2の(失敗回数-1)乗 * 30秒)
                        wait_seconds = min(30 * (2 ** (self.consecutive_failures - 1)), MAX_BACKOFF_SECONDS)
                        
                        log_msg = f"❌ Connection failed (Attempt {self.consecutive_failures}). Waiting {wait_seconds}s..."
                        
                        # 最初の数回は警告ログ、それ以降は頻度を落とすかInfoレベルにするなど調整
                        if self.consecutive_failures <= 5:
                            logger.warning(log_msg)
                        else:
                            # 長期切断中はログレベルを下げてノイズを減らす
                            logger.info(log_msg)
                            
                        time.sleep(wait_seconds)

            except Exception as e:
                logger.error(f"Unexpected error in monitor loop: {e}")
                time.sleep(60) # 予期せぬエラー時は1分待機

if __name__ == "__main__":
    monitor = BluetoothMonitor()
    monitor.run()