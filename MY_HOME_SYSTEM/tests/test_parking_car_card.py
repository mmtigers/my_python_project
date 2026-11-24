# MY_HOME_SYSTEM/tests/test_parking_car_card.py
"""ダッシュボードの「🚗 駐車場」カードを、車の在否で示すテスト(#1117)。

在否は `parking_occupancy_service.current_state`、まとめは `parking_record_service.get_car_summary`、
カードの値・色・補足は `home_status_service.get_car_status` / `describe_car` / `build_status_cards`。
"""
import datetime
import os
import sys

import pandas as pd
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import home_status_service
from services import parking_occupancy_service as occupancy
from services import parking_record_service as records

P, A, U = occupancy.STATE_PRESENT, occupancy.STATE_ABSENT, occupancy.STATE_UNKNOWN
T0 = datetime.datetime(2026, 11, 24, 8, 0)
FEATURES = {
    P: occupancy.ParkingFeatures(False, 30.0, 0.05, 800.0, 40.0, 20.0, 0.04, 0.03),
    A: occupancy.ParkingFeatures(False, 30.0, 0.22, 8000.0, 160.0, 15.0, 0.02, 0.01),
    U: occupancy.ParkingFeatures(False, 30.0, 0.16, 4000.0, 92.0, 15.0, 0.02, 0.01),
}


def _obs(states, start=T0):
    return [occupancy.Observation(start + datetime.timedelta(minutes=i), s, FEATURES[s])
            for i, s in enumerate(states)]


class TestCurrentState:
    def test_最後の変化の時刻からその状態になる(self):
        current = occupancy.current_state(_obs([P, P, P, A, A, A, A]))
        assert current == occupancy.CurrentState(A, T0 + datetime.timedelta(minutes=3),
                                                 T0 + datetime.timedelta(minutes=6))

    def test_変化が無ければ最初に確定した状態で_いつからかは分からない(self):
        current = occupancy.current_state(_obs([U, P, P, P, U]))
        assert (current.state, current.since) == (P, None)
        assert current.last_observed_at == T0 + datetime.timedelta(minutes=4)

    def test_一度も確定していなければ不明(self):
        assert occupancy.current_state(_obs([P, P, U, A])).state == U
        assert occupancy.current_state([]) == occupancy.CurrentState(U, None, None)

    def test_確定に満たない揺れでは変わらない(self):
        current = occupancy.current_state(_obs([A, A, A, P, P]))
        assert (current.state, current.since) == (A, None)


class TestCarSummary:
    def test_観測が無ければ_None(self, isolated_db):
        assert records.get_car_summary(T0) is None

    def test_いまの在否と今日の外出回数をまとめる(self, isolated_db):
        yesterday = T0 - datetime.timedelta(days=1)
        records.save_recording("20261123_080000.mp4", yesterday, _obs([P] * 3 + [A] * 3 + [P] * 3, yesterday), T0)
        records.save_recording("20261124_080000.mp4", T0, _obs([P] * 3 + [A] * 3 + [P] * 3 + [A] * 3), T0)

        summary = records.get_car_summary(T0 + datetime.timedelta(minutes=12))
        assert summary.state == A
        assert summary.since == T0 + datetime.timedelta(minutes=9)
        assert summary.last_observed_at == T0 + datetime.timedelta(minutes=11)
        # 昨日の1回は数えず、今日の2回(08:03 と 08:09 の出発。後者はまだ戻っていない)だけ
        assert summary.trips_today == 2

    def test_範囲より古い観測は読まない(self, isolated_db):
        old = T0 - records.CAR_SUMMARY_WINDOW - datetime.timedelta(hours=1)
        records.save_recording("20261122_070000.mp4", old, _obs([P] * 3, old), T0)
        assert records.get_car_summary(T0) is None


NOW = datetime.datetime(2026, 11, 24, 12, 0)


def _summary(state, since=None, last_minutes_ago=5, trips_today=0):
    return records.CarSummary(state, since, NOW - datetime.timedelta(minutes=last_minutes_ago), trips_today)


