# MY_HOME_SYSTEM/monitors/clinic_monitor.py
import os
import sys
import requests
from datetime import datetime
from typing import Dict, Optional, Any

# プロジェクトルートへのパス解決 (単体実行用)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging

# ロガー設定
logger = setup_logging("clinic_monitor")

class ClinicMonitor:
    """
    小児科予約ページのHTMLを定期収集するモニタークラス。
    """

    def __init__(self) -> None:
        """設定をロードし、初期化を行う。"""
        self.url: str = getattr(config, "CLINIC_MONITOR_URL", "https://ssc6.doctorqube.com/itami-shounika/")
        self.save_dir: str = getattr(config, "CLINIC_HTML_DIR", os.path.join(config.ASSETS_DIR, "clinic_html"))
        self.timeout: int = getattr(config, "CLINIC_REQUEST_TIMEOUT", 10)
        self.user_agent: str = getattr(config, "CLINIC_USER_AGENT", "MyHomeSystem/1.0")
        
        # 稼働時間の設定 (デフォルト: 6時〜19時)
        self.start_hour: int = getattr(config, "CLINIC_MONITOR_START_HOUR", 6)
        self.end_hour: int = getattr(config, "CLINIC_MONITOR_END_HOUR", 19)

        # 保存ディレクトリの作成
        if not os.path.exists(self.save_dir):
            try:
                os.makedirs(self.save_dir, exist_ok=True)
                logger.info(f"📁 Created directory: {self.save_dir}")
            except Exception as e:
                logger.error(f"❌ Failed to create directory {self.save_dir}: {e}")

    def is_operating_hours(self) -> bool:
        """現在時刻が監視対象の時間帯内（診察・予約時間内）か判定する。"""
        now_hour: int = datetime.now().hour
        return self.start_hour <= now_hour < self.end_hour

    def save_html(self, content: bytes) -> None:
        """取得したHTMLコンテンツをファイルに保存する。"""
        timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename: str = f"clinic_{timestamp}.html"
        filepath: str = os.path.join(self.save_dir, filename)

        try:
            with open(filepath, "wb") as f:
                f.write(content)
            logger.info(f"💾 Saved HTML: {filename}")
        except OSError as e:
            logger.error(f"❌ Disk IO Error at {filepath}: {e}")

    def run(self) -> None:
        """
        メイン実行処理。
        時間帯チェックを行い、対象であればHTMLを取得して保存する。
        """
        if not self.is_operating_hours():
            logger.info(f"💤 Out of operating hours ({self.start_hour}-{self.end_hour}). Task skipped.")
            return

        if not self.url:
            logger.error("❌ Clinic URL is not configured.")
            return

        headers: Dict[str, str] = {"User-Agent": self.user_agent}

        try:
            logger.info(f"🌐 Fetching clinic status: {self.url}")
            response: requests.Response = requests.get(
                self.url, 
                headers=headers, 
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                self.save_html(response.content)
            else:
                logger.warning(f"⚠️ HTTP Error: {response.status_code} - {response.reason}")

        except requests.exceptions.RequestException as e:
            logger.warning(f"⚠️ Connection failed: {e}")
        except Exception as e:
            logger.error(f"🔥 Unexpected error in ClinicMonitor: {e}")

if __name__ == "__main__":
    monitor = ClinicMonitor()
    monitor.run()