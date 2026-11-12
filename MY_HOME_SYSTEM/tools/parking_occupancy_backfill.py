#!/usr/bin/env python3
# MY_HOME_SYSTEM/tools/parking_occupancy_backfill.py
"""駐車場カメラの NVR 録画を遡って読み、自家用車の在否と外出の区間を CSV に書き出す CLI。

Phase 0(実現性検討)用。判定のしきい値は暫定値なので、ここで集めた特徴量(observations.csv)を
見て `services/parking_occupancy_service.py` の値を決め直す。DB には何も書かない。

使い方(実機の MY_HOME_SYSTEM/ で):

    .venv/bin/python tools/parking_occupancy_backfill.py                 # 直近7日分
    .venv/bin/python tools/parking_occupancy_backfill.py --days 30       # 直近30日分
    .venv/bin/python tools/parking_occupancy_backfill.py --save-frames   # 判定が変わった時の画像も残す

出力先(既定は NAS の `home_system/parking_occupancy/`。SD カードには書かない):

    observations.csv   1フレームごとの時刻・判定・特徴量(追記していく)
    trips.csv          外出1回ごとの出庫・入庫の時刻と時間(毎回作り直す)
    daily_summary.csv  日ごとの外出回数と合計時間(毎回作り直す)
    frames/            --save-frames を付けた時だけ。判定が変わった・不明だったフレームの JPEG

処理済みの録画は observations.csv の `file` 列で分かるので、もう一度実行すると続きから進む。
録画1本(10分)ごとにファイル全体を NAS から読むため、30日分は数時間かかる。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import fcntl
import glob
import os
import sys
import time
from typing import Dict, Iterable, List, Optional, Set

import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from services import camera_service, home_status_service, record_playlist_service
from services import parking_occupancy_service as occupancy

DEFAULT_DAYS = 7
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_MAX_SAVED_FRAMES = 500
# 書き込み中の録画(更新時刻が新しいもの)は次回に回す。途中までしか読めないのに
# 「処理済み」と記録すると、残りの時間帯が二度と解析されない。
IN_PROGRESS_SECONDS = 120
SAVED_FRAME_SIZE = (640, 360)

OBSERVATIONS_FILE = "observations.csv"
TRIPS_FILE = "trips.csv"
DAILY_SUMMARY_FILE = "daily_summary.csv"
FRAMES_DIR = "frames"
LOCK_FILE = ".backfill.lock"

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
OBSERVATION_FIELDS = [
    "timestamp", "file", "state", "is_ir", "saturation", "flagstone_edge", "flagstone_lap_var",
    "floor_mean", "floor_std", "floor_edge", "floor_outlier",
]


def default_out_dir() -> str:
    return os.path.join(config.NAS_PROJECT_ROOT, "parking_occupancy")


def _today() -> datetime.date:
    return datetime.date.today()


def resolve_record_dir() -> Optional[str]:
    """駐車場カメラの録画ディレクトリ。devices.json にカメラが無ければ None。"""
    cam_conf = camera_service.get_camera_config_or_none(home_status_service.PARKING_CAMERA_ID)
    if cam_conf is None:
        return None
    return record_playlist_service.record_dir(cam_conf)


def recording_start(path: str) -> Optional[datetime.datetime]:
    """録画ファイル名(`%Y%m%d_%H%M%S.mp4`)から開始時刻を読む。読めなければ None。"""
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        return datetime.datetime.strptime(name, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def find_recordings(record_dir: str, days: int, today: datetime.date, now: float) -> List[str]:
    """直近 `days` 日分(今日を含む)の録画を古い順に返す。書き込み中のものは除く。"""
    paths: List[str] = []
    for offset in range(days - 1, -1, -1):
        day = today - datetime.timedelta(days=offset)
        paths.extend(sorted(glob.glob(os.path.join(record_dir, f"{day:%Y%m%d}_*.mp4"))))
    return [
        path for path in paths
        if recording_start(path) is not None and now - os.path.getmtime(path) >= IN_PROGRESS_SECONDS
    ]


def load_processed_files(observations_path: str) -> Set[str]:
    """observations.csv に既に載っている録画ファイル名の集合。"""
    if not os.path.exists(observations_path):
        return set()
    with open(observations_path, newline="", encoding="utf-8") as f:
        return {row["file"] for row in csv.DictReader(f)}


def load_observations(observations_path: str) -> List[occupancy.Observation]:
    """observations.csv の全行を、時刻と判定だけの観測として読む。"""
    if not os.path.exists(observations_path):
        return []
    with open(observations_path, newline="", encoding="utf-8") as f:
        return [
            occupancy.Observation(
                datetime.datetime.strptime(row["timestamp"], TIMESTAMP_FORMAT), row["state"])
            for row in csv.DictReader(f)
        ]


def _observation_row(obs: occupancy.Observation, file_name: str) -> Dict[str, object]:
    features = obs.features
    assert features is not None, "analyze_recording は特徴量つきの観測を返す"
    return {
        "timestamp": obs.timestamp.strftime(TIMESTAMP_FORMAT),
        "file": file_name,
        "state": obs.state,
        "is_ir": int(features.is_ir),
        "saturation": f"{features.saturation:.1f}",
        "flagstone_edge": f"{features.flagstone_edge:.4f}",
        "flagstone_lap_var": f"{features.flagstone_lap_var:.0f}",
        "floor_mean": f"{features.floor_mean:.1f}",
        "floor_std": f"{features.floor_std:.1f}",
        "floor_edge": f"{features.floor_edge:.4f}",
        "floor_outlier": f"{features.floor_outlier:.4f}",
    }


class FrameSaver:
    """判定が直前と変わったフレームと「不明」のフレームを、目で確かめる用に JPEG で残す。"""

    def __init__(self, frames_dir: str, max_frames: int) -> None:
        self.frames_dir = frames_dir
        self.remaining = max_frames
        self.previous_state: Optional[str] = None

    def offer(self, obs: occupancy.Observation, frame) -> None:
        changed = self.previous_state is not None and obs.state != self.previous_state
        self.previous_state = obs.state
        if self.remaining <= 0 or not (changed or obs.state == occupancy.STATE_UNKNOWN):
            return
        os.makedirs(self.frames_dir, exist_ok=True)
        name = f"{obs.timestamp:%Y%m%d_%H%M%S}_{obs.state}.jpg"
        cv2.imwrite(os.path.join(self.frames_dir, name), cv2.resize(frame, SAVED_FRAME_SIZE))
        self.remaining -= 1


def process_recordings(
    paths: Iterable[str], observations_path: str, interval_seconds: int,
    frame_saver: Optional[FrameSaver] = None,
) -> int:
    """録画を順に解析して observations.csv へ追記する。書いた行数を返す。

    1本ごとに flush するので、途中で止めてもそこまでの結果は残り、次回は続きから進む。
    フレームが1枚も取れなかった録画は記録しない(次回もう一度試す)。
    """
    is_new = not os.path.exists(observations_path)
    written = 0
    with open(observations_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OBSERVATION_FIELDS)
        if is_new:
            writer.writeheader()
        for path in paths:
            start = recording_start(path)
            if start is None:
                continue
            file_name = os.path.basename(path)
            for obs, frame in occupancy.analyze_recording(path, start, interval_seconds):
                writer.writerow(_observation_row(obs, file_name))
                written += 1
                if frame_saver is not None:
                    frame_saver.offer(obs, frame)
            f.flush()
    return written


def _format_time(value: Optional[datetime.datetime]) -> str:
    return "" if value is None else value.strftime(TIMESTAMP_FORMAT)


def write_trips(path: str, trips: Iterable[occupancy.Trip]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["departed_at", "returned_at", "duration_minutes", "uncertain"])
        for trip in trips:
            minutes = trip.duration_minutes
            writer.writerow([
                _format_time(trip.departed_at), _format_time(trip.returned_at),
                "" if minutes is None else f"{minutes:.0f}", int(trip.uncertain),
            ])


def write_daily_summary(path: str, summaries: Iterable[occupancy.DailySummary]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "trips", "away_minutes"])
        for summary in summaries:
            writer.writerow([summary.date.isoformat(), summary.trips, f"{summary.away_minutes:.0f}"])


def print_report(observations: List[occupancy.Observation], trips: List[occupancy.Trip],
                 summaries: List[occupancy.DailySummary]) -> None:
    total = len(observations)
    unknown = sum(1 for obs in observations if obs.state == occupancy.STATE_UNKNOWN)
    print(f"観測: {total:,}件 (うち判定できず {unknown:,}件"
          f"{f' = {unknown / total:.1%}' if total else ''})")
    print(f"外出: {len(trips)}回 (うち時刻が不確か {sum(1 for t in trips if t.uncertain)}回)")
    print()
    print("=== 日ごとの外出 ===")
    for summary in summaries:
        print(f"  {summary.date}  {summary.trips}回  計{summary.away_minutes:.0f}分")
    print()
    print("=== 外出の一覧 ===")
    for trip in trips:
        minutes = trip.duration_minutes
        end = "(外出中)" if trip.returned_at is None else f"{trip.returned_at:%m/%d %H:%M}"
        length = "" if minutes is None else f"  {minutes:.0f}分"
        mark = "  ※時刻が不確か" if trip.uncertain else ""
        print(f"  {trip.departed_at:%m/%d %H:%M} → {end}{length}{mark}")


def run(record_dir: str, out_dir: str, days: int, interval_seconds: int, confirm_count: int,
        save_frames: bool, max_saved_frames: int = DEFAULT_MAX_SAVED_FRAMES) -> int:
    """録画を解析して CSV を更新し、結果を表示する。終了コードを返す。"""
    if not os.path.isdir(record_dir):
        print(f"録画ディレクトリが見つかりません: {record_dir}", file=sys.stderr)
        return 1
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, LOCK_FILE), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("別の解析が実行中です。終わってからもう一度実行してください。", file=sys.stderr)
            return 1

        observations_path = os.path.join(out_dir, OBSERVATIONS_FILE)
        processed = load_processed_files(observations_path)
        recordings = find_recordings(record_dir, days, _today(), time.time())
        pending = [path for path in recordings if os.path.basename(path) not in processed]
        print(f"録画 {len(recordings)}本のうち、未処理の {len(pending)}本を解析します"
              f" ({interval_seconds}秒ごとに1フレーム)")

        saver = FrameSaver(os.path.join(out_dir, FRAMES_DIR), max_saved_frames) if save_frames else None
        written = process_recordings(pending, observations_path, interval_seconds, saver)
        print(f"{written:,}件の観測を追記しました: {observations_path}")
        print()

        observations = load_observations(observations_path)
        events = occupancy.build_events(observations, confirm_count=confirm_count)
        trips = occupancy.build_trips(events)
        summaries = occupancy.summarize_daily(trips)
        write_trips(os.path.join(out_dir, TRIPS_FILE), trips)
        write_daily_summary(os.path.join(out_dir, DAILY_SUMMARY_FILE), summaries)
        print_report(observations, trips, summaries)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="駐車場カメラの録画から自家用車の在否と外出を集計する")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="今日を含めて何日分遡るか")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help="何秒ごとに1フレーム見るか")
    parser.add_argument("--confirm", type=int, default=occupancy.DEFAULT_CONFIRM_COUNT,
                        help="同じ判定が何回続いたら状態の変化を確定するか")
    parser.add_argument("--out", default=None, help="出力先ディレクトリ(既定は NAS の parking_occupancy)")
    parser.add_argument("--record-dir", default=None,
                        help="録画ディレクトリ(既定は devices.json の駐車場カメラの録画先)")
    parser.add_argument("--save-frames", action="store_true",
                        help="判定が変わった・不明だったフレームを JPEG で残す")
    args = parser.parse_args(argv)
    if args.days < 1 or args.interval < 1 or args.confirm < 1:
        parser.error("--days / --interval / --confirm は 1 以上にしてください")

    record_dir = args.record_dir or resolve_record_dir()
    if record_dir is None:
        print(f"devices.json に駐車場カメラ({home_status_service.PARKING_CAMERA_ID})がありません。"
              "--record-dir で録画ディレクトリを指定してください。", file=sys.stderr)
        return 1
    return run(record_dir, args.out or default_out_dir(), args.days, args.interval, args.confirm,
               args.save_frames)


if __name__ == "__main__":
    sys.exit(main())
