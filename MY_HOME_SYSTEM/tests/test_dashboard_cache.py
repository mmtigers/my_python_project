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
    mock.tabs.return_value = [MagicMock() for _ in range(5)]
    mock.columns.side_effect = lambda spec, **kwargs: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    mock.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock.expander.return_value.__exit__ = MagicMock(return_value=False)
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

    def test_main_does_not_call_analysis_service_loaders_directly(self):
        mock_st = _mock_st()
        patches = _patch_view_modules()
        with patch.object(dashboard, "st", mock_st), \
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
        # 子供・排便・食事・車・防犯ログの5テーブル
        assert cached_generic.call_count == 5

        for raw in (raw_sensor, raw_generic, raw_bicycle, raw_nas):
            raw.assert_not_called()

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
