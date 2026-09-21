# MY_HOME_SYSTEM/tests/test_dashboard_cache.py
"""
ダッシュボードのデータ読み込みキャッシュの回帰テスト(Issue #741)。

`dashboard.py` の「🔄 データを更新」ボタンは以前から `st.cache_data.clear()` を
呼んでいたが、リポジトリ全体に `@st.cache_data` が1つも存在せず、実際には
何も消していなかった。Streamlit はウィジェット操作・タブ切替・ページ読み込みの
たびにスクリプト全体を再実行するため、「タブを1回切り替える」たびに
SQLite から約33,000行を読み直していた。

「キャッシュがあるつもりのコードだけが残る」状態に戻らないよう、

- `main()` が素の `analysis_service` ではなくキャッシュ付きラッパー経由で読むこと
- ラッパーが実際に2回目の呼び出しを省くこと
- `st.cache_data.clear()` がそれを捨てられること
- キャッシュが View 層に閉じていること(`analysis_service` に Streamlit 依存を
  持ち込むと `unified_server.py` 側が巻き添えになる)

をテストで固定する。
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import streamlit as st

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import dashboard
from views.dashboard import common as view_common


@pytest.fixture(autouse=True)
def _clear_dashboard_cache():
    """`@st.cache_data` はプロセス内でグローバルに残るため、テストごとに捨てる。
    キャッシュキーには関数の引数しか入らず、`analysis_service` を差し替えても
    無効化されないので、明示的なクリアが必要になる。"""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _mock_st():
    mock = MagicMock()
    mock.sidebar.__enter__ = MagicMock(return_value=mock)
    mock.sidebar.__exit__ = MagicMock(return_value=False)
    mock.columns.side_effect = lambda spec, **kwargs: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    # 選択中のタブだけを描画するようになったため(tests/test_dashboard_lazy_tabs.py)、
    # 既定はホームタブ・折りたたみセクションは閉じた状態とする。
    mock.segmented_control.return_value = "home"
    mock.toggle.return_value = False
    mock.query_params = {}
    return mock


def _patch_view_modules():
    return [
        patch.object(dashboard.misc_tab, "render_traffic"),
        patch.object(dashboard.misc_tab, "render_photos"),
        patch.object(dashboard.misc_tab, "render_bicycle"),
        patch.object(dashboard.sensor_tab, "render_electricity"),
        patch.object(dashboard.sensor_tab, "render_temperature"),
        patch.object(dashboard.sensor_tab, "render_takasago"),
        patch.object(dashboard.health_tab, "render"),
        patch.object(dashboard.log_tab, "render_logs"),
        patch.object(dashboard.log_tab, "render_resources"),
        patch.object(dashboard.log_tab, "render_nas_status"),
        patch.object(dashboard.log_tab, "render_server_logs"),
        patch.object(dashboard.log_tab, "render_maintenance"),
        patch.object(dashboard.summary, "render_summary"),
    ]


class TestMainReadsThroughTheCache:
    """再実行のたびに素の analysis_service を叩く状態へ戻っていないこと。"""

    def test_home_tab_reads_through_the_cache_only(self):
        mock_st = _mock_st()
        patches = _patch_view_modules()
        with patch.object(dashboard, "st", mock_st), \
             patch.object(dashboard.view_common, "st", mock_st), \
             patch.object(dashboard.view_common, "load_sensor_data_cached", return_value=pd.DataFrame()) as cached_sensor, \
             patch.object(dashboard.view_common, "load_generic_data_cached", return_value=pd.DataFrame()) as cached_generic, \
             patch.object(dashboard.view_common, "load_bicycle_data_cached", return_value=pd.DataFrame()) as cached_bicycle, \
             patch.object(dashboard.view_common, "load_nas_status_cached", return_value=None) as cached_nas, \
             patch.object(dashboard.analysis_service, "apply_friendly_names", return_value=pd.DataFrame()), \
             patch.object(dashboard.analysis_service, "load_sensor_data") as raw_sensor, \
             patch.object(dashboard.analysis_service, "load_generic_data") as raw_generic, \
             patch.object(dashboard.analysis_service, "load_bicycle_data") as raw_bicycle, \
             patch.object(dashboard.analysis_service, "load_nas_status") as raw_nas, \
             patch.object(dashboard, "logger"):
            for p in patches:
                p.start()
            try:
                dashboard.main()
            finally:
                for p in patches:
                    p.stop()

        cached_sensor.assert_called_once_with(limit=10000)
        cached_bicycle.assert_called_once_with(limit=3000)
        cached_nas.assert_called_once_with()
        # ホームタブが使うのは車のテーブルだけ。以前は5タブ分(子供・排便・食事・
        # 車・防犯ログ)を main() の先頭でまとめて読んでいたが、選択中のタブしか
        # 描画しなくなったため、そのタブが使わないテーブルは読まない。
        cached_generic.assert_called_once_with(dashboard.config.SQLITE_TABLE_CAR)

        for raw in (raw_sensor, raw_generic, raw_bicycle, raw_nas):
            raw.assert_not_called()

    def test_watch_tab_reads_only_the_security_log_when_sections_are_closed(self):
        """閉じたセクション(高砂・健康管理)のぶんまで読みに行かないこと。"""
        mock_st = _mock_st()
        mock_st.segmented_control.return_value = "watch"
        patches = _patch_view_modules()
        with patch.object(dashboard, "st", mock_st), \
             patch.object(dashboard.view_common, "st", mock_st), \
             patch.object(dashboard.view_common, "load_sensor_data_cached", return_value=pd.DataFrame()) as cached_sensor, \
             patch.object(dashboard.view_common, "load_generic_data_cached", return_value=pd.DataFrame()) as cached_generic, \
             patch.object(dashboard.analysis_service, "apply_friendly_names", return_value=pd.DataFrame()), \
             patch.object(dashboard, "logger"):
            for p in patches:
                p.start()
            try:
                dashboard.main()
            finally:
                for p in patches:
                    p.stop()

        cached_generic.assert_called_once_with("security_logs", limit=100)
        cached_sensor.assert_not_called()

    def test_refresh_button_clears_the_cache_and_reruns(self):
        """「🔄 データを更新」が TTL を待たずに捨てる操作として機能すること。"""
        mock_st = _mock_st()
        mock_st.button.return_value = True
        with patch.object(dashboard, "st", mock_st):
            dashboard._render_header_actions()

        mock_st.cache_data.clear.assert_called_once()
        mock_st.rerun.assert_called_once()

    def test_refresh_button_does_nothing_when_not_pressed(self):
        mock_st = _mock_st()
        mock_st.button.return_value = False
        with patch.object(dashboard, "st", mock_st):
            dashboard._render_header_actions()

        mock_st.cache_data.clear.assert_not_called()
        mock_st.rerun.assert_not_called()


class TestCachedLoadersActuallyCache:
    def test_sensor_loader_hits_the_database_once_for_repeated_calls(self):
        df = pd.DataFrame([{"timestamp": "2026-09-19 12:00"}])
        with patch.object(view_common.analysis_service, "load_sensor_data", return_value=df) as mock_load:
            first = view_common.load_sensor_data_cached(limit=10000)
            second = view_common.load_sensor_data_cached(limit=10000)

        mock_load.assert_called_once_with(limit=10000)
        assert first.equals(second)

    def test_different_limits_are_cached_separately(self):
        with patch.object(view_common.analysis_service, "load_sensor_data",
                          return_value=pd.DataFrame()) as mock_load:
            view_common.load_sensor_data_cached(limit=100)
            view_common.load_sensor_data_cached(limit=200)

        assert mock_load.call_count == 2

    def test_generic_loader_caches_per_table(self):
        with patch.object(view_common.analysis_service, "load_generic_data",
                          return_value=pd.DataFrame()) as mock_load:
            view_common.load_generic_data_cached("child_records")
            view_common.load_generic_data_cached("child_records")
            view_common.load_generic_data_cached("food_records")

        assert mock_load.call_count == 2

    def test_bicycle_loader_caches(self):
        with patch.object(view_common.analysis_service, "load_bicycle_data",
                          return_value=pd.DataFrame()) as mock_load:
            view_common.load_bicycle_data_cached(limit=3000)
            view_common.load_bicycle_data_cached(limit=3000)

        mock_load.assert_called_once_with(limit=3000)

    def test_nas_loader_caches_even_when_it_returns_none(self):
        with patch.object(view_common.analysis_service, "load_nas_status",
                          return_value=None) as mock_load:
            assert view_common.load_nas_status_cached() is None
            assert view_common.load_nas_status_cached() is None

        mock_load.assert_called_once()

    def test_clear_makes_the_next_call_read_again(self):
        with patch.object(view_common.analysis_service, "load_sensor_data",
                          return_value=pd.DataFrame()) as mock_load:
            view_common.load_sensor_data_cached(limit=10000)
            st.cache_data.clear()
            view_common.load_sensor_data_cached(limit=10000)

        assert mock_load.call_count == 2

    def test_cached_values_are_isolated_from_caller_mutation(self):
        """`st.cache_data` は値渡しなので、呼び出し元が返り値を書き換えても
        次の呼び出しに漏れないこと(描画側は .copy() 前提のコードがある)。"""
        df = pd.DataFrame([{"power_watts": 100}])
        with patch.object(view_common.analysis_service, "load_sensor_data", return_value=df):
            first = view_common.load_sensor_data_cached(limit=10000)
            first.loc[0, "power_watts"] = 999
            second = view_common.load_sensor_data_cached(limit=10000)

        assert second.loc[0, "power_watts"] == 100


class TestExternalIoIsCachedToo:
    """DB以外の重い読み取り(HTTPスクレイピング・journalctl・年間集計SQL)も
    同じTTLで共有されること。

    DBの読み取りだけをキャッシュしていた頃は、1回の描画で
    JR運行情報のスクレイピングがサマリーと「おでかけ」タブから2回走り、
    `journalctl` はたたまれた expander のために毎回起動していた。
    """

    def test_traffic_is_scraped_once_even_when_two_views_need_it(self):
        from views.dashboard import misc_tab

        fake_status = {
            "宝塚線": {"status": "平常運転", "detail": "", "is_delay": False},
            "神戸線": {"status": "平常運転", "detail": "", "is_delay": False},
        }
        mock_st = MagicMock()
        mock_st.columns.side_effect = lambda spec, **kwargs: [
            MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
        ]
        with patch.object(view_common.train_service, "get_jr_traffic_status",
                          return_value=fake_status) as mock_scrape, \
             patch.object(view_common, "load_route_info_cached", return_value={"summary": "取得失敗"}), \
             patch.object(misc_tab, "st", mock_st):
            view_common.load_jr_traffic_status_cached()  # ホームタブのサマリーカード
            misc_tab.render_traffic()                    # おでかけタブ

        mock_scrape.assert_called_once()

    def test_system_logs_are_cached_and_the_refresh_button_clears_them(self):
        from views.dashboard import log_tab

        with patch.object(view_common.analysis_service, "get_system_logs",
                          return_value="log body") as mock_logs:
            view_common.get_system_logs_cached(lines=50)
            view_common.get_system_logs_cached(lines=50)
            assert mock_logs.call_count == 1

            view_common.get_system_logs_cached.clear()
            view_common.get_system_logs_cached(lines=50)
            assert mock_logs.call_count == 2

        # 「🔄 ログを更新」はキャッシュを捨ててから再実行すること
        # (捨てないと押しても同じ内容が返る)
        mock_st = MagicMock()
        mock_st.columns.side_effect = lambda spec, **kwargs: [
            MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
        ]
        mock_st.button.return_value = True
        mock_st.radio.return_value = "直近のログを表示"
        mock_st.selectbox.return_value = "全て"
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab, "view_common") as mock_common:
            log_tab.render_server_logs()

        mock_common.get_system_logs_cached.clear.assert_called_once()
        mock_st.rerun.assert_called_once()

    def test_yearly_temperature_and_resources_are_cached(self):
        with patch.object(view_common.analysis_service, "load_yearly_temperature_stats",
                          return_value=pd.DataFrame()) as mock_yearly, \
             patch.object(view_common.analysis_service, "get_memory_usage",
                          return_value={"percent": 10}) as mock_mem, \
             patch.object(view_common.analysis_service, "get_disk_usage",
                          return_value={"percent": 20}) as mock_disk:
            for _ in range(2):
                view_common.load_yearly_temperature_stats_cached(2026)
                view_common.get_memory_usage_cached()
                view_common.get_disk_usage_cached()

        mock_yearly.assert_called_once_with(2026)
        mock_mem.assert_called_once()
        mock_disk.assert_called_once()


class TestViewsDoNotBypassTheCache:
    """View が素のサービスを直接呼ぶ形に戻っていないこと。

    戻しても画面は同じに見えるが、スマホでの初回表示にHTTP 2本と
    サブプロセス1本が毎回上乗せされる(気づけるのは実機だけ)。
    """

    FORBIDDEN = {
        "summary.py": ["train_service.get_jr_traffic_status", "analysis_service.get_memory_usage"],
        "misc_tab.py": ["train_service.get_jr_traffic_status", "train_service.get_route_info"],
        "sensor_tab.py": ["analysis_service.load_yearly_temperature_stats"],
        "log_tab.py": [
            "analysis_service.get_system_logs",
            "analysis_service.get_disk_usage",
            "analysis_service.get_memory_usage",
            "analysis_service.load_nas_status",
        ],
    }

    def test_view_modules_call_the_cached_wrappers(self):
        views_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "views", "dashboard"
        )
        for filename, forbidden_calls in self.FORBIDDEN.items():
            with open(os.path.join(views_dir, filename), encoding="utf-8") as f:
                source = f.read()
            for call in forbidden_calls:
                assert f"{call}(" not in source, f"{filename} が {call}() を直接呼んでいる"


class TestCacheLayering:
    def test_ttl_is_shorter_than_the_sensor_write_interval(self):
        """センサーの書き込み間隔(5分=300秒)より十分短いこと。
        長くすると「今の状態を見る」用途で古い値を見せてしまう。"""
        assert 0 < view_common.DASHBOARD_CACHE_TTL_SEC < 300

    def test_analysis_service_does_not_depend_on_streamlit(self):
        """キャッシュは View 層に閉じていること。`analysis_service` は
        `unified_server.py` からも import されるため、Streamlit 依存を
        持ち込むとサーバー側が巻き添えになる。"""
        import inspect

        from services import analysis_service

        source = inspect.getsource(analysis_service)
        assert "import streamlit" not in source
        assert "st.cache_data" not in source


class TestChartDownsampling:
    """グラフに渡す点数を間引くこと(A-3)。

    plotly に渡した点はそのまま WebSocket のペイロードとしてスマートフォンへ
    転送される。駐輪場の推移は3系列 × 1,000点前後あり、折れ線の形しか読まない。
    """

    def _series(self, n, name="A", start="2026-09-01"):
        return pd.DataFrame({
            "timestamp": pd.date_range(start, periods=n, freq="1min"),
            "waiting_count": range(n),
            "area_name": [name] * n,
        })

    def test_small_series_is_returned_untouched(self):
        df = self._series(10)
        assert view_common.downsample_for_chart(df, max_points=100) is df

    def test_large_series_is_thinned_to_the_limit(self):
        df = self._series(5000)
        out = view_common.downsample_for_chart(df, max_points=100)
        assert len(out) <= 101  # 末尾1点を足すぶんの余裕

    def test_the_latest_point_is_always_kept(self):
        """右端が欠けると「止まっている」ように見えるため。"""
        df = self._series(1001)
        out = view_common.downsample_for_chart(df, max_points=100)
        assert out["timestamp"].max() == df["timestamp"].max()
        assert out["waiting_count"].iloc[-1] == df["waiting_count"].iloc[-1]

    def test_each_series_is_thinned_independently(self):
        df = pd.concat([self._series(1000, "第1A"), self._series(10, "第3E")])
        out = view_common.downsample_for_chart(df, series_col="area_name", max_points=50)

        # 少ない系列は間引かれず、多い系列だけが間引かれる
        assert (out["area_name"] == "第3E").sum() == 10
        assert (out["area_name"] == "第1A").sum() <= 51

    def test_columns_and_dtypes_survive(self):
        """色分けに使う文字列列を落とさないこと(平均リサンプルにしない理由)。"""
        df = self._series(2000)
        out = view_common.downsample_for_chart(df, max_points=50)
        assert list(out.columns) == list(df.columns)
        assert out["area_name"].iloc[0] == "A"

    def test_empty_and_missing_column_are_passed_through(self):
        empty = pd.DataFrame()
        assert view_common.downsample_for_chart(empty) is empty
        no_ts = pd.DataFrame({"value": [1, 2, 3]})
        assert view_common.downsample_for_chart(no_ts) is no_ts
