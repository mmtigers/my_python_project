# MY_HOME_SYSTEM/monitors/weather_monitor.py
"""日次の気温を取得して `weather_history` へ保存する監視スクリプト(`scheduler_boot.py` から定期実行)。

電気代と気温を突き合わせる分析(`/dashboard/power`)の気温データの元。毎回、直近
`RECENT_DAYS` 日分を更新するので、数日の取得失敗があっても次回の実行で補われる。

過去分の取り込み(電力データのある期間まで遡る。初回だけ手で実行する):

    python monitors/weather_monitor.py --backfill-days 400

取得に失敗しても終了コードは 0(外部APIの一時的な不調で、スケジューラ側のエラー扱い・
通知を起こさない。失敗は警告ログに残る)。
"""
import argparse
import os
import sys

# プロジェクトルートへのパス解決
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logging
from services import weather_history_service

logger = setup_logging("weather_monitor")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日次の気温を取得して weather_history に保存する")
    parser.add_argument(
        "--backfill-days", type=int, default=0,
        help="前日から指定した日数さかのぼって過去分を取り込む(省略時は直近分の更新のみ)",
    )
    args = parser.parse_args(argv)

    if args.backfill_days > 0:
        saved = weather_history_service.backfill(args.backfill_days)
        logger.info(f"🌤️ 過去{args.backfill_days}日分の気温を取り込みました: {saved}日分を保存")
    saved = weather_history_service.update_recent()
    if saved:
        logger.debug(f"🌤️ 直近の気温を更新しました: {saved}日分")
    else:
        logger.warning("🌤️ 直近の気温を更新できませんでした(取得または保存に失敗。次回の実行で再試行します)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
