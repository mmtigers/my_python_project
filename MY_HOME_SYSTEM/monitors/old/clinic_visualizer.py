import os
import sys
import pandas as pd
import matplotlib.pyplot as plt
import japanize_matplotlib  # 日本語豆腐文字化け対策
from datetime import datetime, timedelta
from typing import Optional

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging

logger = setup_logging("clinic_visualizer")

class ClinicVisualizer:
    """
    小児科の混雑データを可視化し、グラフ画像を生成するクラス。
    """

    def __init__(self) -> None:
        self.csv_path: str = getattr(config, "CLINIC_STATS_CSV", "")
        self.output_image: str = getattr(config, "CLINIC_GRAPH_PATH", "")

    def generate_graph(self, days: int = 7) -> Optional[str]:
        """
        過去N日分のデータをグラフ化し、画像ファイルとして保存する。

        Args:
            days (int): 表示する期間（日数）。

        Returns:
            Optional[str]: 保存した画像のパス。失敗時はNone。
        """
        if not os.path.exists(self.csv_path):
            logger.warning(f"CSV file not found: {self.csv_path}")
            return None

        try:
            # CSV読み込み
            df = pd.read_csv(self.csv_path)
            
            # タイムスタンプをdatetime型に変換
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            # 直近N日分にフィルタリング
            start_date = datetime.now() - timedelta(days=days)
            df = df[df['timestamp'] >= start_date]

            if df.empty:
                logger.warning("No data found in the specified range.")
                return None

            # グラフ描画設定
            plt.figure(figsize=(12, 6))
            
            # プロット: 予約人数（AM/PM合計）と院内待ち人数（AM/PM合計）を計算
            # ※簡易的にAMとPMを足し合わせる（同時間帯に両方立つことは稀なため）
            df['total_reserved'] = df['am_reserved'] + df['pm_reserved']
            df['total_in_clinic'] = df['am_in_clinic'] + df['pm_in_clinic']

            plt.plot(df['timestamp'], df['total_reserved'], label='予約総数', color='tab:blue', alpha=0.7)
            plt.plot(df['timestamp'], df['total_in_clinic'], label='院内待ち', color='tab:orange', linestyle='--', linewidth=2)

            # タイトル・軸ラベル
            plt.title(f"小児科混雑トレンド (過去{days}日間)", fontsize=16)
            plt.xlabel("日時", fontsize=12)
            plt.ylabel("人数", fontsize=12)
            plt.grid(True, which='both', linestyle='--', alpha=0.5)
            plt.legend()
            
            # X軸の日付フォーマット調整
            plt.gcf().autofmt_xdate()

            # 保存
            plt.savefig(self.output_image, bbox_inches='tight')
            plt.close() # メモリ解放

            logger.info(f"✅ Graph saved: {self.output_image}")
            return self.output_image

        except Exception as e:
            logger.error(f"❌ Failed to generate graph: {e}", exc_info=True)
            return None

if __name__ == "__main__":
    viz = ClinicVisualizer()
    viz.generate_graph(days=7)