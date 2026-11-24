# MY_HOME_SYSTEM/tests/test_parking_occupancy_service.py
"""services/parking_occupancy_service.py のテスト。

実際のカメラ画像はリポジトリに入れない(自宅の映像のため)。ここでは「石畳の目地」と
「一様な床」「車体の明暗」を合成したフレームで、判定と状態の確定を確かめる。
"""
import datetime
import os
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import parking_occupancy_service as occupancy

W, H = occupancy.ANALYZE_WIDTH, occupancy.ANALYZE_HEIGHT
T0 = datetime.datetime(2026, 11, 12, 8, 0, 0)


def _region(roi):
    return slice(int(roi.y0 * H), int(roi.y1 * H)), slice(int(roi.x0 * W), int(roi.x1 * W))


def _frame(car: bool, night: bool, flagstone_hidden=None, floor_blocked=None, floor_shadow=False) -> np.ndarray:
    """合成フレーム。`flagstone_hidden`/`floor_blocked` で2つの領域を別々に切り替えられる。

    `floor_shadow` は、日没後の玄関灯で柱の影が床に斜めに落ちた状態(車はない)。
    """
    flagstone_hidden = car if flagstone_hidden is None else flagstone_hidden
    floor_blocked = car if floor_blocked is None else floor_blocked
    base = 110 if night else 160
    frame = np.full((H, W, 3), base, dtype=np.uint8)
    if not night:
        frame[..., 0] = base - 60  # 青を落として色を付ける(昼=カラー)

    ys, xs = _region(occupancy.FLAGSTONE_ROI)
    if not flagstone_hidden:
        # 石畳の目地: 細かい格子模様
        patch = frame[ys, xs]
        patch[::6, :] = 20
        patch[:, ::6] = 20
    ys, xs = _region(occupancy.FLOOR_ROI)
    patch = frame[ys, xs]
    if floor_blocked:
        # 車体: 床の領域がほぼ一様な暗いリアガラスで埋まり、上端に少し反射が入る
        patch[:] = 30
        patch[:6, :] = 200
    elif floor_shadow:
        # 柱の影: 床の領域の3割ほどが暗い斜めの帯になる
        for i in range(patch.shape[0]):
            start = i * patch.shape[1] // patch.shape[0]
            patch[i, start:start + patch.shape[1] // 3] = 40
    return frame


def _obs(minute: float, state: str) -> occupancy.Observation:
    return occupancy.Observation(T0 + datetime.timedelta(minutes=minute), state)


P, A, U = occupancy.STATE_PRESENT, occupancy.STATE_ABSENT, occupancy.STATE_UNKNOWN


class TestClassify:
    @pytest.mark.parametrize("car,night,expected", [
        (False, False, A), (False, True, A), (True, False, P), (True, True, P),
    ])
    def test_昼と夜のどちらでも在否を判定できる(self, car, night, expected):
        features = occupancy.compute_features(_frame(car, night))
        assert occupancy.classify(features) == expected
        assert features.is_ir is night

    def test_床が一様な暗いガラスで埋まっても車ありと判定する(self):
        # #1115: 車を左・奥に寄せて停めると、床の領域がリアガラスだけで埋まる。
        # 以前の「外れ画素の割合」ではこれを何もない床と区別できなかった。
        for night in (False, True):
            features = occupancy.compute_features(_frame(True, night))
            assert features.floor_outlier < 0.06  # 以前の判定なら「車なし」になる値
            assert occupancy.classify(features) == P

    def test_床に柱の影が落ちても車なしと判定する(self):
        # #1115: 日没後に玄関灯で柱の影が床に斜めに落ちると、以前は「車あり」に誤判定した
        # (実機ではこの時間帯はまだ IR に切り替わっておらず、カラーのまま)。
        features = occupancy.compute_features(_frame(False, False, floor_shadow=True))
        assert features.floor_outlier > 0.2  # 以前の判定なら「車あり」になる値
        assert occupancy.classify(features) == A

    def test_石畳と床が食い違えば石畳を優先する(self):
        # #1115: 車を奥に停めて床が見えている(実機では夜通し続いた)。石畳は隠れているので「あり」
        features = occupancy.compute_features(_frame(True, True, floor_blocked=False))
        assert features.floor_mean >= occupancy.FLOOR_MEAN_ABSENT_MIN
        assert occupancy.classify(features) == P
        # 車はないが、晴れた昼に影や人で床が暗くなった。石畳は見えているので「なし」
        features = occupancy.compute_features(_frame(False, False, floor_blocked=True))
        assert features.floor_mean <= occupancy.FLOOR_MEAN_PRESENT_MAX
        assert occupancy.classify(features) == A

    def test_石畳がどちらとも言えない時は床で決める(self):
        base = occupancy.compute_features(_frame(True, False))
        middle = (occupancy.FLAGSTONE_EDGE_PRESENT_MAX + occupancy.FLAGSTONE_EDGE_ABSENT_MIN) / 2

        def with_(floor_mean):
            return occupancy.ParkingFeatures(
                **{**base.__dict__, "flagstone_edge": middle, "floor_mean": floor_mean})

        # 日没後で石畳が暗くなった(実機 0.166)が、床は明るい → なし
        assert occupancy.classify(with_(137.0)) == A
        assert occupancy.classify(with_(40.0)) == P
        # 床もどちらとも言えなければ不明
        assert occupancy.classify(with_(92.0)) == U

    def test_大きさの違うフレームは解析用の大きさに揃えてから計算する(self):
        import cv2
        big = cv2.resize(_frame(True, False), (W * 2, H * 2), interpolation=cv2.INTER_NEAREST)
        assert occupancy.classify(occupancy.compute_features(big)) == P


class TestBuildEvents:
    def test_連続回数に達した時点で確定し時刻は連続の最初になる(self):
        observations = [_obs(i, P) for i in range(3)] + [_obs(3 + i, A) for i in range(3)]
        events = occupancy.build_events(observations, confirm_count=3)
        assert events == [occupancy.ParkingEvent(occupancy.EVENT_DEPART, T0 + datetime.timedelta(minutes=3))]

    def test_連続回数に満たない揺れは無視する(self):
        observations = [_obs(0, P), _obs(1, P), _obs(2, P), _obs(3, A), _obs(4, A), _obs(5, P), _obs(6, P)]
        assert occupancy.build_events(observations, confirm_count=3) == []

    def test_不明は読み飛ばし連続を途切れさせない(self):
        observations = [_obs(0, P), _obs(1, P), _obs(2, P), _obs(3, A), _obs(4, U), _obs(5, A), _obs(6, A)]
        events = occupancy.build_events(observations, confirm_count=3)
        assert [(e.kind, e.at) for e in events] == [
            (occupancy.EVENT_DEPART, T0 + datetime.timedelta(minutes=3))]

    def test_最初の状態はイベントにしない(self):
        assert occupancy.build_events([_obs(i, A) for i in range(5)], confirm_count=3) == []

    def test_観測の空白をまたいだ変化は不確かとする(self):
        observations = [_obs(i, P) for i in range(3)] + [_obs(120 + i, A) for i in range(3)]
        events = occupancy.build_events(observations, confirm_count=3)
        assert events == [
            occupancy.ParkingEvent(occupancy.EVENT_DEPART, T0 + datetime.timedelta(minutes=120), True)]

    def test_時刻順でない入力でも並べ替えて扱う(self):
        observations = [_obs(3 + i, A) for i in range(3)] + [_obs(i, P) for i in range(3)]
        events = occupancy.build_events(observations, confirm_count=3)
        assert [e.kind for e in events] == [occupancy.EVENT_DEPART]

    def test_連続回数は1以上でなければならない(self):
        with pytest.raises(ValueError):
            occupancy.build_events([], confirm_count=0)


class TestTripsAndSummary:
    def _events(self, *pairs):
        return [occupancy.ParkingEvent(kind, T0 + datetime.timedelta(minutes=m)) for kind, m in pairs]

    def test_出庫と次の入庫を組にする(self):
        trips = occupancy.build_trips(self._events(
            (occupancy.EVENT_DEPART, 0), (occupancy.EVENT_ARRIVE, 45),
            (occupancy.EVENT_DEPART, 100), (occupancy.EVENT_ARRIVE, 130)))
        assert [t.duration_minutes for t in trips] == [45, 30]

    def test_出庫より前の入庫は捨て最後の出庫は外出中として残す(self):
        trips = occupancy.build_trips(self._events(
            (occupancy.EVENT_ARRIVE, 0), (occupancy.EVENT_DEPART, 10)))
        assert len(trips) == 1
        assert trips[0].returned_at is None and trips[0].duration_minutes is None

    def test_出庫か入庫のどちらかが不確かなら外出も不確かとする(self):
        events = [
            occupancy.ParkingEvent(occupancy.EVENT_DEPART, T0),
            occupancy.ParkingEvent(occupancy.EVENT_ARRIVE, T0 + datetime.timedelta(minutes=5), True),
        ]
        assert occupancy.build_trips(events)[0].uncertain is True

    def test_出庫した日ごとに回数と時間をまとめる(self):
        next_day = T0 + datetime.timedelta(days=1)
        trips = [
            occupancy.Trip(T0, T0 + datetime.timedelta(minutes=45)),
            occupancy.Trip(T0 + datetime.timedelta(hours=3), T0 + datetime.timedelta(hours=4)),
            occupancy.Trip(next_day, None),
        ]
        assert occupancy.summarize_daily(trips) == [
            occupancy.DailySummary(T0.date(), 2, 105.0),
            occupancy.DailySummary(next_day.date(), 1, 0.0),
        ]


class _FakeStdout:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk

    def close(self) -> None:
        self.closed = True


def _fake_popen(monkeypatch, frames, returncode=0):
    """ffmpeg を起動せず、与えたフレームを stdout から流す偽プロセスに差し替える。"""
    process = MagicMock()
    process.stdout = _FakeStdout(b"".join(f.tobytes() for f in frames))
    process.wait.return_value = returncode
    process.poll.return_value = returncode
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(occupancy.subprocess, "Popen", popen)
    return popen, process


class TestExtractFrames:
    def test_フレームごとに区切って返し時刻を等間隔で付ける(self, monkeypatch):
        popen, process = _fake_popen(monkeypatch, [_frame(False, False), _frame(True, True)])
        results = list(occupancy.analyze_recording("/rec/a.mp4", T0, 60))

        assert [(obs.timestamp, obs.state) for obs, _ in results] == [
            (T0, A), (T0 + datetime.timedelta(seconds=60), P)]
        assert results[0][1].shape == (H, W, 3)
        assert process.stdout.closed
        cmd = popen.call_args.args[0]
        assert "/rec/a.mp4" in cmd and f"fps=1/60,scale={W}:{H}" in cmd
        # キーフレームだけをデコードする指定は入力(-i)より前に置く必要がある
        assert cmd.index("-skip_frame") < cmd.index("-i")

    def test_ionice_があれば先頭に付ける(self, monkeypatch):
        monkeypatch.setattr(occupancy.shutil, "which", lambda name: "/usr/bin/ionice")
        assert occupancy.build_frame_command("a.mp4", 60)[0] == "ionice"
        monkeypatch.setattr(occupancy.shutil, "which", lambda name: None)
        assert occupancy.build_frame_command("a.mp4", 60)[0] == "nice"

    def test_ffmpeg_が失敗したら読めた分を返した後で例外にする(self, monkeypatch):
        _fake_popen(monkeypatch, [_frame(False, False)], returncode=234)

        frames = occupancy.extract_frames("/rec/broken.mp4", 60)
        assert next(frames).shape == (H, W, 3)
        with pytest.raises(occupancy.FrameExtractionError, match="/rec/broken.mp4") as raised:
            next(frames)
        # ffmpeg 自身が読めないと答えた(正の終了コード)ので、読み直しても無駄
        assert raised.value.returncode == 234
        assert raised.value.unreadable is True

    def test_時間切れで強制終了した場合は読み直す余地を残す(self, monkeypatch):
        _fake_popen(monkeypatch, [], returncode=-9)  # kill で終わると負の終了コード

        with pytest.raises(occupancy.FrameExtractionError) as raised:
            list(occupancy.extract_frames("/rec/a.mp4", 60))
        assert raised.value.unreadable is False
        assert occupancy.FrameExtractionError("x").unreadable is False

    def test_時間切れで_ffmpeg_を強制終了する見張りを掛け終われば外す(self, monkeypatch):
        _, process = _fake_popen(monkeypatch, [_frame(False, False)])
        timer = MagicMock()
        monkeypatch.setattr(occupancy.threading, "Timer", timer)

        list(occupancy.extract_frames("/rec/a.mp4", 60))

        # 読み取りが固まっても抜けられるよう、読み始める前から kill の予約が入っている
        timer.assert_called_once_with(occupancy.FFMPEG_TIMEOUT_SECONDS, process.kill)
        timer.return_value.start.assert_called_once()
        timer.return_value.cancel.assert_called_once()

    def test_途中で読むのをやめたら_ffmpeg_を残さない(self, monkeypatch):
        _, process = _fake_popen(monkeypatch, [_frame(False, False), _frame(False, False)])
        process.poll.return_value = None  # まだ動いている

        frames = occupancy.extract_frames("/rec/a.mp4", 60)
        next(frames)
        frames.close()

        process.kill.assert_called_once()
        assert process.stdout.closed
