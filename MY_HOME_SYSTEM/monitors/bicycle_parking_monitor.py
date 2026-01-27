import requests
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

    def fetch_and_parse(self) -> bool:
        """
        Webサイトからデータを取得し、self.recordsに格納する。
        """
        logger.info(f"Fetching data from: {self.url}")
        try:
            res = requests.get(self.url, timeout=15)
            res.encoding = res.apparent_encoding
            
            if res.status_code != 200:
                logger.error(f"HTTP Error: {res.status_code}")
                return False
                
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # テーブル構造に依存したスクレイピング
            # (伊丹・鈴原エリアを含むテーブルをターゲットとする)
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
                        status_text = text_row[1] # "空きあり", "待ち人数：5人" etc.
                        
                        # ターゲットエリアか判定
                        if any(k in area_name for k in target_keywords):
                            # 待ち人数を抽出 (正規表現)
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

        except Exception as e:
            logger.error(f"Scraping failed: {e}")
            logger.debug(traceback.format_exc())
            return False

    def save_to_db(self) -> None:
        """取得したデータをDBに保存する"""
        if not self.records:
            logger.info("No records to save.")
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

        logger.info(f"💾 {success_count}/{len(self.records)} 件のデータを保存しました。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="駐輪場待機状況モニター")
    parser.add_argument("--save", action="store_true", help="DBに保存する")
    args = parser.parse_args()

    # 自動実行(cron)を想定し、printではなくloggerを使用
    logger.info("🚲 --- Bicycle Parking Monitor ---")
    monitor = BicycleParkingMonitor()
    
    is_success = monitor.fetch_and_parse()
    
    if is_success:
        logger.info(f"✅ 解析完了: {len(monitor.records)} 件のエリア情報を取得")
        
        if monitor.records:
            for r in monitor.records:
                # ログレベル INFO で結果を出力
                logger.info(f"  - {r['area_name']}: {r['status_text']}")
            
            if args.save:
                monitor.save_to_db()
    else:
        sys.exit(1)