# MY_HOME_SYSTEM/tests/test_parking_away_heatmap.py
"""くらしページ「🚗 車」ヒートマップ(1時間のうち車が出ていた分)のテスト。

按分は `parking_occupancy_service.hourly_away_grid`、読み出しは
`parking_record_service.get_hourly_away`、表示は `dashboard_page_service.render_home_climate_heatmap`(metric=car)。
"""
import datetime
import os
import sys

import pandas as pd
from freezegun import freeze_time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import dashboard_page_service as page
from services import parking_occupancy_service as occupancy

P, A, U = occupancy.STATE_PRESENT, occupancy.STATE_ABSENT, occupancy.STATE_UNKNOWN
D = datetime.datetime
NOW = D(2026, 11, 28, 15, 30)
SINCE = datetime.date(2026, 11, 1)


def _grid(trips, obs=(), now=NOW, since=SINCE):
    return occupancy.hourly_away_grid(trips, obs, since, now)


class TestHourlyAwayGrid:
    def test_時間をまたぐ外出は重なった分ずつに分かれる(self):
        grid = _grid([occupancy.Trip(D(2026, 11, 27, 8, 45), D(2026, 11, 27, 10, 15))])
        row = grid["2026-11-27"]
        assert row[8] == 15 and row[9] == 60 and row[10] == 15
        assert row[7] is None and row[11] is None

    def test_日をまたぐ外出は日付の境で分かれる(self):
        grid = _grid([occupancy.Trip(D(2026, 11, 26, 23, 30), D(2026, 11, 27, 1, 30))])
        assert grid["2026-11-26"][23] == 30
        assert grid["2026-11-27"][0] == 60 and grid["2026-11-27"][1] == 30

    def test_1時間に複数回出ても60分を超えない(self):
        grid = _grid([occupancy.Trip(D(2026, 11, 27, 9, 0), D(2026, 11, 27, 9, 40)),
                      occupancy.Trip(D(2026, 11, 27, 9, 30), D(2026, 11, 27, 10, 0))])
        assert grid["2026-11-27"][9] == 60

    def test_戻りが未確定の外出は現在時刻まで数える(self):
        grid = _grid([occupancy.Trip(D(2026, 11, 28, 14, 0), None)])
        row = grid["2026-11-28"]
        assert row[14] == 60 and row[15] == 30 and row[16] is None

    def test_観測のあった時間は不在0分の記録ありにする(self):
        grid = _grid([], [occupancy.Observation(D(2026, 11, 27, 12, 5), P)])
        assert grid["2026-11-27"][12] == 0.0
        assert grid["2026-11-27"][13] is None

    def test_不明の観測は記録ありにしない(self):
        grid = _grid([], [occupancy.Observation(D(2026, 11, 27, 12, 5), U)])
        assert grid == {}

    def test_表示期間より前の日は含めない(self):
        grid = _grid([occupancy.Trip(D(2026, 10, 31, 22, 0), D(2026, 11, 1, 1, 0))])
        assert "2026-10-31" not in grid
        assert grid["2026-11-01"][0] == 60

    def test_段階の境界(self):
        assert [occupancy.away_level(m) for m in (0, 0.5, 1, 19, 20, 39, 40, 60)] == [0, 0, 1, 1, 2, 2, 3, 3]


class TestRenderCarHeatmap:
    @freeze_time("2026-11-28 15:30:00")
    def _render(self, car_away):
        return page.render_home_climate_heatmap(
            pd.DataFrame(), NOW, dashboard_path="/dashboard/", metric="car", days=3, car_away=car_away)

    def test_段階ごとのクラスと記録なしと未来を区別する(self):
        row = [None] * 24
        row[8], row[9], row[10], row[11] = 0.0, 15.0, 30.0, 60.0
        out = self._render({"2026-11-28": row})
        assert 'title="11/28 8時台: 車あり"' in out and "hm-0" in out
        assert 'hm-1" title="11/28 9時台: 不在15分"' in out
        assert 'hm-2" title="11/28 10時台: 不在30分"' in out
        assert 'hm-3" title="11/28 11時台: 不在60分"' in out
        assert 'cl-none" title="11/28 12時台: 記録なし"' in out
        assert 'cl-none" title="11/28 20時台: まだ先です"' in out
        assert "車あり" in out and "40分〜" in out

    def test_行は新しい日が上で3日分(self):
        out = self._render({"2026-11-28": [0.0] * 24})
        assert out.count('class="climate-row"') == 3
        assert out.index("11/28(") < out.index("11/27(") < out.index("11/26(")

    def test_データが無ければ案内だけ出す(self):
        out = self._render(None)
        assert "車の記録がまだありません" in out and "climate-row" not in out

    def test_切り替えに車が出て対象の行は出ない(self):
        out = self._render({})
        assert "🚗 車" in out and 'aria-current="true">🚗 車' in out
        assert "30日の推移" not in out  # 温度・湿度用の表示切替は出さない

    def test_metricの解釈(self):
        assert page.parse_climate_metric("car") == "car"
        assert page.parse_climate_metric("x") == page.CLIMATE_DEFAULT_METRIC
