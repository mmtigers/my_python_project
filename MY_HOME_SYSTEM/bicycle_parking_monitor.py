import requests
from bs4 import BeautifulSoup, Tag
import re
import sys
import os
import argparse
import logging
import traceback
from datetime import datetime
from typing import List, Dict, Optional, Any, TypedDict

# プロジェクトルートへのパス設定
sys.path.append(os.getcwd())

try:
    import config
    import common
except ImportError:
    # 開発環境で万が一モジュールが見つからない場合の安全策
    # 本番環境(RasPi)および整備済みローカル環境では実行されません
    sys.stderr.write("Error: 'config.py' or 'common.py' not found.\n")
    sys.exit(1)

# ロガーのセットアップ
logger = common.setup_logging("bicycle_monitor")

# データ構造の定義 (Type Hinting)
class ParkingRecord(TypedDict):
    area_name: str
    status_text: str
    waiting_count: int

class BicycleParkingMonitor:
    """
    駐輪場の定期利用待機状況をスクレイピングし、DBに記録するクラス。
    
    Attributes:
        url (str): 監視対象のURL
        records (List[ParkingRecord]): 取得したデータのリスト
    """
    
    def __init__(self) -> None:
        # configから設定を読み込む (定数のハードコード排除)
        self.url: str = getattr(config, "BICYCLE_PARKING_URL", "https://www.midi-kintetsu.com/mpns/pa/h-itami/teiki/index.php")
        self.table_name: str = getattr(config, "SQLITE_TABLE_BICYCLE", "bicycle_parking_records")
        self.records: List[ParkingRecord] = []

    def fetch_and_parse(self) -> bool:
        """
        Webページを取得して解析を実行する。
        
        Returns:
            bool: 取得と解析が成功した場合はTrue
        """
        logger.info(f"🌍 アクセス中: {self.url}")
        try:
            headers: Dict[str, str] = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            res = requests.get(self.url, headers=headers, timeout=15)
            res.raise_for_status()
            
            # 文字コードをUTF-8に強制指定（文字化け防止）
            res.encoding = "utf-8"

            soup = BeautifulSoup(res.text, "html.parser")
            self._extract_data_robust(soup)
            return True

        except Exception as e:
            logger.error(f"❌ 取得エラー: {e}")
            logger.debug(traceback.format_exc())
            return False

    def _extract_data_robust(self, soup: BeautifulSoup) -> None:
        """
        BeautifulSoupオブジェクトから駐輪場データを抽出する。
        
        Args:
            soup (BeautifulSoup): 解析対象のHTMLスープ
        """
        table = soup.find("table", class_="itami")
        if not isinstance(table, Tag):
            logger.warning("⚠️ class='itami' のテーブルが見つかりません。")
            return

        rows = table.find_all("tr")
        current_parking_name: str = "不明な駐輪場"

        logger.info(f"🔍 {len(rows)} 行のデータを解析します...")

        for row in rows:
            current_parking_name = self._update_parking_name(row, current_parking_name)
            self._process_data_row(row, current_parking_name)

    def _update_parking_name(self, row: Tag, current_name: str) -> str:
        """
        行内に駐輪場名（ヘッダー情報）が含まれているか確認し、あれば更新して返す。
        
        Args:
            row (Tag): trタグ
            current_name (str): 現在の駐輪場名
            
        Returns:
            str: 更新された（あるいはそのままの）駐輪場名
        """
        th = row.find("th")
        if th:
            text = th.get_text(strip=True)
            # 「駐輪場」または「駐車場」が含まれる場合、新しいエリア名とみなす
            if ("駐輪場" in text or "駐車場" in text) and text != "自転車駐車場":
                logger.info(f"   💡 名前検出: {text}")
                return text
        return current_name

    def _process_data_row(self, row: Tag, parking_name: str) -> None:
        """
        データ行（tdが4つある行）であればパースしてリストに追加する。
        
        Args:
            row (Tag): trタグ
            parking_name (str): 現在の駐輪場名
        """
        tds = row.find_all("td")
        if len(tds) != 4:
            return

        # データの抽出
        area_code: str = tds[0].get_text(strip=True)   # A, B...
        status_text: str = tds[2].get_text(strip=True) # 6台, 0台...
        
        waiting_count = self._parse_waiting_count(status_text)
        
        # ログ用整形
        full_area_name = f"{parking_name} ({area_code})"
        
        record: ParkingRecord = {
            "area_name": full_area_name,
            "status_text": status_text,
            "waiting_count": waiting_count
        }
        self.records.append(record)

    def _parse_waiting_count(self, text: str) -> int:
        """
        テキストから待機数を抽出する。
        
        Args:
            text (str): "6台", "空き" などの文字列
            
        Returns:
            int: 抽出された数値。不明時は0とする（仕様準拠）
        """
        if "台" in text or "人" in text:
            match = re.search(r'(\d+)', text)
            if match:
                return int(match.group(1))
        
        # "0", "空き", "無" などの場合は0とみなす
        return 0

    def save_to_db(self) -> None:
        """
        抽出したデータをDBに保存する。
        """
        if not self.records:
            logger.warning("📭 保存するデータがありません。")
            return

        success_count = 0
        timestamp = common.get_now_iso()
        
        cols = ["area_name", "status_text", "waiting_count", "timestamp"]

        for r in self.records:
            try:
                vals = (r["area_name"], r["status_text"], r["waiting_count"], timestamp)
                
                # commonモジュールの汎用保存関数を使用
                if common.save_log_generic(self.table_name, cols, vals):
                    success_count += 1
            except Exception as e:
                logger.error(f"DB保存エラー ({r['area_name']}): {e}")

        logger.info(f"💾 {success_count}/{len(self.records)} 件のデータを保存しました。")

if __name__ == "__main__":
    # 引数パース
    parser = argparse.ArgumentParser(description="駐輪場待機状況モニター (Refactored)")
    parser.add_argument("--save", action="store_true", help="DBに保存する")
    args = parser.parse_args()

    print("🚲 --- Bicycle Parking Monitor (Refactored) ---")
    monitor = BicycleParkingMonitor()
    
    if monitor.fetch_and_parse():
        print(f"\n✅ 解析完了: {len(monitor.records)} 件のエリア情報を取得")
        
        if monitor.records:
            print("-" * 70)
            print(f"{'エリア名':<40} | {'待機数'}")
            print("-" * 70)
            for r in monitor.records:
                # 待機数が1以上なら目立たせる
                prefix = "🔴" if r['waiting_count'] > 0 else "  "
                print(f"{prefix} {r['area_name']:<38} | {r['status_text']}")
            print("-" * 70)

        if args.save:
            monitor.save_to_db()
        else:
            print("ℹ️ 保存は行っていません (`--save` で保存)")
    else:
        print("❌ データの取得に失敗しました。")
        sys.exit(1)