# MY_HOME_SYSTEM/services/timelapse_service.py
"""お家ダッシュボードの「タイムラプス」一覧用に、生成済みの動画を列挙する(読み取り専用)。

動画は `monitors/daily_timelapse_job.py` が `BASE_DIR/assets/timelapse/` に
`<カメラ>_<日付>[_<開始>-<終了>]_summary.mp4`(例: `entrance_2026-10-16_0730-0900_summary.mp4`)
として書き、同名の `.jpg` をサムネイルとして添える。保持期間(`RECORDING_RETENTION_DAYS`)を
過ぎたものは `nas_monitor` が削除する。ここは**何も書かず・消さず**、ファイル名の形式に
一致する通常ファイルだけを新しい順に返す。動画自体は `unified_server` が `/assets` として
配信している静的ファイルをそのまま使う(専用のAPI・変換は無い)。

配信(Discordへの送信)や生成には一切触れない。例外は送出しない(ページを落とさない)。
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date

import config
from core.logger import setup_logging

logger = setup_logging("timelapse_service")

# `/assets` の配信元(`unified_server`の StaticFiles)配下の相対パス。
URL_PREFIX = "/assets/timelapse"
# 生成の途中のファイル(書き込み中)を一覧に出さないための猶予(秒)。
MIN_AGE_SEC = 90

# ファイル名はこの形式に完全一致したものだけを扱う(パス区切り・任意の名前は通さない)。
_FILENAME_RE = re.compile(
    r"^(?P<camera>[A-Za-z0-9-]+)_(?P<day>\d{4}-\d{2}-\d{2})"
    r"(?:_(?P<start>\d{4}|start)-(?P<end>\d{4}|end))?_summary\.mp4$"
)


@dataclass(frozen=True)
class Timelapse:
    filename: str
    camera: str
    day: date
    start: str | None  # "HHMM" / None(時間指定なし・開始指定なし)
    end: str | None
    size_bytes: int
    has_thumbnail: bool


def timelapse_dir() -> str:
    """生成先。`monitors/smart_timelapse_generator.setup_directories` と同じローカルのパス。"""
    return os.path.join(config.BASE_DIR, "assets", "timelapse")


def _clock(value: str | None) -> str | None:
    return value if value and value.isdigit() else None


def list_timelapses(limit: int | None = None, *, now: float | None = None) -> list[Timelapse]:
    """新しい順(日付→開始時刻)のタイムラプス一覧。ディレクトリが無い・読めないときは空リスト。"""
    current = time.time() if now is None else now
    found: list[Timelapse] = []
    try:
        with os.scandir(timelapse_dir()) as entries:
            for entry in entries:
                match = _FILENAME_RE.match(entry.name)
                if match is None or not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
                if stat.st_size <= 0 or current - stat.st_mtime < MIN_AGE_SEC:
                    continue
                try:
                    day = date.fromisoformat(match["day"])
                except ValueError:
                    continue
                thumbnail = os.path.join(timelapse_dir(), entry.name[: -len(".mp4")] + ".jpg")
                found.append(Timelapse(
                    filename=entry.name,
                    camera=match["camera"],
                    day=day,
                    start=_clock(match["start"]),
                    end=_clock(match["end"]),
                    size_bytes=stat.st_size,
                    has_thumbnail=os.path.isfile(thumbnail),
                ))
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning(f"タイムラプスの一覧を取得できませんでした: {e}")
        return []
    found.sort(key=lambda t: (t.day, t.start or "0000", t.camera), reverse=True)
    return found if limit is None else found[:limit]
