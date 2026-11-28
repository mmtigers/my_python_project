# MY_HOME_SYSTEM/tests/test_parking_record_service.py
"""services/parking_record_service.py と monitors/parking_occupancy_monitor.py のテスト(#1116)。

ffmpeg は起動しない。`analyze_recording` を、ファイル名ごとに決めた判定を返す偽物に差し替える。
"""
import csv
import datetime
import os
import sys

import numpy as np
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_cursor
from monitors import parking_occupancy_monitor as monitor
from services import parking_occupancy_service as occupancy
from services import parking_record_service as records
from tools import parking_occupancy_backfill as backfill

P, A, U = occupancy.STATE_PRESENT, occupancy.STATE_ABSENT, occupancy.STATE_UNKNOWN
FEATURES = {
    P: occupancy.ParkingFeatures(False, 30.0, 0.05, 800.0, 40.0, 20.0, 0.04, 0.03),
    A: occupancy.ParkingFeatures(False, 30.0, 0.22, 8000.0, 160.0, 15.0, 0.02, 0.01),
    U: occupancy.ParkingFeatures(True, 5.0, 0.16, 4000.0, 92.0, 15.0, 0.02, 0.01),
}
NOW = datetime.datetime(2026, 11, 24, 12, 0, 0)


def _observations(start, states):
    return [
        occupancy.Observation(start + datetime.timedelta(minutes=i), state, FEATURES[state])
        for i, state in enumerate(states)
    ]


def _count(table):
    with get_db_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608 - テスト内の固定テーブル名
        return cur.fetchone()[0]


class TestRecords:
    def test_保存した特徴量から今のしきい値で判定し直して読み出す(self, isolated_db, monkeypatch):
        start = datetime.datetime(2026, 11, 24, 8, 0)
        written = records.save_recording("20261124_080000.mp4", start, _observations(start, [P, A, U]), NOW)
        assert written == 3

        loaded = records.load_observations(start)
        assert [o.state for o in loaded] == [P, A, U]
        assert [o.timestamp for o in loaded] == [start + datetime.timedelta(minutes=i) for i in range(3)]
        assert loaded[2].features == FEATURES[U]  # 特徴量はそのまま戻る(is_ir の bool も)

        # しきい値を変えると、保存し直さなくても判定が変わる
        monkeypatch.setattr(occupancy, "FLAGSTONE_EDGE_PRESENT_MAX", 0.01)
        monkeypatch.setattr(occupancy, "FLOOR_MEAN_PRESENT_MAX", 10.0)
        assert [o.state for o in records.load_observations(start)] == [U, A, U]

    def test_同じ録画は二度保存しない(self, isolated_db):
        start = datetime.datetime(2026, 11, 24, 8, 0)
        records.save_recording("20261124_080000.mp4", start, _observations(start, [P]), NOW)
        assert records.save_recording("20261124_080000.mp4", start, _observations(start, [P]), NOW) == 0
        assert _count("parking_observations") == 1

    def test_期間で絞り込み時刻順に返す(self, isolated_db):
        day1 = datetime.datetime(2026, 11, 23, 8, 0)
        day2 = datetime.datetime(2026, 11, 24, 8, 0)
        records.save_recording("20261124_080000.mp4", day2, _observations(day2, [A]), NOW)
        records.save_recording("20261123_080000.mp4", day1, _observations(day1, [P]), NOW)

        assert [o.timestamp for o in records.load_observations(day1)] == [day1, day2]
        assert [o.timestamp for o in records.load_observations(day1, day2)] == [day1]
        assert records.known_files(day2) == {"20261124_080000.mp4"}
        assert records.known_files(day1) == {"20261123_080000.mp4", "20261124_080000.mp4"}

    def test_読めない録画も解析済みとして数える(self, isolated_db):
        start = datetime.datetime(2026, 11, 24, 8, 0)
        records.mark_unreadable("20261124_080000.mp4", start, NOW)
        records.mark_unreadable("20261124_080000.mp4", start, NOW)  # 二度目は無視される
        assert records.known_files(start) == {"20261124_080000.mp4"}
        assert _count("parking_observations") == 0
        with get_db_cursor() as cur:
            cur.execute("SELECT status, observation_count FROM parking_recordings")
            assert [tuple(r) for r in cur.fetchall()] == [(records.STATUS_UNREADABLE, 0)]

    def test_外出の区間を求める(self, isolated_db):
        start = datetime.datetime(2026, 11, 24, 8, 0)
        records.save_recording("20261124_080000.mp4", start, _observations(start, [P] * 3 + [A] * 5 + [P] * 3), NOW)
        trips = records.get_trips(start)
        assert [(t.departed_at, t.returned_at) for t in trips] == [
            (start + datetime.timedelta(minutes=3), start + datetime.timedelta(minutes=8))]