class TestCarCard:
    @pytest.mark.parametrize("state,expected", [
        (P, ("🅿️ 駐車中", "theme-green")),
        (A, ("🚙 外出中", "theme-blue")),
        (U, ("⚪ 判定できません", "theme-gray")),
    ])
    def test_在否で値と色を決める(self, state, expected):
        assert home_status_service.get_car_status(_summary(state), NOW) == expected

    def test_判定が古ければ止まっていると示す(self):
        stale = _summary(P, last_minutes_ago=home_status_service.CAR_STATE_STALE_MINUTES + 1)
        assert home_status_service.get_car_status(stale, NOW) == ("⚪ 判定が止まっています", "theme-gray")
        assert home_status_service.describe_car(stale, NOW) == "最終判定 11:14"
        fresh = _summary(P, last_minutes_ago=home_status_service.CAR_STATE_STALE_MINUTES - 1)
        assert home_status_service.get_car_status(fresh, NOW)[0] == "🅿️ 駐車中"

    def test_データが無ければ_None(self):
        assert home_status_service.get_car_status(None, NOW) is None
        assert home_status_service.describe_car(None, NOW) is None

    def test_補足に帰宅_出発の時刻と今日の外出回数を出す(self):
        arrived = _summary(P, since=datetime.datetime(2026, 11, 24, 9, 30), trips_today=2)
        assert home_status_service.describe_car(arrived, NOW) == "09:30 に帰宅・今日 2回外出"
        left = _summary(A, since=datetime.datetime(2026, 11, 23, 18, 5))
        assert home_status_service.describe_car(left, NOW) == "昨日 18:05 に出発"
        unknown_since = _summary(P)
        assert home_status_service.describe_car(unknown_since, NOW) is None

    def test_タイムゾーン付きの現在時刻でも比べられる(self):
        aware_now = home_status_service.pytz.timezone("Asia/Tokyo").localize(NOW)
        summary = _summary(A, since=datetime.datetime(2026, 11, 24, 9, 30))
        assert home_status_service.get_car_status(summary, aware_now) == ("🚙 外出中", "theme-blue")
        assert home_status_service.describe_car(summary, aware_now) == "09:30 に出発"


def _parking_card(cards):
    return next(c for c in cards if c.title == "🚗 駐車場")


class TestBuildStatusCards:
    def test_在否の判定があればカードに出す(self):
        car = _summary(A, since=datetime.datetime(2026, 11, 24, 9, 30), trips_today=1)
        card = _parking_card(home_status_service.build_status_cards(NOW, pd.DataFrame(), None, None, None, car=car))
        assert (card.value, card.theme, card.sub) == ("🚙 外出中", "theme-blue", "09:30 に出発・今日 1回外出")
        # 詳細(駐車場カメラの映像)へのリンク先は変わらない
        assert card.anchor == f"?camera={home_status_service.PARKING_CAMERA_ID}#camera-section"

    def test_判定が無ければ従来どおり動体検知を出す(self):
        card = _parking_card(home_status_service.build_status_cards(NOW, pd.DataFrame(), None, None, None))
        assert (card.value, card.theme, card.sub) == ("⚪ データなし", "theme-gray", None)

    def test_ホームと自動更新は同じカードを組み立てる(self, monkeypatch):
        car = _summary(P, since=datetime.datetime(2026, 11, 24, 9, 30))
        monkeypatch.setattr(home_status_service, "get_car_summary", lambda: car)
        monkeypatch.setattr(home_status_service, "get_camera_recording_states", lambda: None)
        monkeypatch.setattr(home_status_service, "get_cached_materials", lambda: home_status_service.DashboardMaterials(
            **{**{f: None for f in home_status_service.DashboardMaterials._fields}, "df_sensor": pd.DataFrame()}))
        cards, _ = home_status_service.collect_status_cards(NOW)
        assert _parking_card(cards).value == "🅿️ 駐車中"

    def test_サーバー本体は_OpenCV_を読み込まない(self):
        """判定と集計だけを使うダッシュボードのために、OpenCV は解析する時だけ読み込む。"""
        import subprocess
        code = ("import sys; sys.path.insert(0, '.'); import services.home_status_service; "
                "print('cv2' in sys.modules)")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
        assert out.stdout.strip().splitlines()[-1] == "False", out.stderr[-500:]
