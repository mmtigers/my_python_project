# MY_HOME_SYSTEM/tests/test_parking_usage_trends.py
"""車の利用の曜日・時間帯の傾向(#1183)のテスト。

集計は `parking_occupancy_service.summarize_usage_trends`、読み出しは
`parking_record_service.get_usage_trends`、表示は `dashboard_page_service.render_car_usage_trends`。
"""
import datetime
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import dashboard_page_service
from services import parking_occupancy_service as occupancy
from services import parking_record_service as records

P, A = occupancy.STATE_PRESENT, occupancy.STATE_ABSENT
FEATURES = {
    P: occupancy.ParkingFeatures(False, 30.0, 0.05, 800.0, 40.0, 20.0, 0.04, 0.03),
    A: occupancy.ParkingFeatures(False, 30.0, 0.22, 8000.0, 160.0, 15.0, 0.02, 0.01),
}
# 2026-11-23 は月曜日
MON = datetime.datetime(2026, 11, 23)


def _trip(day_offset, hour, minutes=None):
    departed = MON + datetime.timedelta(days=day_offset, hours=hour)
    returned = None if minutes is None else departed + datetime.timedelta(minutes=minutes)
    return occupancy.Trip(departed, returned)


class TestSummarizeUsageTrends:
    def test_曜日ごとの回数と平均時間(self):
        trends = occupancy.summarize_usage_trends(
            [_trip(0, 8, 30), _trip(0, 18, 90), _trip(5, 10, 60)])
        by_day = {b.label: b for b in trends.by_weekday}
        assert trends.total_trips == 3
        assert [b.label for b in trends.by_weekday] == list("月火水木金土日")
        assert by_day["月"].trips == 2 and by_day["月"].average_minutes == 60
        assert by_day["土"].trips == 1 and by_day["土"].average_minutes == 60
        assert by_day["火"].trips == 0 and by_day["火"].average_minutes is None

    def test_出発の時間帯は境界の時刻を後ろの帯に入れる(self):
        trends = occupancy.summarize_usage_trends([_trip(0, 5), _trip(0, 6), _trip(0, 23), _trip(0, 0)])
        counts = [b.trips for b in trends.by_departure_band]
        assert counts == [2, 1, 0, 0, 0, 0, 1]
        assert sum(counts) == trends.total_trips

    def test_戻りが未確定の外出は回数に数えるが平均には入れない(self):
        trends = occupancy.summarize_usage_trends([_trip(0, 8, 40), _trip(0, 9, None)])
        monday = trends.by_weekday[0]
        assert monday.trips == 2 and monday.average_minutes == 40

    def test_外出が無くても全ての曜日と時間帯を返す(self):
        trends = occupancy.summarize_usage_trends([])
        assert trends.total_trips == 0
        assert len(trends.by_weekday) == 7
        assert len(trends.by_departure_band) == len(occupancy.DEPARTURE_BANDS)


class TestGetUsageTrends:
    def _save(self, start, states):
        observations = [occupancy.Observation(start + datetime.timedelta(minutes=i), s, FEATURES[s])
                        for i, s in enumerate(states)]
        records.save_recording(start.strftime("%Y%m%d_%H%M%S.mp4"), start, observations, start)

    def test_観測が無ければNone(self, isolated_db):
        assert records.get_usage_trends(datetime.datetime(2026, 11, 28, 12)) is None

    def test_保存した観測から外出を数える(self, isolated_db):
        self._save(MON + datetime.timedelta(hours=8), [P] * 3 + [A] * 5 + [P] * 3)
        trends = records.get_usage_trends(datetime.datetime(2026, 11, 28, 12))
        assert trends is not None and trends.total_trips == 1
        assert trends.by_weekday[0].trips == 1  # 月曜の朝に出発

    def test_期間より古い観測は含めない(self, isolated_db):
        old = datetime.datetime(2026, 11, 28, 12) - records.USAGE_TRENDS_WINDOW - datetime.timedelta(days=3)
        self._save(old, [P] * 3 + [A] * 5 + [P] * 3)
        assert records.get_usage_trends(datetime.datetime(2026, 11, 28, 12)) is None


class TestRenderCarUsageTrends:
    def test_データが無ければ節を出さない(self):
        assert dashboard_page_service.render_car_usage_trends(None) == ""
        assert dashboard_page_service.render_car_usage_trends(occupancy.summarize_usage_trends([])) == ""

    def test_曜日と時間帯の表を出す(self):
        html = dashboard_page_service.render_car_usage_trends(
            occupancy.summarize_usage_trends([_trip(0, 8, 90), _trip(0, 19, None)]))
        assert "車の利用の傾向" in html
        assert "<td>月</td><td>2回</td><td>1時間30分</td>" in html
        assert "朝(6〜9時)" in html and "夜(18〜21時)" in html
        assert "合計 2回" in html

    def test_くらしページに載る(self):
        trends = occupancy.summarize_usage_trends([_trip(0, 8, 30)])
        page = dashboard_page_service.render_life_page([], dashboard_path="/dashboard/", car_usage=trends)
        assert "車の利用の傾向" in page
        assert "車の利用の傾向" not in dashboard_page_service.render_life_page([], dashboard_path="/dashboard/")
