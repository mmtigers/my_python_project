import requests
from requests.adapters import HTTPAdapter  # <--- 追加
from urllib3.util.retry import Retry       # <--- 追加
from bs4 import BeautifulSoup
import sys
import os
import argparse
import re
import traceback
from typing import List, TypedDict

# プロジェクトルートへのパス設定
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
# 【修正】common廃止 -> coreモジュールへ移行
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso

# 【修正】統一ロガーを使用
logger = setup_logging("bicycle_monitor")

# データ構造の定義 (Type Hinting)
class ParkingRecord(TypedDict):
    area_name: str
    status_text: str
    waiting_count: int

class BicycleParkingMonitor:
    """
    駐輪場の定期利用待機状況をスクレイピングし、DBに記録するクラス。
    """
    
    def __init__(self) -> None:
        # Configに定義がなければデフォルトURLを使用
        self.url: str = getattr(config, "BICYCLE_PARKING_URL", "https://www.midi-kintetsu.com/mpns/pa/h-itami/teiki/index.php")
        self.table_name: str = getattr(config, "SQLITE_TABLE_BICYCLE", "bicycle_parking_logs")
        self.records: List[ParkingRecord] = []
    
    def _get_session(self) -> requests.Session:
        """
        リトライ戦略を設定したrequestsセッションを作成して返す。
        - 接続エラーや一時的なサーバーエラー(5xx)に対して、自動的にリトライを行う。
        - Backoff Factor=1 により、リトライ間隔を空けてサーバー負荷を考慮する。
        """
        session = requests.Session()
        retries = Retry(
            total=3,                # 最大リトライ回数
            backoff_factor=1,       # リトライ間隔 (1秒, 2秒, 4秒...)
            status_forcelist=[500, 502, 503, 504],  # リトライ対象のHTTPステータス
            allowed_methods=["GET"] # GETリクエストのみ対象
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def fetch_and_parse(self) -> bool:
        """
        Webサイトからデータを取得し、self.recordsに格納する。
        """
        logger.debug(f"Fetching data from: {self.url}")
        
        # 【修正】セッションを使用してリトライを行う & 明示的なClose (Context Manager)
        try:
            with self._get_session() as session:
                res = session.get(self.url, timeout=15)
                res.encoding = res.apparent_encoding
                
                if res.status_code != 200:
                    # HTTPステータスエラーはサーバー側の問題の可能性があるためWARNINGとする
                    logger.warning(f"HTTP Error: {res.status_code}")
                    return False
                    
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # テーブル構造に依存したスクレイピング
                tables = soup.find_all('table')
                if not tables:
                    logger.warning("No tables found on the page.")
                    return False

                self.records = []
                
                # 特定のエリアキーワード
                target_keywords = ["鈴原", "伊丹", "阪急"]
                
                for table in tables:
                    rows = table.find_all('tr')
                    for row in rows:
                        cols = row.find_all(['td', 'th'])
                        text_row = [c.get_text(strip=True) for c in cols]
                        
                        if len(text_row) >= 2:
                            area_name = text_row[0]
                            status_text = text_row[1]
                            
                            if any(k in area_name for k in target_keywords):
                                count = 0
                                match = re.search(r'(\d+)人', status_text)
                                if match:
                                    count = int(match.group(1))
                                elif "空" in status_text or "○" in status_text:
                                    count = 0
                                
                                self.records.append({
                                    "area_name": area_name,
                                    "status_text": status_text,
                                    "waiting_count": count
                                })
                return True

        except requests.exceptions.RequestException as e:
            # 【修正】リトライ後も失敗したネットワークエラーは、重要度が低いためWARNINGに留める
            # これにより DiscordErrorHandler (ERROR以上で通知) を回避する
            logger.warning(f"Network Connection Failed (Retries exhausted): {e}")
            return False

        except Exception as e:
            # 【修正】予期せぬパースエラーやロジックエラーは引き続きERRORで通知する
            logger.error(f"Unexpected Scraping failed: {e}")
            logger.debug(traceback.format_exc())
            return False

    def save_to_db(self) -> None:
        """取得したデータをDBに保存する"""
        if not self.records:
            logger.debug("No records to save.")
            return

        success_count = 0
        cols = ["timestamp", "area_name", "status_text", "waiting_count"]
        
        for r in self.records:
            try:
                vals = (
                    get_now_iso(),
                    r['area_name'],
                    r['status_text'],
                    r['waiting_count']
                )
                # 【修正】core.database.save_log_generic を使用
                if save_log_generic(self.table_name, cols, vals):
                    success_count += 1
            except Exception as e:
                logger.error(f"DB保存エラー ({r['area_name']}): {e}")

        logger.debug(f"💾 {success_count}/{len(self.records)} 件のデータを保存しました。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="駐輪場待機状況モニター")
    parser.add_argument("--save", action="store_true", help="DBに保存する")
    args = parser.parse_args()

    # 自動実行(cron)を想定し、printではなくloggerを使用
    logger.debug("🚲 --- Bicycle Parking Monitor ---")
    monitor = BicycleParkingMonitor()
    
    is_success = monitor.fetch_and_parse()
    
    if is_success:
        logger.debug(f"✅ 解析完了: {len(monitor.records)} 件のエリア情報を取得")
        
        if monitor.records:
            for r in monitor.records:
                # ログレベル DEBUG で結果を出力
                logger.debug(f"  - {r['area_name']}: {r['status_text']}")
            
            if args.save:
                monitor.save_to_db()
    else:
        # 【修正】エラーハンドリング済みの失敗なら異常終了コードを出さない運用に変更するか、
        # Scheduler側でWARNINGを検知できないため、exit(1)は残すが
        # Schedulerがログを吐く際、標準エラー出力がWARNINGなら通知しない制御は難しいため
        # ここでは「既知の失敗」として正常終了(0)させるか、exit(1)させるかの判断。
        # 今回は「通知ノイズ削減」が主目的なので、スクリプト内でWARNINGログを出した上で
        # sys.exit(0) することでSchedulerの "Task failed" 通知も抑制します。
        
        logger.warning("⚠️ Task finished incompletely due to network/parsing issues.")
        sys.exit(0) # Schedulerへの通知を抑制