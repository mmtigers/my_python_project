import os
import sys
import requests
from datetime import datetime
from typing import Dict, Optional

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging

# Rule 8.1: 指定ロガーインスタンスの使用 [cite: 143]
logger = setup_logging("clinic_monitor")

class ClinicMonitor:
    """
    伊丹たかの小児科の予約ページHTMLを定期収集するモニタークラス。
    
    Attributes:
        url (str): 監視対象のURL。
        save_dir (str): HTML保存先ディレクトリ。
        timeout (int): リクエストタイムアウト(秒)。
        user_agent (str): リクエストヘッダーのUser-Agent。
    """

    def __init__(self) -> None:
        """設定をロードし、初期化を行う。"""
        self.url: str = getattr(config, "CLINIC_MONITOR_URL", "")
        
        # Robustness Fix: config.py で解決された安全なパスを使用するが、念のためディレクトリ生成を試みる
        base_dir = getattr(config, "CLINIC_HTML_DIR", "")
        if not base_dir:
            # 万が一 config が空の場合のデフォルト
            base_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "clinic_html")
            
        self.save_dir: str = base_dir
        
        # ディレクトリ準備 (エラーでもクラッシュさせない)
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"❌ Failed to create save directory '{self.save_dir}'. Saving to monitor directory. Error: {e}")
            self.save_dir = os.path.dirname(__file__) # 最終フォールバック

        self.timeout: int = getattr(config, "CLINIC_REQUEST_TIMEOUT", 10)
        self.user_agent: str = getattr(config, "CLINIC_USER_AGENT", "MyHomeSystem/1.0")

        if not self.url:
            logger.error("❌ Config Invalid: CLINIC_MONITOR_URL is missing.")
            # URLがない場合は機能しないため終了してよいが、save_dirエラーでは終了しない
            sys.exit(1)

    def is_operating_hours(self) -> bool:
        """
        現在時刻が監視対象の時間帯かチェックする。

        Returns:
            bool: 実行すべき時間帯であれば True。
        """
        current_hour: int = datetime.now().hour
        start: int = getattr(config, "CLINIC_MONITOR_START_HOUR", 8)
        end: int = getattr(config, "CLINIC_MONITOR_END_HOUR", 19)
        return start <= current_hour <= end

    def save_html(self, content: bytes) -> None:
        """
        取得したHTMLバイナリをファイルに保存する。

        Args:
            content (bytes): レスポンスボディ。
        """
        timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename: str = f"clinic_{timestamp}.html"
        filepath: str = os.path.join(self.save_dir, filename)

        try:
            with open(filepath, "wb") as f:
                f.write(content)
            logger.info(f"✅ Saved HTML: {filename} ({len(content)} bytes)")
        except OSError as e:
            # Rule 8.2: ディスクフル等はERROR扱い 
            logger.error(f"❌ Failed to save HTML to {filepath}: {e}", exc_info=True)

    def run(self) -> None:
        """
        メイン実行処理。
        時間帯チェックを行い、対象であればHTMLを取得して保存する。
        """
        if not self.is_operating_hours():
            logger.info("💤 Out of operating hours. Task skipped.")
            return

        logger.info(f"Fetching clinic status from: {self.url}")

        headers: Dict[str, str] = {
            "User-Agent": self.user_agent
        }

        # Rule 9.5: 明示的なセッション破棄 (with session) 
        try:
            with requests.Session() as session:
                response: requests.Response = session.get(
                    self.url, 
                    headers=headers, 
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    self.save_html(response.content)
                else:
                    # Rule 8.2: 外部APIの一時的エラーはWARNING (Tracebackなし) 
                    logger.warning(f"⚠️ HTTP Error: {response.status_code} - {response.reason}")

        except requests.exceptions.RequestException as e:
            # Rule 8.2: 接続エラーはWARNING (通知なし) 
            logger.warning(f"⚠️ Connection failed: {e}")
        except Exception as e:
            # Rule 8.2: 予期せぬエラーはERROR (Tracebackあり) 
            logger.error(f"💀 Unexpected Error in ClinicMonitor: {e}", exc_info=True)

if __name__ == "__main__":
    monitor = ClinicMonitor()
    monitor.run()