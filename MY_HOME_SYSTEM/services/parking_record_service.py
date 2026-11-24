# MY_HOME_SYSTEM/services/parking_record_service.py
"""駐車場の在否判定の結果を DB に保存し、読み出すサービス(#1116)。

判定そのもの(特徴量の計算・しきい値・外出の区間へのまとめ方)は
`services/parking_occupancy_service.py` にある。ここは永続化だけを担う。

* 保存するのは1フレームごとの**特徴量**で、在否(あり/なし/不明)は保存しない。
  読み出すたびに `parking_occupancy_service.classify` で今のしきい値から判定し直す。
  しきい値を見直しても、録画を読み直さずに過去分の集計が変わる。
* 解析した録画は `parking_recordings` に1行ずつ記録し、二度と読まない。
  観測と録画の記録は同じトランザクションで書くので、途中で止まっても
  「観測だけある」「録画だけ処理済み」にはならない。
* 時刻は録画ファイル名と同じホストのローカル時刻("YYYY-MM-DD HH:MM:SS")。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Iterable, List, Optional, Set

from core.database import get_db_cursor
from services import parking_occupancy_service as occupancy

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

STATUS_OK = "ok"
STATUS_UNREADABLE = "unreadable"

# SQL は文字列を組み立てず、列名を書き下した固定の文にする(bandit B608 対策)。
_INSERT_OBSERVATION_SQL = (
    "INSERT INTO parking_observations (observed_at, file, is_ir, saturation, flagstone_edge,"
    " flagstone_lap_var, floor_mean, floor_std, floor_edge, floor_outlier)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_OBSERVATIONS_SQL = (
    "SELECT observed_at, is_ir, saturation, flagstone_edge, flagstone_lap_var,"
    " floor_mean, floor_std, floor_edge, floor_outlier"
    " FROM parking_observations WHERE observed_at >= ? AND observed_at < ? ORDER BY observed_at"
)
# until を省略したときの上限(どの時刻よりも後ろに並ぶ文字列)
_FAR_FUTURE = "9999-12-31 23:59:59"


def _fmt(value: datetime.datetime) -> str:
    return value.strftime(TIMESTAMP_FORMAT)


def known_files(since: datetime.datetime) -> Set[str]:
    """`since` 以降に始まった録画のうち、もう解析した(読めなかったものを含む)ファイル名の集合。"""
    with get_db_cursor() as cur:
        cur.execute("SELECT file FROM parking_recordings WHERE started_at >= ?", (_fmt(since),))
        return {row[0] for row in cur.fetchall()}


def save_recording(
    file_name: str, started_at: datetime.datetime,
    observations: Iterable[occupancy.Observation], now: datetime.datetime,
) -> int:
    """録画1本分の観測と、その録画を解析したことを1つのトランザクションで書く。書いた観測の数を返す。

    同じ録画がもう記録されていれば何もせず 0 を返す(二重に動いた場合の重複を防ぐ)。
    """
    rows = []
    for obs in observations:
        f = obs.features
        assert f is not None, "保存する観測は特徴量を持つ"
        rows.append((
            _fmt(obs.timestamp), file_name, int(f.is_ir), f.saturation, f.flagstone_edge,
            f.flagstone_lap_var, f.floor_mean, f.floor_std, f.floor_edge, f.floor_outlier,
        ))
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT OR IGNORE INTO parking_recordings"
            " (file, started_at, status, observation_count, processed_at) VALUES (?, ?, ?, ?, ?)",
            (file_name, _fmt(started_at), STATUS_OK, len(rows), _fmt(now)),
        )
        if cur.rowcount == 0:
            return 0
        cur.executemany(_INSERT_OBSERVATION_SQL, rows)
    return len(rows)


def mark_unreadable(file_name: str, started_at: datetime.datetime, now: datetime.datetime) -> None:
    """ffmpeg 自身が読めないと答えた録画を記録する。次回からは読まない。"""
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT OR IGNORE INTO parking_recordings"
            " (file, started_at, status, observation_count, processed_at) VALUES (?, ?, ?, 0, ?)",
            (file_name, _fmt(started_at), STATUS_UNREADABLE, _fmt(now)),
        )


def load_observations(
    since: datetime.datetime, until: Optional[datetime.datetime] = None,
) -> List[occupancy.Observation]:
    """`since` 以上 `until` 未満の観測を時刻順に返す。在否は今のしきい値で判定し直す。"""
    with get_db_cursor() as cur:
        cur.execute(_SELECT_OBSERVATIONS_SQL, (_fmt(since), _fmt(until) if until else _FAR_FUTURE))
        rows = cur.fetchall()
    observations = []
    for row in rows:
        features = occupancy.ParkingFeatures(
            is_ir=bool(row[1]), saturation=row[2], flagstone_edge=row[3], flagstone_lap_var=row[4],
            floor_mean=row[5], floor_std=row[6], floor_edge=row[7], floor_outlier=row[8],
        )
        observations.append(occupancy.Observation(
            datetime.datetime.strptime(row[0], TIMESTAMP_FORMAT), occupancy.classify(features), features))
    return observations


@dataclass(frozen=True)
class CarSummary:
    """ダッシュボードの「🚗 駐車場」カード用のまとめ。"""
    state: str                                  # parking_occupancy_service.STATE_*
    since: Optional[datetime.datetime]          # その状態になった時刻(読んだ範囲より前からなら None)
    last_observed_at: Optional[datetime.datetime]
    trips_today: int                            # 今日出発した外出の回数


# カード用に読む範囲。外出中の「何時から」を出すには、出発が範囲に入っている必要がある。
CAR_SUMMARY_WINDOW = datetime.timedelta(days=2)


def get_car_summary(now: datetime.datetime) -> Optional[CarSummary]:
    """直近 `CAR_SUMMARY_WINDOW` の観測から、いまの在否と今日の外出回数を求める。観測が無ければ None。"""
    observations = load_observations(now - CAR_SUMMARY_WINDOW)
    if not observations:
        return None
    current = occupancy.current_state(observations)
    trips = occupancy.build_trips(occupancy.build_events(observations))
    trips_today = sum(1 for trip in trips if trip.departed_at.date() == now.date())
    return CarSummary(current.state, current.since, current.last_observed_at, trips_today)


def get_trips(
    since: datetime.datetime, until: Optional[datetime.datetime] = None,
    confirm_count: int = occupancy.DEFAULT_CONFIRM_COUNT,
) -> List[occupancy.Trip]:
    """期間内の観測から外出の区間を求める。

    期間の始まりで既に外出中だった分は、出た時刻が分からないので含まれない
    (`parking_occupancy_service.build_trips` と同じ)。
    """
    events = occupancy.build_events(load_observations(since, until), confirm_count=confirm_count)
    return occupancy.build_trips(events)
