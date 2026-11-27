# MY_HOME_SYSTEM/services/recording_status_service.py
"""カメラごとの常時録画(NVR)が続いているかを、NAS上の録画ファイルの新しさから判定する。

録画そのものは `nvr-*.service`(ffmpeg が10分ごとのMP4をNASへ書く)が担い、このモジュールは
読み取るだけ。以前は同じ判定が `monitors/health_watch.py`(毎時cron → Discord通知)にしか無く、
ダッシュボードからは「カメラが生きているか」が分からなかった(見えるのは最後の動体検知だけで、
静かな日とカメラの故障を区別できない)。ファイル名から開始時刻を読む処理をここへ移し、
health_watch とダッシュボード(`home_status_service`)の両方が同じ実装を使う。

NAS が未マウントのときは判定しない(None)。未マウント自体は health_watch の別チェックと
ダッシュボードのNASカードが知らせる。
"""
from __future__ import annotations

import datetime
import glob
import os
from typing import Any
from zoneinfo import ZoneInfo

import config
from core.utils import get_now_jst

JST = ZoneInfo("Asia/Tokyo")

# 最新の録画セグメントの開始がこれより古ければ「止まっている」とみなす(秒)。
# セグメントは10分ごとなので、正常なら最新の開始時刻は常に10分以内。余裕を見て30分。
RECORDING_STALE_SEC: int = 30 * 60


def recording_times(folder: str, now: datetime.datetime) -> list[datetime.datetime]:
    """録画フォルダの当日・前日のセグメント開始時刻(ファイル名から、JST)を新しい順に返す。

    CIFS 越しに保持期間(30日)分を毎回列挙しないよう、当日と前日(日付の変わり目用)に絞る。
    ファイル名は ffmpeg の -strftime によるローカル時刻(実機のTZはAsia/Tokyo)。
    """
    names: list[str] = []
    for day in (now, now - datetime.timedelta(days=1)):
        pattern = os.path.join(folder, f"{day.strftime('%Y%m%d')}_*.mp4")
        names.extend(os.path.basename(p) for p in glob.glob(pattern))
    times: list[datetime.datetime] = []
    for name in sorted(names, reverse=True):
        try:
            times.append(datetime.datetime.strptime(name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=JST))
        except ValueError:
            continue
    return times


def camera_recording_states(now: datetime.datetime | None = None) -> list[dict[str, Any]] | None:
    """有効なカメラごとの録画の状態を返す。NAS未マウント・有効なカメラ無しのときは None(判定できない)。

    各要素は `{"id", "name", "state", "latest"}`。`state` は
    `ok`(最新の録画が `RECORDING_STALE_SEC` 以内)/`stalled`(それより古い)/
    `missing`(今日・昨日の録画ファイルが無い)。`latest` は最新セグメントの開始時刻(無ければ None)。
    """
    if not os.path.ismount(config.NAS_MOUNT_POINT):
        return None
    now = now or get_now_jst()
    states: list[dict[str, Any]] = []
    for cam in config.CAMERAS:
        if not cam.get("enabled", True):
            continue
        folder_name = cam.get("nas_folder") or cam["name"]
        times = recording_times(os.path.join(config.NVR_RECORD_DIR, folder_name), now)
        latest = times[0] if times else None
        if latest is None:
            state = "missing"
        elif (now - latest).total_seconds() > RECORDING_STALE_SEC:
            state = "stalled"
        else:
            state = "ok"
        states.append({"id": cam["id"], "name": cam["name"], "state": state, "latest": latest})
    return states or None


# === 録画の欠け(Issue #1169) ===
# 録画の ffmpeg は SEGMENT_SEC(600秒)ごとに新しいファイルを作る。隣り合うセグメントの開始時刻の
# 間隔が SEGMENT_SEC + GAP_TOLERANCE_SEC を超えたら、その差(間隔 − SEGMENT_SEC)を欠けとみなす。
# ffmpeg の再起動(RestartSec=10s)で生じる短いセグメントは間隔が縮むだけなので欠けにならない。
# 直前のセグメントが途中で終わっていた場合は実際の欠けより短く出る(ファイルの長さは読まないため、
# 値は下限の目安)。30分以上の停止や細切れは health_watch のチェック10が別途通知する。
SEGMENT_SEC: int = 600
GAP_TOLERANCE_SEC: int = 60


def recording_times_between(
    folder: str, start: datetime.datetime, end: datetime.datetime
) -> list[datetime.datetime]:
    """録画フォルダの、start〜end のセグメント開始時刻を古い順に返す。

    start より前の最後の1本も含める(期間の先頭の欠けを数えるため)。日付ごとに glob するので、
    読むのは期間の日数ぶん(+前日)のファイル名だけ。
    """
    names: list[str] = []
    day = (start - datetime.timedelta(days=1)).date()
    while day <= end.date():
        pattern = os.path.join(folder, f"{day.strftime('%Y%m%d')}_*.mp4")
        names.extend(os.path.basename(p) for p in glob.glob(pattern))
        day += datetime.timedelta(days=1)
    times: list[datetime.datetime] = []
    for name in sorted(set(names)):
        try:
            times.append(datetime.datetime.strptime(name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=JST))
        except ValueError:
            continue
    before = [t for t in times if t < start]
    inside = [t for t in times if start <= t <= end]
    return before[-1:] + inside


def find_recording_gaps(times: list[datetime.datetime]) -> list[tuple[datetime.datetime, int]]:
    """古い順のセグメント開始時刻から、欠け(欠けが始まったと見られる時刻, 欠けの秒数)を返す。"""
    gaps: list[tuple[datetime.datetime, int]] = []
    for prev, cur in zip(times, times[1:]):
        interval = int((cur - prev).total_seconds())
        if interval > SEGMENT_SEC + GAP_TOLERANCE_SEC:
            gaps.append((prev + datetime.timedelta(seconds=SEGMENT_SEC), interval - SEGMENT_SEC))
    return gaps


def camera_recording_gaps(
    start: datetime.datetime, end: datetime.datetime
) -> list[dict[str, Any]] | None:
    """有効なカメラごとの、start〜end の録画の欠けの件数と合計秒数。NAS未マウントなら None。

    各要素は `{"name", "count", "total_sec"}`。週次レポートが「後から欠けの有無を確かめる記録」
    として使う(数分の欠けは都度通知するとノイズになるので通知しない。Issue #1169)。
    """
    if not os.path.ismount(config.NAS_MOUNT_POINT):
        return None
    results: list[dict[str, Any]] = []
    for cam in config.CAMERAS:
        if not cam.get("enabled", True):
            continue
        folder_name = cam.get("nas_folder") or cam["name"]
        gaps = find_recording_gaps(
            recording_times_between(os.path.join(config.NVR_RECORD_DIR, folder_name), start, end)
        )
        results.append({"name": cam["name"], "count": len(gaps), "total_sec": sum(s for _, s in gaps)})
    return results or None
