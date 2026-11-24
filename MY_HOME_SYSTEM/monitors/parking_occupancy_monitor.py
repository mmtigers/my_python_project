# MY_HOME_SYSTEM/monitors/parking_occupancy_monitor.py
"""駐車場カメラの録画を解析して、自家用車の在否の特徴量を DB に保存する(#1116)。

`scheduler_boot.py` から10分ごとに実行する。書き終わった駐車場カメラの録画のうち、
まだ解析していないものを古い順に読み、1分ごとのフレームの特徴量を
`parking_observations` に保存する(`services/parking_record_service.py`)。
録画は10分ごとに1本増えるので、ふだんは1回の実行で1本(Pi で約7秒)を読む。

止まっていた間の録画は、直近 `LOOKBACK_DAYS` 日分まで遡って取り戻す。1回に読む本数は
`MAX_FILES_PER_RUN` までに抑え、残りは次の実行に回す(長く止まっていた後でも、
1回の実行が長引いて他の監視と CPU を取り合わないようにする)。

過去分の取り込み(初回だけ手で実行する):

    # Phase 0〜1 の CLI(tools/parking_occupancy_backfill.py)で集めた CSV を取り込む
    .venv/bin/python monitors/parking_occupancy_monitor.py --import-csv /mnt/nas/home_system/parking_occupancy
    # 録画から直接、指定した日数ぶん遡って解析する(上限なしで全部読む)
    .venv/bin/python monitors/parking_occupancy_monitor.py --days 7 --no-limit

駐車場カメラが devices.json に無い・録画ディレクトリが見えない場合は、何もせず終了コード 0
(スケジューラのエラー通知を起こさない。NAS の停止は nas_monitor / health_watch が検知する)。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logging
from services import parking_occupancy_service as occupancy
from services import parking_record_service as records
from tools import parking_occupancy_backfill as backfill

logger = setup_logging("parking_occupancy_monitor")

LOOKBACK_DAYS = 2
MAX_FILES_PER_RUN = 12            # 2時間分。Pi で1本約7秒なので1回あたり最大2分弱
INTERVAL_SECONDS = 60             # 1分ごとに1フレーム(Phase 0〜1 で精度を確かめた間隔)


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def process_pending(record_dir: str, days: int, limit: Optional[int]) -> Dict[str, int]:
    """まだ解析していない録画を古い順に解析して DB に保存する。件数の内訳を返す。"""
    now = _now()
    since = datetime.datetime.combine(now.date() - datetime.timedelta(days=days - 1), datetime.time())
    known = records.known_files(since)
    pending = [
        path for path in backfill.find_recordings(record_dir, days, now.date())
        if os.path.basename(path) not in known
    ]
    if limit is not None:
        pending = pending[:limit]

    counts = {"saved": 0, "observations": 0, "unreadable": 0, "retry": 0}
    for path in pending:
        start = backfill.recording_start(path)
        if start is None:  # find_recordings が名前を読めたものだけ返すので通らない
            continue
        file_name = os.path.basename(path)
        try:
            observations = [obs for obs, _frame in occupancy.analyze_recording(path, start, INTERVAL_SECONDS)]
        except occupancy.FrameExtractionError as e:
            if e.unreadable:
                records.mark_unreadable(file_name, start, _now())
                counts["unreadable"] += 1
                logger.info(f"🚗 読めない録画を記録しました(次回からは読みません): {e}")
            else:
                counts["retry"] += 1
                logger.warning(f"🚗 録画を最後まで読めなかったので次回に回します: {e}")
            continue
        if not observations:
            # フレームが1枚も取れなかった(ffmpeg は成功)。記録せず次回もう一度試す。
            counts["retry"] += 1
            continue
        counts["observations"] += records.save_recording(file_name, start, observations, _now())
        counts["saved"] += 1
    return counts


def import_csv(out_dir: str) -> Dict[str, int]:
    """Phase 0〜1 の CLI の出力(observations.csv・unreadable.txt)を DB に取り込む。

    録画ごとにまとめて `save_recording` に渡すので、もう DB にある録画は飛ばされる。
    """
    now = _now()
    by_file: Dict[str, List[occupancy.Observation]] = {}
    observations_path = os.path.join(out_dir, backfill.OBSERVATIONS_FILE)
    if os.path.exists(observations_path):
        with open(observations_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                features = backfill._features_from_row(row)
                timestamp = datetime.datetime.strptime(row["timestamp"], backfill.TIMESTAMP_FORMAT)
                by_file.setdefault(row["file"], []).append(
                    occupancy.Observation(timestamp, occupancy.classify(features), features))

    counts = {"saved": 0, "observations": 0, "unreadable": 0}
    for file_name, observations in sorted(by_file.items()):
        start = backfill.recording_start(file_name)
        if start is None:
            continue
        written = records.save_recording(file_name, start, observations, now)
        if written:
            counts["saved"] += 1
            counts["observations"] += written
    for file_name in sorted(backfill.load_unreadable_files(os.path.join(out_dir, backfill.UNREADABLE_FILE))):
        start = backfill.recording_start(file_name)
        if start is not None:
            records.mark_unreadable(file_name, start, now)
            counts["unreadable"] += 1
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="駐車場カメラの録画を解析して在否の特徴量を DB に保存する")
    parser.add_argument("--days", type=int, default=LOOKBACK_DAYS, help="今日を含めて何日分遡るか")
    parser.add_argument("--no-limit", action="store_true", help="1回に読む本数の上限をなくす(手で遡るとき用)")
    parser.add_argument("--import-csv", metavar="DIR", default=None,
                        help="tools/parking_occupancy_backfill.py の出力ディレクトリから取り込む")
    parser.add_argument("--record-dir", default=None, help="録画ディレクトリ(既定は devices.json の駐車場カメラ)")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days は 1 以上にしてください")

    if args.import_csv:
        counts = import_csv(args.import_csv)
        logger.info(f"🚗 CSV を取り込みました: 録画 {counts['saved']}本・観測 {counts['observations']}件"
                    f"・読めない録画 {counts['unreadable']}本")
        return 0

    record_dir = args.record_dir or backfill.resolve_record_dir()
    if record_dir is None:
        logger.debug("🚗 devices.json に駐車場カメラが無いので何もしません")
        return 0
    if not os.path.isdir(record_dir):
        logger.warning(f"🚗 録画ディレクトリが見えないので何もしません: {record_dir}")
        return 0

    counts = process_pending(record_dir, args.days, None if args.no_limit else MAX_FILES_PER_RUN)
    if any(counts.values()):
        logger.info(f"🚗 駐車場の録画を解析しました: 録画 {counts['saved']}本・観測 {counts['observations']}件"
                    f"・読めない録画 {counts['unreadable']}本・次回に回した録画 {counts['retry']}本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
