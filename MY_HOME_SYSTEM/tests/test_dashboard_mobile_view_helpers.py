# MY_HOME_SYSTEM/tests/test_dashboard_mobile_view_helpers.py
"""スマホ向けの描画ヘルパー(グラフ・表・時刻表示)の回帰テスト。

実機のスマートフォンで確認した3点への対応を固定する。

1. plotly の既定設定はPCのマウス操作前提で、グラフ上を指でなぞると
   ページの縦スクロールがドラッグ(ズーム)に奪われ、画面から抜け出せない。
   モードバーも 390px 幅ではグラフ本体を圧迫する。
2. `st.dataframe` は画面幅を超えると横スクロールの箱になる。防犯ログは
   `image_path`(NAS上のフルパス)まで列にしていたため、検知時刻すら
   横に振らないと読めなかった。
3. 表示は最大60秒キャッシュされるため、画面の描画時刻を「最終更新」として
   出すと嘘になる。「いつのデータか」は相対表記のほうが速く読める。
"""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import plotly.graph_objects as go
import pytest
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import common as view_common


class TestRenderChart:
    def _render(self):
        mock_st = MagicMock()
        fig = go.Figure()
        with patch.object(view_common, "st", mock_st):
            view_common.render_chart(fig)
        return mock_st, fig

    def test_drag_is_disabled_so_the_page_can_be_scrolled_over_the_chart(self):
        _, fig = self._render()
        assert fig.layout.dragmode is False

    def test_mode_bar_is_hidden(self):
        mock_st, _ = self._render()
        config = mock_st.plotly_chart.call_args.kwargs["config"]
        assert config["displayModeBar"] is False
        assert config["scrollZoom"] is False

    def test_height_is_tightened_for_small_screens(self):
        _, fig = self._render()
        assert fig.layout.height == view_common.CHART_HEIGHT_PX
        assert view_common.CHART_HEIGHT_PX < 450  # plotly の既定

    def test_chart_fills_the_available_width(self):
        mock_st, _ = self._render()
        assert mock_st.plotly_chart.call_args.kwargs["width"] == "stretch"


class TestRenderTable:
    DF = pd.DataFrame([
        {"timestamp": "2026-09-21 10:00:00+09:00", "friendly_name": "玄関カメラ",
         "classification": "person", "image_path": "/mnt/nas/very/long/path/a.jpg"},
    ])

    def _render(self, df, columns, **kwargs):
        mock_st = MagicMock()
        with patch.object(view_common, "st", mock_st):
            view_common.render_table(df, columns, **kwargs)
        return mock_st

    def test_only_the_listed_columns_survive(self):
        mock_st = self._render(self.DF, {"timestamp": "検知時刻", "friendly_name": "デバイス"})
        shown = mock_st.dataframe.call_args.args[0]
        assert list(shown.columns) == ["検知時刻", "デバイス"]

    def test_missing_columns_are_skipped_without_raising(self):
        mock_st = self._render(self.DF, {"friendly_name": "デバイス", "nonexistent": "無い列"})
        shown = mock_st.dataframe.call_args.args[0]
        assert list(shown.columns) == ["デバイス"]

    def test_row_numbers_are_hidden(self):
        mock_st = self._render(self.DF, {"friendly_name": "デバイス"})
        assert mock_st.dataframe.call_args.kwargs["hide_index"] is True

    def test_timestamps_are_shortened(self):
        mock_st = self._render(self.DF, {"timestamp": "検知時刻"})
        shown = mock_st.dataframe.call_args.args[0]
        assert shown.iloc[0]["検知時刻"] == "09/21 10:00"

    @freeze_time("2026-09-21 10:05:00+09:00")
    def test_relative_time_is_appended_when_requested(self):
        mock_st = self._render(self.DF, {"timestamp": "検知時刻"}, relative_time=True)
        shown = mock_st.dataframe.call_args.args[0]
        assert shown.iloc[0]["検知時刻"] == "09/21 10:00 (5分前)"

    def test_empty_dataframe_shows_a_placeholder_instead_of_an_empty_box(self):
        mock_st = self._render(pd.DataFrame(), {"timestamp": "検知時刻"})
        mock_st.info.assert_called_once()
        mock_st.dataframe.assert_not_called()


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
        assert view_common.format_relative_time(value, self.NOW) == expected

    def test_old_values_fall_back_to_a_short_absolute_form(self):
        """1週間より古いと「10日前」より日付のほうが分かりやすい。"""
        assert view_common.format_relative_time("2026-08-01 12:00:00+09:00", self.NOW) == "08/01 12:00"

    def test_future_values_are_not_reported_as_relative(self):
        assert view_common.format_relative_time("2026-09-21 12:30:00+09:00", self.NOW) == "09/21 12:30"

    def test_naive_timestamps_are_treated_as_jst(self):
        """DB由来の naive な時刻を UTC 扱いすると9時間ずれる(`core.utils` と同じ方針)。"""
        assert view_common.format_relative_time("2026-09-21 11:58:00", self.NOW) == "2分前"

    @pytest.mark.parametrize("value", [None, "", "not-a-time", float("nan")])
    def test_unparsable_values_render_as_empty(self, value):
        assert view_common.format_relative_time(value, self.NOW) == ""
        assert view_common.format_short_timestamp(value) == ""


class TestCacheGeneration:
    @freeze_time("2026-09-21 10:00:00+09:00")
    def test_it_reports_the_fetch_time_not_the_render_time(self):
        """60秒キャッシュがある以上、描画時刻を「最終更新」と書くと嘘になる。"""
        import streamlit as st

        st.cache_data.clear()
        try:
            fetched = view_common.cache_generation_started_at()
            assert fetched.strftime("%H:%M:%S") == "10:00:00"

            with freeze_time("2026-09-21 10:00:30+09:00"):
                # TTL(60秒)の中では、再描画しても取得時刻は動かない
                assert view_common.cache_generation_started_at() == fetched
        finally:
            st.cache_data.clear()
