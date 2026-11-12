# MY_HOME_SYSTEM/tests/test_parking_occupancy_backfill.py
"""tools/parking_occupancy_backfill.py のテスト。

ffmpeg は起動しない。`analyze_recording` を、ファイル名ごとに決めた判定を返す偽物に
差し替えて、録画の選び方・CSV への追記と再開・集計の出力を確かめる。
"""
import csv
import datetime
import fcntl
import os
import sys
import time

import numpy as np
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from services import parking_occupancy_service as occupancy
from tools import parking_occupancy_backfill as backfill

TODAY = datetime.date(2026, 11, 12)
P, A, U = occupancy.STATE_PRESENT, occupancy.STATE_ABSENT, occupancy.STATE_UNKNOWN
FEATURES = occupancy.ParkingFeatures(False, 30.0, 0.2, 8000.0, 160.0, 15.0, 0.02, 0.01)


def _touch(path, age_seconds=3600):
    path.write_bytes(b"")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return str(path)


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def fake_analyze(monkeypatch):
    """ファイル名 → 判定の並び。呼ばれたファイル名を `calls` に残す。"""
    states = {}
    calls = []

    def analyze(path, start, interval_seconds):
        name = os.path.basename(path)
        calls.append(name)
        for index, state in enumerate(states.get(name, [])):
            if isinstance(state, BaseException):
                raise state
            timestamp = start + datetime.timedelta(seconds=index * interval_seconds)
            yield occupancy.Observation(timestamp, state, FEATURES), np.zeros((72, 128, 3), np.uint8)

    monkeypatch.setattr(occupancy, "analyze_recording", analyze)
    monkeypatch.setattr(backfill, "_today", lambda: TODAY)
    return states, calls


class TestFindRecordings:
    def test_期間内の録画を古い順に返し書き込み中と名前の読めないものは除く(self, tmp_path):
        old = _touch(tmp_path / "20261110_230000.mp4")
        yesterday = _touch(tmp_path / "20261111_120000.mp4")
        today = _touch(tmp_path / "20261112_000000.mp4")
        _touch(tmp_path / "20261112_001000.mp4", age_seconds=5)  # 書き込み中
        _touch(tmp_path / "20261112_broken.mp4")

        found = backfill.find_recordings(str(tmp_path), 2, TODAY, time.time())
        assert found == [yesterday, today]
        assert old not in found

    def test_ファイル名から開始時刻を読む(self):
        assert backfill.recording_start("/rec/20261112_093000.mp4") == datetime.datetime(2026, 11, 12, 9, 30)
        assert backfill.recording_start("/rec/readme.mp4") is None


