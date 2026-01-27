# MY_HOME_SYSTEM/monitors/clinic_monitor.py
import os
import sys
import requests
from datetime import datetime
from typing import Dict, Optional

# プロジェクトルートへのパス解決 (単体実行用)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging

# ロガー設定
logger = setup_logging("clinic_monitor")

class ClinicMonitor:
    """
    伊丹たかの小児科の予約ページHTMLを定期収集するモニタークラス。
    
    Attributes:
        url (str): 監視対象のURL
        save_dir (str): HTML保存先ディレクトリ
        timeout (int): リクエストタイムアウト(秒)
        user_agent (str): リクエストヘッダーのUser-Agent
    """

    def __init__(self) -> None:
        """設定をロードし、初期化を行う"""
        self.url: str = getattr(config, "CLINIC_MONITOR_URL", "")
        self.save_dir: str = getattr(config, "CLINIC_HTML_DIR", "")
        self.timeout: int = getattr(config, "CLINIC_REQUEST_TIMEOUT", 10)
        self.user_agent: str = getattr(config, "CLINIC_USER_AGENT", "MyHomeSystem/1.0")

        # 設定不備チェック (CRITICALではなくERRORで停止)
        if not self.url or not self.save_dir:
            logger.error("❌ Config Invalid: CLINIC_MONITOR_URL or CLINIC_HTML_DIR is missing.")
            sys.exit(1)

    def is_operating_hours(self) -> bool:
        """
        現在時刻が監視対象の時間帯かチェックする。

        Returns:
            bool: 実行すべき時間帯であれば True
        """
        current_hour: int = datetime.now().hour
        start: int = getattr(config, "CLINIC_MONITOR_START_HOUR", 8)
        end: int = getattr(config, "CLINIC_MONITOR_END_HOUR", 19)
        return start <= current_hour <= end

    def save_html(self, content: bytes) -> None:
        """
        取得したHTMLバイナリをファイルに保存する。

        Args:
            content (bytes): レスポンスボディ
        """
        timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename: str = f"clinic_{timestamp}.html"
        filepath: str = os.path.join(self.save_dir, filename)

        try:
            with open(filepath, "wb") as f:
                f.write(content)
            logger.info(f"✅ Saved HTML: {filename} ({len(content)} bytes)")
        except OSError as e:
            # ディスクIOエラーは Warning ではなく Error 扱い
            logger.error(f"❌ Failed to save HTML to {filepath}: {e}")

    def run(self) -> None:
        """
        メイン実行処理。
        時間帯チェックを行い、対象であればHTMLを取得して保存する。
        """
        # 1. 時間帯チェック
        if not self.is_operating_hours():
            # 正常動作範囲内のスキップなので INFO で記録 (ノイズ低減のため文言を簡素化)
            logger.info("💤 Out of operating hours. Task skipped.")
            return

        logger.info(f"Fetching clinic status from: {self.url}")

        headers: Dict[str, str] = {
            "User-Agent": self.user_agent
        }

        # 2. HTML取得
        try:
            response: requests.Response = requests.get(
                self.url, 
                headers=headers, 
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                # 3. 保存
                self.save_html(response.content)
            else:
                # 相手サーバーエラー (500系) や 404 は WARNING (一時的な可能性が高いため)
                logger.warning(f"⚠️ HTTP Error: {response.status_code} - {response.reason}")

        except requests.exceptions.RequestException as e:
            # 接続エラーはリトライせず、次のスケジュールまで待機 (WARNING)
            logger.warning(f"⚠️ Connection failed: {e}")
        except Exception as e:
            # 想定外のコードエラー等は ERROR
            logger.error(f"💀 Unexpected Error in ClinicMonitor: {e}", exc_info=True)

if __name__ == "__main__":
    monitor = ClinicMonitor()
    monitor.run()