def _touch(path):
    path.write_bytes(b"")
    return str(path)


@pytest.fixture
def rec_dir(tmp_path, monkeypatch):
    """録画ディレクトリ。いちばん新しい録画は書き込み中として除かれるので、最後に1本足しておく。"""
    rec = tmp_path / "rec"
    rec.mkdir()
    _touch(rec / "20261124_235000.mp4")
    monkeypatch.setattr(monitor, "_now", lambda: NOW)
    return rec


@pytest.fixture
def fake_analyze(monkeypatch):
    states = {}
    calls = []

    def analyze(path, start, interval_seconds):
        name = os.path.basename(path)
        calls.append(name)
        for index, state in enumerate(states.get(name, [])):
            if isinstance(state, BaseException):
                raise state
            yield (occupancy.Observation(start + datetime.timedelta(seconds=index * interval_seconds),
                                         state, FEATURES[state]), np.zeros((4, 4, 3), np.uint8))

    monkeypatch.setattr(occupancy, "analyze_recording", analyze)
    return states, calls


class TestMonitor:
    def test_まだ解析していない録画を古い順に保存し次回は飛ばす(self, isolated_db, rec_dir, fake_analyze):
        states, calls = fake_analyze
        _touch(rec_dir / "20261124_081000.mp4")
        _touch(rec_dir / "20261124_080000.mp4")
        states["20261124_080000.mp4"] = [P, P]
        states["20261124_081000.mp4"] = [A]

        assert monitor.main(["--record-dir", str(rec_dir)]) == 0
        assert calls == ["20261124_080000.mp4", "20261124_081000.mp4"]
        assert _count("parking_observations") == 3

        monitor.main(["--record-dir", str(rec_dir)])
        assert calls == ["20261124_080000.mp4", "20261124_081000.mp4"]  # 2回目は読まない

    def test_1回に読む本数を抑え残りは次回に回す(self, isolated_db, rec_dir, fake_analyze, monkeypatch):
        states, calls = fake_analyze
        monkeypatch.setattr(monitor, "MAX_FILES_PER_RUN", 2)
        for minute in range(3):
            name = f"20261124_08{minute}000.mp4"
            _touch(rec_dir / name)
            states[name] = [P]

        monitor.main(["--record-dir", str(rec_dir)])
        assert len(calls) == 2
        monitor.main(["--record-dir", str(rec_dir)])
        assert len(calls) == 3

        calls.clear()
        _touch(rec_dir / "20261124_090000.mp4")
        monitor.main(["--record-dir", str(rec_dir), "--no-limit"])
        assert calls == ["20261124_090000.mp4"]

    def test_読めない録画は記録し時間切れは次回もう一度読む(self, isolated_db, rec_dir, fake_analyze):
        states, calls = fake_analyze
        # Issue #1184 で時間切れの後ろの録画はその回に読まなくなったため、フレームが取れなかった
        # 録画(この試験のもう1つの対象)は時間切れより前に置く
        _touch(rec_dir / "20261124_075000.mp4")
        _touch(rec_dir / "20261124_080000.mp4")
        _touch(rec_dir / "20261124_081000.mp4")
        states["20261124_080000.mp4"] = [occupancy.FrameExtractionError("映像が無い", returncode=234)]
        states["20261124_081000.mp4"] = [P, occupancy.FrameExtractionError("時間切れ", returncode=-9)]
        states["20261124_075000.mp4"] = []  # フレームが1枚も取れなかった(ffmpeg は成功)

        monitor.main(["--record-dir", str(rec_dir)])
        monitor.main(["--record-dir", str(rec_dir)])
        assert calls.count("20261124_080000.mp4") == 1
        assert calls.count("20261124_081000.mp4") == 2
        # フレームが取れなかった録画は観測0件として記録し、読み直さない(上限を食いつぶさないため)
        assert calls.count("20261124_075000.mp4") == 1
        assert _count("parking_observations") == 0  # 途中まで読めた録画の観測も残さない

    def test_時間切れが出たらその回の残りは読まずに次回へ回す(self, isolated_db, rec_dir, fake_analyze):
        """Issue #1184: NAS が固まっているなら残りも読めないので、1回の実行を長引かせない。"""
        states, calls = fake_analyze
        for minute in range(4):
            name = f"20261124_08{minute}000.mp4"
            _touch(rec_dir / name)
            states[name] = [P]
        states["20261124_081000.mp4"] = [occupancy.FrameExtractionError("時間切れ", returncode=-9)]

        counts = monitor.process_pending(str(rec_dir), monitor.LOOKBACK_DAYS, monitor.MAX_FILES_PER_RUN)
        assert calls == ["20261124_080000.mp4", "20261124_081000.mp4"]
        assert counts["saved"] == 1 and counts["retry"] == 3  # 時間切れ1本 + 読まなかった2本

        # NAS が戻れば、次の回で時間切れだった録画から読み直す
        states["20261124_081000.mp4"] = [P]
        calls.clear()
        monitor.process_pending(str(rec_dir), monitor.LOOKBACK_DAYS, monitor.MAX_FILES_PER_RUN)
        assert calls == ["20261124_081000.mp4", "20261124_082000.mp4", "20261124_083000.mp4"]

    def test_遡る日数より古い録画は読まない(self, isolated_db, rec_dir, fake_analyze):
        _, calls = fake_analyze
        _touch(rec_dir / "20261122_080000.mp4")
        _touch(rec_dir / "20261123_080000.mp4")
        monitor.main(["--record-dir", str(rec_dir)])
        assert calls == ["20261123_080000.mp4"]

    def test_駐車場カメラが無い_録画が見えないなら何もせず正常終了する(self, isolated_db, monkeypatch, tmp_path, fake_analyze):
        _, calls = fake_analyze
        monkeypatch.setattr(backfill, "resolve_record_dir", lambda: None)
        assert monitor.main([]) == 0
        assert monitor.main(["--record-dir", str(tmp_path / "missing")]) == 0
        assert calls == []

    def test_遡る日数は1以上(self):
        with pytest.raises(SystemExit):
            monitor.main(["--days", "0"])

    def test_CLI_の出力を取り込み既にある録画は飛ばす(self, isolated_db, tmp_path, monkeypatch):
        monkeypatch.setattr(monitor, "_now", lambda: NOW)
        out = tmp_path / "out"
        out.mkdir()
        with open(out / backfill.OBSERVATIONS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=backfill.OBSERVATION_FIELDS)
            writer.writeheader()
            start = datetime.datetime(2026, 11, 24, 8, 0)
            for obs in _observations(start, [P, P]):
                writer.writerow(backfill._observation_row(obs, "20261124_080000.mp4"))
            for obs in _observations(start + datetime.timedelta(minutes=10), [A]):
                writer.writerow(backfill._observation_row(obs, "20261124_081000.mp4"))
        (out / backfill.UNREADABLE_FILE).write_text("20261124_082000.mp4\nbroken.mp4\n", encoding="utf-8")
        # 一部はもう DB にある
        later = start + datetime.timedelta(minutes=10)
        records.save_recording("20261124_081000.mp4", later, _observations(later, [A]), NOW)

        assert monitor.main(["--import-csv", str(out)]) == 0
        assert _count("parking_observations") == 3
        assert records.known_files(start) == {
            "20261124_080000.mp4", "20261124_081000.mp4", "20261124_082000.mp4"}
        assert [o.state for o in records.load_observations(start)] == [P, P, A]

    def test_CSV_が無くても取り込みは失敗しない(self, isolated_db, tmp_path):
        assert monitor.import_csv(str(tmp_path)) == {"saved": 0, "observations": 0, "unreadable": 0}