class TestRun:
    def test_解析して_CSV_を書き出し外出を集計する(self, tmp_path, fake_analyze, capsys):
        states, _ = fake_analyze
        rec, out = tmp_path / "rec", tmp_path / "out"
        rec.mkdir()
        _touch(rec / "20261112_080000.mp4")
        _touch(rec / "20261112_081000.mp4")
        states["20261112_080000.mp4"] = [P, P, P, A, A, A, A, A, A, A]
        states["20261112_081000.mp4"] = [A, A, U, P, P, P, P, P, P, P]

        assert backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False) == 0

        rows = _read_csv(out / backfill.OBSERVATIONS_FILE)
        assert len(rows) == 20
        assert rows[0]["timestamp"] == "2026-11-12 08:00:00" and rows[0]["file"] == "20261112_080000.mp4"
        assert rows[0]["flagstone_edge"] == "0.2000"
        assert _read_csv(out / backfill.TRIPS_FILE) == [{
            "departed_at": "2026-11-12 08:03:00", "returned_at": "2026-11-12 08:13:00",
            "duration_minutes": "10", "uncertain": "0"}]
        assert _read_csv(out / backfill.DAILY_SUMMARY_FILE) == [
            {"date": "2026-11-12", "trips": "1", "away_minutes": "10"}]
        printed = capsys.readouterr().out
        assert "外出: 1回" in printed and "11/12 08:03 → 11/12 08:13  10分" in printed
        assert "判定できず 1件" in printed
        assert not (out / backfill.FRAMES_DIR).exists()

    def test_もう一度実行すると処理済みの録画は飛ばして続きから進む(self, tmp_path, fake_analyze):
        states, calls = fake_analyze
        rec, out = tmp_path / "rec", tmp_path / "out"
        rec.mkdir()
        _touch(rec / "20261112_080000.mp4")
        states["20261112_080000.mp4"] = [P, P, P]
        backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False)

        _touch(rec / "20261112_081000.mp4")
        states["20261112_081000.mp4"] = [A, A, A]
        backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False)

        assert calls == ["20261112_080000.mp4", "20261112_081000.mp4"]
        rows = _read_csv(out / backfill.OBSERVATIONS_FILE)
        assert [row["state"] for row in rows] == [P, P, P, A, A, A]
        # 外出中のまま終わる外出も trips.csv に載る
        assert _read_csv(out / backfill.TRIPS_FILE)[0]["returned_at"] == ""

    def test_フレームが取れなかった録画は記録せず次回もう一度試す(self, tmp_path, fake_analyze):
        _, calls = fake_analyze
        rec, out = tmp_path / "rec", tmp_path / "out"
        rec.mkdir()
        _touch(rec / "20261112_080000.mp4")

        backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False)
        backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False)

        assert calls == ["20261112_080000.mp4"] * 2

    def test_最後まで読めなかった録画は1行も書かず次回もう一度試す(self, tmp_path, fake_analyze, capsys):
        states, calls = fake_analyze
        rec, out = tmp_path / "rec", tmp_path / "out"
        rec.mkdir()
        _touch(rec / "20261112_080000.mp4")
        _touch(rec / "20261112_081000.mp4")
        states["20261112_080000.mp4"] = [P, P, occupancy.FrameExtractionError("NAS が応答しない")]
        states["20261112_081000.mp4"] = [A, A, A]

        assert backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False) == 0
        rows = _read_csv(out / backfill.OBSERVATIONS_FILE)
        assert {row["file"] for row in rows} == {"20261112_081000.mp4"}
        assert "次回に回します" in capsys.readouterr().err

        states["20261112_080000.mp4"] = [P, P, P]
        backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False)
        assert calls == ["20261112_080000.mp4", "20261112_081000.mp4", "20261112_080000.mp4"]
        assert len(_read_csv(out / backfill.OBSERVATIONS_FILE)) == 6

    def test_途中で中断した録画の行は残さない(self, tmp_path, fake_analyze):
        states, _ = fake_analyze
        rec, out = tmp_path / "rec", tmp_path / "out"
        rec.mkdir()
        _touch(rec / "20261112_080000.mp4")
        _touch(rec / "20261112_081000.mp4")
        states["20261112_080000.mp4"] = [P, P, P]
        states["20261112_081000.mp4"] = [A, KeyboardInterrupt()]

        with pytest.raises(KeyboardInterrupt):
            backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False)

        rows = _read_csv(out / backfill.OBSERVATIONS_FILE)
        assert {row["file"] for row in rows} == {"20261112_080000.mp4"}

    def test_判定が変わった時と不明の時だけ画像を残し上限を守る(self, tmp_path, fake_analyze):
        states, _ = fake_analyze
        rec, out = tmp_path / "rec", tmp_path / "out"
        rec.mkdir()
        _touch(rec / "20261112_080000.mp4")
        states["20261112_080000.mp4"] = [P, P, A, A, U, P]

        backfill.run(str(rec), str(out), 1, 60, 3, save_frames=True)
        assert sorted(os.listdir(out / backfill.FRAMES_DIR)) == [
            "20261112_080200_absent.jpg", "20261112_080400_unknown.jpg", "20261112_080500_present.jpg"]

        capped = tmp_path / "capped"
        backfill.run(str(rec), str(capped), 1, 60, 3, save_frames=True, max_saved_frames=1)
        assert os.listdir(capped / backfill.FRAMES_DIR) == ["20261112_080200_absent.jpg"]

    def test_録画ディレクトリが無ければ何も作らず失敗する(self, tmp_path, capsys):
        out = tmp_path / "out"
        assert backfill.run(str(tmp_path / "missing"), str(out), 1, 60, 3, save_frames=False) == 1
        assert not out.exists()
        assert "録画ディレクトリが見つかりません" in capsys.readouterr().err

    def test_別の解析が実行中なら何もしない(self, tmp_path, fake_analyze, capsys):
        _, calls = fake_analyze
        rec, out = tmp_path / "rec", tmp_path / "out"
        rec.mkdir()
        out.mkdir()
        _touch(rec / "20261112_080000.mp4")

        with open(out / backfill.LOCK_FILE, "w") as other:
            fcntl.flock(other, fcntl.LOCK_EX)
            assert backfill.run(str(rec), str(out), 1, 60, 3, save_frames=False) == 1

        assert calls == []
        assert "別の解析が実行中" in capsys.readouterr().err


class TestMain:
    def test_駐車場カメラが登録されていなければ録画先の指定を求める(self, monkeypatch, capsys):
        monkeypatch.setattr(config, "CAMERAS", [])
        assert backfill.main([]) == 1
        assert "--record-dir" in capsys.readouterr().err

    def test_録画先は_devices_json_のカメラ設定から決め出力先は_NAS_にする(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "CAMERAS", [
            {"id": backfill.home_status_service.PARKING_CAMERA_ID, "name": "駐車場カメラ",
             "nas_folder": "parking"}])
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path / "nvr"))
        monkeypatch.setattr(config, "NAS_PROJECT_ROOT", str(tmp_path / "nas"))
        called = {}
        monkeypatch.setattr(backfill, "run", lambda *args: called.setdefault("args", args) and 0)

        assert backfill.main(["--days", "3", "--interval", "30", "--confirm", "2", "--save-frames"]) == 0
        assert called["args"] == (
            str(tmp_path / "nvr" / "parking"), str(tmp_path / "nas" / "parking_occupancy"), 3, 30, 2, True)

    def test_引数で録画先と出力先を上書きできる(self, monkeypatch):
        called = {}
        monkeypatch.setattr(backfill, "run", lambda *args: called.setdefault("args", args) and 0)
        backfill.main(["--record-dir", "/rec", "--out", "/out"])
        assert called["args"][:2] == ("/rec", "/out")

    def test_1未満の値は受け付けない(self):
        with pytest.raises(SystemExit):
            backfill.main(["--days", "0"])
