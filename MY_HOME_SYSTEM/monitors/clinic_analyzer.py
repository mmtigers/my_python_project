import os
import sys
import csv
import re
from typing import List, Dict, Optional, Tuple
from bs4 import BeautifulSoup

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging

# Rule 8.1: 指定ロガーの使用 [cite: 143]
logger = setup_logging("clinic_analyzer")

class ClinicAnalyzer:
    """
    蓄積された小児科のHTMLファイルを解析し、混雑状況をCSVに抽出するクラス。
    
    Attributes:
        html_dir (str): HTMLファイルの読み込み元ディレクトリ。
        output_csv (str): 解析結果の出力先CSVパス。
        headers (List[str]): CSVのヘッダー定義。
    """

    def __init__(self) -> None:
        """設定をロードし、初期化を行う。"""
        self.html_dir: str = getattr(config, "CLINIC_HTML_DIR", os.path.join(config.ASSETS_DIR, "clinic_html"))
        self.output_csv: str = getattr(config, "CLINIC_STATS_CSV", os.path.join(config.ASSETS_DIR, "clinic_stats.csv"))
        
        self.headers: List[str] = [
            "timestamp", 
            "am_reserved", "am_in_clinic", 
            "pm_reserved", "pm_in_clinic"
        ]

    def _count_items(self, text: str) -> int:
        """
        テキスト内の要素数をカウントする。

        Args:
            text (str): 解析対象のテキスト（例: "１、３、５"）。

        Returns:
            int: 要素数。
        """
        if not text:
            return 0
        
        # 除外キーワードチェック
        if "おられません" in text or "受付は終了" in text or "なし" in text:
            return 0

        # 全角読点、半角カンマで分割し、空文字を除去してカウント
        items: List[str] = [x for x in re.split(r'[、,]', text) if x.strip()]
        return len(items)

    def _parse_section(self, section: Optional[BeautifulSoup]) -> Tuple[int, int]:
        """
        午前/午後のセクションブロックを解析する。

        Args:
            section (Optional[BeautifulSoup]): 解析対象のdiv要素。

        Returns:
            Tuple[int, int]: (予約人数, 院内待ち人数)
        """
        if not section:
            return 0, 0
        
        # 1. 予約総数 (waitlistall)
        r_count: int = 0
        wait_span = section.find("span", class_="waitlistall")
        if wait_span:
            r_count = self._count_items(wait_span.get_text())

        # 2. 院内待ち
        c_count: int = 0
        clinic_p = None
        # 「院内でお待ちの方」を含むpタグを安全に探索
        for p in section.find_all("p", class_="nowinfo"):
            if "院内でお待ちの方" in p.get_text():
                clinic_p = p
                break
        
        if clinic_p:
            span = clinic_p.find("span")
            if span:
                c_count = self._count_items(span.get_text())
        
        return r_count, c_count

    def extract_data_from_html(self, file_path: str) -> Optional[Dict[str, any]]:
        """
        1つのHTMLファイルから午前・午後の混雑データを抽出する。

        Args:
            file_path (str): HTMLファイルのパス。

        Returns:
            Optional[Dict[str, any]]: 抽出データ、エラー時はNone。
        """
        try:
            filename: str = os.path.basename(file_path)
            # ファイル名からタイムスタンプ抽出
            match = re.search(r"clinic_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", filename)
            timestamp: str
            if match:
                y, m, d, H, M, S = match.groups()
                timestamp = f"{y}-{m}-{d} {H}:{M}:{S}"
            else:
                timestamp = filename

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                soup = BeautifulSoup(f, "lxml")

            current_div = soup.find("div", id="smpcurrent")
            if not current_div:
                return None

            section_am = current_div.find("div", class_="aroundline10")
            section_pm = current_div.find("div", class_="aroundline7")

            am_r, am_c = self._parse_section(section_am)
            pm_r, pm_c = self._parse_section(section_pm)

            return {
                "timestamp": timestamp,
                "am_reserved": am_r,
                "am_in_clinic": am_c,
                "pm_reserved": pm_r,
                "pm_in_clinic": pm_c
            }

        except Exception as e:
            # Rule 8.2: ファイル単位の解析エラーはWARNING扱いで継続 
            logger.warning(f"⚠️ Failed to parse {file_path}: {e}")
            return None

    def run(self) -> None:
        """
        ディレクトリ内の全ファイルを解析してCSVに出力する。
        """
        if not os.path.exists(self.html_dir):
            logger.error(f"HTML directory not found: {self.html_dir}")
            return

        files: List[str] = sorted([f for f in os.listdir(self.html_dir) if f.endswith(".html")])
        if not files:
            logger.info("No HTML files found to analyze.")
            return

        logger.info(f"Analyzing {len(files)} files...")
        
        results: List[Dict[str, any]] = []
        for file in files:
            file_path = os.path.join(self.html_dir, file)
            data = self.extract_data_from_html(file_path)
            if data:
                results.append(data)

        try:
            with open(self.output_csv, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.headers)
                writer.writeheader()
                writer.writerows(results)
            logger.info(f"✅ Analysis complete. {len(results)} records saved to: {self.output_csv}")
        except OSError as e:
            # Rule 8.2: IOエラーはERROR 
            logger.error(f"❌ Failed to save CSV: {e}", exc_info=True)

if __name__ == "__main__":
    analyzer = ClinicAnalyzer()
    analyzer.run()