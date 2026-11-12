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
