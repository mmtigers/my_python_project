# MY_HOME_SYSTEM/tests/test_dashboard_sensor_tab.py
"""
views/dashboard/sensor_tab.py の回帰テスト(Issue #754)。

Issue #451/#453: device_type の判定文字列(`Nature Remo E Lite` / `Plug` /
`Meter`)はデバイスマスタ側の名称が変わると該当データが恒常的に0件になり、
グラフが黙って空になる。それを検知できるようにするため「0件のときは
st.info ではなく st.warning を出す」という設計になっている。この意図が
テストで固定されていなかったため、`.coveragerc` の omit を外すのに合わせて追加した。
"""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import common as view_common
from views.dashboard import sensor_tab

# JST 固定。naive な datetime() を localize するより、オフセット付きの
# ISO 文字列から起こすほうが「どの時刻か」が読んで分かる。
NOW = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

SENSOR_COLUMNS = [
    "timestamp", "location", "device_type", "friendly_name",
    "contact_state", "power_watts", "temperature_celsius", "humidity_percent",
]


def _mock_st():
    """st.columns の戻り値をアンパックできる形にした Streamlit のスタブ。"""
    mock = MagicMock()
    mock.columns.side_effect = lambda spec, **kwargs: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    return mock


def _patch_st(mock_st):
    """`sensor_tab` と、そこから呼ばれる `view_common` の st をまとめて差し替える。

    グラフ・表の描画は `view_common.render_chart` / `render_table` 経由に
    なった(モードバー無効化・列絞りを1箇所に寄せたため)。
    """
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch.object(sensor_tab, "st", mock_st))
    stack.enter_context(patch.object(view_common, "st", mock_st))
    return stack


def _sensor_df(rows):
    df = pd.DataFrame(rows, columns=SENSOR_COLUMNS)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def _row(**kwargs):
    base = {
        "timestamp": NOW, "location": "伊丹", "device_type": "Meter",
        "friendly_name": "リビング", "contact_state": "", "power_watts": 0,
        "temperature_celsius": 25.0, "humidity_percent": 50.0,
    }
    base.update(kwargs)
    return base


def _warning_texts(mock_st):
    return [str(c.args[0]) for c in mock_st.warning.call_args_list if c.args]


class TestRenderElectricity:
    def test_empty_dataframe_shows_info_and_returns_early(self):
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_electricity(pd.DataFrame(), NOW)

        mock_st.info.assert_called_once()
        mock_st.plotly_chart.assert_not_called()

    def test_todays_power_data_is_plotted(self):
        df = _sensor_df([
            _row(device_type=sensor_tab.DEVICE_TYPE_NATURE_REMO_E_LITE,
                 power_watts=420, timestamp=NOW - timedelta(hours=1)),
        ])
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_electricity(df, NOW)

        assert mock_st.plotly_chart.call_count >= 1

    def test_yesterday_series_is_shifted_onto_todays_axis(self):
        """昨日の系列は「今日と重ねて比較する」ため +1日 して描画される。"""
        df = _sensor_df([
            _row(device_type=sensor_tab.DEVICE_TYPE_NATURE_REMO_E_LITE,
                 power_watts=300, timestamp=NOW - timedelta(days=1)),
        ])
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_electricity(df, NOW)

        fig = mock_st.plotly_chart.call_args_list[0].args[0]
        assert len(fig.data) == 1
        assert fig.data[0].name == "昨日"

    def test_missing_nature_remo_data_warns_instead_of_failing_silently(self):
        """Issue #453: device_type 名の変更等で恒常的に0件になっても
        気づけるよう、情報表示ではなく警告を出すこと。"""
        df = _sensor_df([_row(device_type="Plug A", power_watts=10)])
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_electricity(df, NOW)

        assert any(sensor_tab.DEVICE_TYPE_NATURE_REMO_E_LITE in t for t in _warning_texts(mock_st))

    def test_plug_data_is_plotted_and_absence_warns(self):
        df_with = _sensor_df([_row(device_type="Smart Plug", power_watts=60)])
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_electricity(df_with, NOW)
        assert "プラグデータなし" not in _warning_texts(mock_st)

        df_without = _sensor_df([_row(device_type="Meter", power_watts=0)])
        mock_st2 = _mock_st()
        with patch.object(sensor_tab, "st", mock_st2):
            sensor_tab.render_electricity(df_without, NOW)
        assert "プラグデータなし" in _warning_texts(mock_st2)


class TestRenderTemperature:
    def _patch_yearly(self, df):
        return patch.object(sensor_tab.view_common, "load_yearly_temperature_stats_cached", return_value=df)

    def test_empty_dataframe_shows_info_and_returns_early(self):
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_temperature(pd.DataFrame(), NOW)
        mock_st.info.assert_called_once()

    def test_dataframe_without_device_type_column_returns_early(self):
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_temperature(pd.DataFrame([{"timestamp": NOW}]), NOW)
        mock_st.info.assert_called_once()

    def test_meter_data_draws_temperature_and_humidity_charts(self):
        df = _sensor_df([_row(device_type="WoIOSensor Meter", timestamp=NOW - timedelta(hours=2))])
        mock_st = _mock_st()
        with _patch_st(mock_st), self._patch_yearly(pd.DataFrame()):
            sensor_tab.render_temperature(df, NOW)

        # 室温・湿度の2枚(年間データは空なので描画されない)
        assert mock_st.plotly_chart.call_count == 2
        assert "年間データがまだありません。" in [str(c.args[0]) for c in mock_st.info.call_args_list if c.args]

    def test_missing_meter_data_warns_for_both_temperature_and_humidity(self):
        df = _sensor_df([_row(device_type="Plug", timestamp=NOW)])
        mock_st = _mock_st()
        with _patch_st(mock_st), self._patch_yearly(pd.DataFrame()):
            sensor_tab.render_temperature(df, NOW)

        warnings = _warning_texts(mock_st)
        assert "今日の室温データなし" in warnings
        assert "今日の湿度データなし" in warnings

    def test_yearly_stats_add_only_the_series_that_exist(self):
        """年間グラフは out_max/out_min/in_max/in_min の存在する列だけを描く。"""
        df = _sensor_df([_row(device_type="Meter")])
        yearly = pd.DataFrame({
            "date": ["2026-01-01", "2026-01-02"],
            "out_max": [10.0, 12.0],
            "in_min": [18.0, 17.5],
        })
        mock_st = _mock_st()
        with _patch_st(mock_st), self._patch_yearly(yearly):
            sensor_tab.render_temperature(df, NOW)

        yearly_fig = mock_st.plotly_chart.call_args_list[-1].args[0]
        names = sorted(trace.name for trace in yearly_fig.data)
        assert names == sorted(["最高気温(外)", "最低室温(内)"])


class TestRenderTakasago:
    def test_empty_dataframe_renders_nothing(self):
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_takasago(pd.DataFrame())
        mock_st.dataframe.assert_not_called()

    def test_only_takasago_rows_are_shown(self):
        df = _sensor_df([
            _row(location="高砂", friendly_name="玄関", contact_state="open"),
            _row(location="伊丹", friendly_name="リビング", contact_state="open"),
        ])
        mock_st = _mock_st()
        with _patch_st(mock_st):
            sensor_tab.render_takasago(df)

        shown = mock_st.dataframe.call_args.args[0]
        assert len(shown) == 1
        # 列は表示名へ。時刻は「09/21 03:04 (3分前)」の短縮表記になる
        # (ISO文字列はスマホの表で1列を丸ごと食うため)。
        assert list(shown.columns) == ["時刻", "センサー", "状態"]
        assert shown.iloc[0]["センサー"] == "玄関"
