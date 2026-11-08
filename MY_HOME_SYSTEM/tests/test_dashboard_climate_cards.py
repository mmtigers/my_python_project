# MY_HOME_SYSTEM/tests/test_dashboard_climate_cards.py
"""ホームの「🌡️ 室温」グループ(拠点別の温湿度カード: 伊丹・高砂・長岡京)のテスト。

判定(`get_climate_status`)・補足(`describe_climate`)・拠点の判定(`climate_location`)・
カードの並びとリンク先、拠点の定義(`CLIMATE_SITES`)とヒートマップの切り替えの整合を確認する。
材料は `load_sensor_data` の戻り値と同じ形の DataFrame を直接渡す(DBは使わない)。
"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import dashboard_page_service, home_status_service

NOW = datetime.fromisoformat("2026-11-08T12:00:00+09:00")
NAGAOKAKYO_ID = "HUB2_NAGAOKAKYO"  # テスト用のダミーID


def _row(minutes_ago, temp, hum, *, device_id="remo-a", name="伊丹_Remo"):
    return {
        "timestamp": pd.Timestamp(NOW - timedelta(minutes=minutes_ago)),
        "device_id": device_id,
        "device_name": name,
        "device_type": "Meter",
        "temperature_celsius": temp,
        "humidity_percent": hum,
    }


def _df(*rows):
    return pd.DataFrame(list(rows)).sort_values("timestamp", ascending=False).reset_index(drop=True)


@pytest.fixture(autouse=True)
def _devices(monkeypatch):
    # 長岡京のハブ2は devices.json の location で拠点が決まる(機器名の先頭には頼らない)
    monkeypatch.setattr(config, "MONITOR_DEVICES", [
        {"id": NAGAOKAKYO_ID, "type": "Hub 2", "location": "長岡京", "name": "ハブ2"},
    ])


class TestClimateSites:
    def test_sites_are_itami_takasago_nagaokakyo_in_order(self):
        assert home_status_service.CLIMATE_LOCATIONS == ("伊丹", "高砂", "長岡京")

    def test_keys_and_titles_are_unique(self):
        keys = [key for _, key, _ in home_status_service.CLIMATE_SITES]
        titles = [title for _, _, title in home_status_service.CLIMATE_SITES]
        assert len(set(keys)) == len(keys)
        assert len(set(titles)) == len(titles)

    def test_heatmap_uses_the_same_definition(self):
        assert dashboard_page_service.CLIMATE_LOCATIONS == home_status_service.CLIMATE_LOCATIONS
        for name, key, _ in home_status_service.CLIMATE_SITES:
            assert dashboard_page_service.parse_climate_location(key) == name


class TestClimateLocation:
    def test_devices_json_location_wins(self):
        assert home_status_service.climate_location(NAGAOKAKYO_ID, "伊丹_紛らわしい名前") == "長岡京"

    def test_nature_remo_name_prefix(self):
        assert home_status_service.climate_location("unknown", "高砂_Remo") == "高砂"

    def test_unknown_device_is_none(self):
        assert home_status_service.climate_location("unknown", "温湿度計") is None


class TestGetClimateStatus:
    def test_latest_value_with_one_decimal_and_integer_humidity(self):
        df = _df(_row(5, 19.34, 43.0, device_id=NAGAOKAKYO_ID, name="ハブ2"), _row(10, 25.0, 60.0,
                 device_id=NAGAOKAKYO_ID, name="ハブ2"))
        assert home_status_service.get_climate_status(df, "長岡京", NOW) == ("19.3℃ / 43%", "theme-green")

    @pytest.mark.parametrize("temp,theme", [
        (14.9, "theme-blue"), (15.0, "theme-green"), (29.9, "theme-green"), (30.0, "theme-red"),
    ])
    def test_theme_by_temperature(self, temp, theme):
        df = _df(_row(5, temp, 50.0))
        assert home_status_service.get_climate_status(df, "伊丹", NOW)[1] == theme

    def test_stale_value_is_gray_but_still_shown(self):
        df = _df(_row(31, 32.0, 50.0))
        assert home_status_service.get_climate_status(df, "伊丹", NOW) == ("32.0℃ / 50%", "theme-gray")
        assert home_status_service.describe_climate(df, "伊丹", NOW) == "31分前の値(更新が止まっています)"

    def test_exactly_30_minutes_is_still_fresh(self):
        df = _df(_row(30, 20.0, 50.0))
        assert home_status_service.get_climate_status(df, "伊丹", NOW)[1] == "theme-green"
        assert home_status_service.describe_climate(df, "伊丹", NOW) == "30分前"

    def test_older_than_a_day_is_no_data(self):
        df = _df(_row(24 * 60 + 1, 20.0, 50.0))
        assert home_status_service.get_climate_status(df, "伊丹", NOW) == ("⚪ データなし", "theme-gray")
        assert home_status_service.describe_climate(df, "伊丹", NOW) is None

    def test_no_rows_is_no_data(self):
        assert home_status_service.get_climate_status(pd.DataFrame(), "長岡京", NOW) == ("⚪ データなし", "theme-gray")
        assert home_status_service.describe_climate(pd.DataFrame(), "長岡京", NOW) is None

    def test_zero_humidity_is_treated_as_missing(self):
        """湿度0.0は取れなかったときの代用値。前の記録の湿度も混ぜない。"""
        df = _df(_row(5, 20.0, 0.0), _row(10, 21.0, 55.0))
        assert home_status_service.get_climate_status(df, "伊丹", NOW)[0] == "20.0℃ / --%"

    def test_dry_air_is_noted(self):
        df = _df(_row(5, 20.0, 29.0))
        assert home_status_service.describe_climate(df, "伊丹", NOW) == "5分前・乾燥"

    def test_locations_do_not_mix(self):
        df = _df(_row(5, 10.0, 40.0, device_id="remo-b", name="高砂_Remo"),
                 _row(8, 22.0, 50.0, device_id=NAGAOKAKYO_ID, name="ハブ2"))
        assert home_status_service.get_climate_status(df, "高砂", NOW)[0] == "10.0℃ / 40%"
        assert home_status_service.get_climate_status(df, "長岡京", NOW)[0] == "22.0℃ / 50%"
        assert home_status_service.get_climate_status(df, "伊丹", NOW)[0] == "⚪ データなし"

    def test_newest_device_wins_within_a_location(self):
        df = _df(_row(5, 23.0, 50.0, device_id="remo-a", name="伊丹_Remo"),
                 _row(2, 24.0, 52.0, device_id="meter-x", name="伊丹_温湿度計"))
        assert home_status_service.get_climate_status(df, "伊丹", NOW)[0] == "24.0℃ / 52%"

    def test_rows_without_temperature_are_ignored(self):
        """電力・開閉の行(temperature_celsius が NaN)はカードの材料にしない。"""
        power = _row(1, float("nan"), float("nan"))
        df = _df(power, _row(5, 21.0, 45.0))
        assert home_status_service.get_climate_status(df, "伊丹", NOW)[0] == "21.0℃ / 45%"


class TestClimateCards:
    def _cards(self, df=None):
        return home_status_service.build_status_cards(NOW, pd.DataFrame() if df is None else df, None, None, 0)

    def test_three_cards_sit_between_watch_and_life(self):
        titles = [card.title for card in self._cards()]
        start = titles.index("🎥 カメラ") + 1
        assert titles[start:start + 3] == ["🏠 伊丹の室温", "👵 高砂の室温", "🧳 長岡京の室温"]
        assert titles[start + 3] == "💰 今月の電気代"

    def test_titles_are_unique(self):
        titles = [card.title for card in self._cards()]
        assert len(set(titles)) == len(titles)

    def test_group_and_link_to_the_heatmap_of_that_location(self):
        cards = [card for card in self._cards() if card.group == "climate"]
        hrefs = [home_status_service.card_detail_href(card, "/dashboard/") for card in cards]
        assert hrefs == [
            "/dashboard/?hm_loc=itami&hm_metric=temp#climate-heatmap",
            "/dashboard/?hm_loc=takasago&hm_metric=temp#climate-heatmap",
            "/dashboard/?hm_loc=nagaokakyo&hm_metric=temp#climate-heatmap",
        ]

    def test_values_come_from_the_sensor_frame(self):
        df = _df(_row(3, 19.3, 43.0, device_id=NAGAOKAKYO_ID, name="ハブ2"))
        (card,) = [card for card in self._cards(df) if card.title == "🧳 長岡京の室温"]
        assert (card.value, card.theme, card.sub) == ("19.3℃ / 43%", "theme-green", "3分前")

    def test_group_title_is_rendered(self):
        html_out = home_status_service.render_status_grid_html(self._cards(), dashboard_path="/dashboard/")
        assert html_out.index("👀 見守り") < html_out.index("🌡️ 室温") < html_out.index("💡 くらし")


class TestHeatmapWithThreeLocations:
    DF = pd.DataFrame(
        [
            ("2026-11-08", 9, "remo-a", "伊丹_Remo", 20.0, 50.0, 6),
            ("2026-11-08", 9, "remo-b", "高砂_Remo", 15.0, 45.0, 6),
            ("2026-11-08", 9, NAGAOKAKYO_ID, "ハブ2", 19.3, 43.0, 6),
        ],
        columns=["date", "hour", "device_id", "device_name", "temp_avg", "hum_avg", "n"],
    )

    def test_switch_has_three_locations_without_error(self):
        html = dashboard_page_service.render_home_climate_heatmap(
            self.DF, NOW, dashboard_path="/dashboard/", location="長岡京")
        for key in ("itami", "takasago", "nagaokakyo"):
            assert f'href="/dashboard/?hm_loc={key}&amp;hm_metric=temp#climate-heatmap"' in html
        assert "長岡京・1時間ごとの平均" in html
        assert 'title="11/8 9時台: 19.3℃"' in html

    def test_nagaokakyo_query_selects_it(self):
        assert dashboard_page_service.parse_climate_location("nagaokakyo") == "長岡京"
