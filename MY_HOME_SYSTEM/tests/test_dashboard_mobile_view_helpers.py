# MY_HOME_SYSTEM/tests/test_dashboard_mobile_view_helpers.py
"""時刻の相対表記(`services/home_status_service.py`)の回帰テスト。

#829: 以前はStreamlit版ダッシュボード(`views/dashboard/common.py`)のグラフ・表
ヘルパー(`render_chart`/`render_table`)もここでテストしていたが、Streamlit版の
廃止に伴い削除した。時刻の相対表記だけは見守りページ・システムページの両方が
使う共通ロジックとして`home_status_service`に残っている。
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import home_status_service


class TestTimeFormatting:
    """Issue #658 の方針どおり、実時刻に任せず freezegun で固定する。"""

    NOW = datetime.fromisoformat("2026-09-21T12:00:00+09:00")

    @pytest.mark.parametrize("value,expected", [
        ("2026-09-21 11:59:30+09:00", "たった今"),
        ("2026-09-21 11:58:00+09:00", "2分前"),
        ("2026-09-21 09:00:00+09:00", "3時間前"),
        ("2026-09-19 12:00:00+09:00", "2日前"),
    ])
    def test_relative_wording(self, value, expected):
        assert home_status_service.format_relative_time(value, self.NOW) == expected

    def test_old_values_fall_back_to_a_short_absolute_form(self):
        """1週間より古いと「10日前」より日付のほうが分かりやすい。"""
        assert home_status_service.format_relative_time("2026-08-01 12:00:00+09:00", self.NOW) == "08/01 12:00"

    def test_future_values_are_not_reported_as_relative(self):
        assert home_status_service.format_relative_time("2026-09-21 12:30:00+09:00", self.NOW) == "09/21 12:30"

    def test_naive_timestamps_are_treated_as_jst(self):
        """DB由来の naive な時刻を UTC 扱いすると9時間ずれる(`core.utils` と同じ方針)。"""
        assert home_status_service.format_relative_time("2026-09-21 11:58:00", self.NOW) == "2分前"

    @pytest.mark.parametrize("value", [None, "", "not-a-time", float("nan")])
    def test_unparsable_values_render_as_empty(self, value):
        assert home_status_service.format_relative_time(value, self.NOW) == ""
        assert home_status_service.format_short_timestamp(value) == ""
