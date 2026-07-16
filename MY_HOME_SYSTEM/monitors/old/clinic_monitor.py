import os
import sys
import requests
import hashlib
from datetime import datetime
from typing import Dict, Optional

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging

# Rule 8.1: 指定ロガーインスタンスの使用
logger = setup_logging("clinic_monitor")

class ClinicMonitor:
    """
    伊丹たかの小児科の予約ページHTMLを定期収集するモニタークラス。
    
    Attributes:
        url (str): 監視対象のURL。
        save_dir (str): HTML保存先ディレクトリ。
        timeout (int): リクエストタイムアウト(秒)。
        user_agent (str): リクエストヘッダーのUser-Agent。
        last_html_hash (Optional[str]): 前回取得したHTMLのハッシュ値。
    """

    def __init__(self) -> None:
        """設定をロードし、初期化を行う。"""
        self.url: str = getattr(config, "CLINIC_MONITOR_URL", "")
        
        base_dir: str = getattr(config, "CLINIC_HTML_DIR", "")
        if not base_dir:
            base_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "clinic_html")
            
        self.save_dir: str = base_dir
        
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"❌ Failed to create save directory '{self.save_dir}'. Saving to monitor directory. Error: {e}")
            self.save_dir = os.path.dirname(__file__)

        self.timeout: int = getattr(config, "CLINIC_REQUEST_TIMEOUT", 10)
        self.user_agent: str = getattr(config, "CLINIC_USER_AGENT", "MyHomeSystem/1.0")
        
        # 状態変化検知用のインメモリキャッシュ
        self.last_html_hash: Optional[str] = None

        if not self.url:
            logger.error("❌ Config Invalid: CLINIC_MONITOR_URL is missing.")
            sys.exit(1)

    def is_operating_hours(self) -> bool:
        """現在時刻が監視対象の時間帯かチェックする。"""
        current_hour: int = datetime.now().hour
        start: int = getattr(config, "CLINIC_MONITOR_START_HOUR", 8)
        end: int = getattr(config, "CLINIC_MONITOR_END_HOUR", 19)
        return start <= current_hour <= end

    def save_html(self, content: bytes) -> None:
        """取得したHTMLバイナリをファイルに保存する。"""
        timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename: str = f"clinic_{timestamp}.html"
        filepath: str = os.path.join(self.save_dir, filename)

        try:
            with open(filepath, "wb") as f:
                f.write(content)
            # 状態変化時のみINFO出力
            logger.info(f"🔄 Clinic status changed! Saved HTML: {filename} ({len(content)} bytes)")
        except OSError as e:
            logger.error(f"❌ Failed to save HTML to {filepath}: {e}", exc_info=True)

    def run(self) -> None:
        """メイン実行処理。"""
        if not self.is_operating_hours():
            # 定常スキップはDEBUG
            logger.debug("💤 Out of operating hours. Task skipped.")
            return

        # 定常ポーリング開始はDEBUG
        logger.debug(f"Fetching clinic status from: {self.url}")

        headers: Dict[str, str] = {
            "User-Agent": self.user_agent
        }

        try:
            with requests.Session() as session:
                response: requests.Response = session.get(
                    self.url, 
                    headers=headers, 
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    content: bytes = response.content
                    current_hash: str = hashlib.md5(content).hexdigest()
                    
                    # 差分検知ロジック
                    if self.last_html_hash != current_hash:
                        self.save_html(content)
                        self.last_html_hash = current_hash
                    else:
                        # 変化なしはDEBUG
                        logger.debug(f"✅ Clinic status unchanged. ({len(content)} bytes)")
                else:
                    logger.warning(f"⚠️ HTTP Error: {response.status_code} - {response.reason}")

        except requests.exceptions.RequestException as e:
            logger.warning(f"⚠️ Connection failed: {e}")
        except Exception as e:
            logger.error(f"💀 Unexpected Error in ClinicMonitor: {e}", exc_info=True)

if __name__ == "__main__":
    monitor = ClinicMonitor()
    monitor.run()