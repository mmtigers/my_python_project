# MY_HOME_SYSTEM/tests/test_dashboard_lazy_tabs.py
"""選択中のタブだけを描画する(遅延評価)ことの回帰テスト。

背景(スマホでの体感速度):
    Streamlit の `st.tabs` / `st.expander` は、**選択されていないタブ・
    たたまれた expander の中身もすべて実行**する(描画結果をクライアント側で
    隠しているだけ)。そのため「🏠 ホーム」を開いただけの1回の描画で

      - JR運行情報のスクレイピング(HTTP, timeout 5秒)
      - Yahoo!路線情報のスクレイピング(HTTP, timeout 5秒)
      - `journalctl` のサブプロセス起動
      - 年間気温の集計SQL
      - plotly のグラフ6枚ぶんの生成とWebSocket転送

    まで毎回走っていた。タブを10個から5個に束ね直した再設計は「探しやすさ」を
    直したが、実行される処理量は1つも減っていなかった。

    `st.segmented_control`(選択状態がPython側から読める)と
    `view_common.lazy_section`(`st.toggle` ベース)に置き換え、
    選択中のタブ・開いているセクションの中身だけを実行するようにした。

この構造は「うっかり `st.tabs` に戻す」と**画面は同じに見えるまま**元の
重さへ戻る(スマホでしか体感できない)ため、ここで固定する。
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import dashboard
from views.dashboard import common as view_common

_DASHBOARD_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.py"
)


def _mock_st(active_tab="home", toggles_open=False):
    """`dashboard.st` を差し替えるスタブ。

    `segmented_control` は選択中のタブキーを、`toggle`(= lazy_section)は
    開閉状態を返す。
    """
    mock = MagicMock()
    mock.sidebar.__enter__ = MagicMock(return_value=mock)
    mock.sidebar.__exit__ = MagicMock(return_value=False)
    mock.columns.side_effect = lambda spec, **kwargs: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    mock.segmented_control.return_value = active_tab
    mock.toggle.return_value = toggles_open
    mock.query_params = {}
    mock.session_state = {}
    return mock


def _patch_all_renderers(stack):
    """全タブの描画関数をモックに差し替え、呼ばれたかどうかだけを見る。"""
    from contextlib import ExitStack  # noqa: F401  (型の明示のみ)

    return {
        "summary": stack.enter_context(patch.object(dashboard.summary, "render_summary")),
        "traffic": stack.enter_context(patch.object(dashboard.misc_tab, "render_traffic")),
        "bicycle": stack.enter_context(patch.object(dashboard.misc_tab, "render_bicycle")),
        "photos": stack.enter_context(patch.object(dashboard.misc_tab, "render_photos")),
        "takasago": stack.enter_context(patch.object(dashboard.sensor_tab, "render_takasago")),
        "health": stack.enter_context(patch.object(dashboard.health_tab, "render")),
        "electricity": stack.enter_context(patch.object(dashboard.sensor_tab, "render_electricity")),
        "temperature": stack.enter_context(patch.object(dashboard.sensor_tab, "render_temperature")),
        "resources": stack.enter_context(patch.object(dashboard.log_tab, "render_resources")),
        "nas": stack.enter_context(patch.object(dashboard.log_tab, "render_nas_status")),
        "server_logs": stack.enter_context(patch.object(dashboard.log_tab, "render_server_logs")),
        "sensor_logs": stack.enter_context(patch.object(dashboard.log_tab, "render_logs")),
        "maintenance": stack.enter_context(patch.object(dashboard.log_tab, "render_maintenance")),
    }


def _run_main(active_tab="home", toggles_open=False):
    """`dashboard.main()` を1回実行し、(mock_st, 各描画関数のモック) を返す。"""
    from contextlib import ExitStack

    mock_st = _mock_st(active_tab=active_tab, toggles_open=toggles_open)
    with ExitStack() as stack:
        stack.enter_context(patch.object(dashboard, "st", mock_st))
        stack.enter_context(patch.object(view_common, "st", mock_st))
        stack.enter_context(patch.object(view_common, "load_sensor_data_cached", return_value=pd.DataFrame()))
        stack.enter_context(patch.object(view_common, "load_generic_data_cached", return_value=pd.DataFrame()))
        stack.enter_context(patch.object(view_common, "load_bicycle_data_cached", return_value=pd.DataFrame()))
        stack.enter_context(patch.object(view_common, "load_nas_status_cached", return_value=None))
        stack.enter_context(patch.object(dashboard.analysis_service, "apply_friendly_names", return_value=pd.DataFrame()))
        stack.enter_context(patch.object(dashboard, "logger"))
        renderers = _patch_all_renderers(stack)
        dashboard.main()
    return mock_st, renderers


class TestOnlyTheActiveTabRenders:
    """選択中のタブの中身だけが実行されること。"""

    def test_home_tab_does_not_run_the_other_tabs(self):
        _, renderers = _run_main(active_tab="home")

        renderers["summary"].assert_called_once()
        # HTTPスクレイピング・サブプロセス・年間集計SQLを伴うタブは走らない
        for name in ("traffic", "photos", "electricity", "resources", "nas"):
            assert not renderers[name].called, f"{name} がホームタブで実行されている"

    def test_system_tab_does_not_run_the_summary(self):
        _, renderers = _run_main(active_tab="sys")

        renderers["resources"].assert_called_once()
        renderers["nas"].assert_called_once()
        assert not renderers["summary"].called
        assert not renderers["traffic"].called

    @pytest.mark.parametrize("tab_key", list(dashboard.TAB_KEYS))
    def test_every_tab_key_has_a_renderer_and_runs(self, tab_key):
        """タブを増やしたときに描画関数の登録漏れで空白画面にならないこと。"""
        assert tab_key in dashboard.TAB_RENDERERS
        mock_st, _ = _run_main(active_tab=tab_key)
        # 例外に落ちて全体エラー表示になっていないこと
        assert not mock_st.error.called


class TestLazySections:
    """たたまれたセクションの中身が実行されないこと。"""

    def test_closed_sections_do_not_render(self):
        _, renderers = _run_main(active_tab="sys", toggles_open=False)

        for name in ("server_logs", "sensor_logs", "maintenance"):
            assert not renderers[name].called, f"{name} が閉じた状態で実行されている"

    def test_opened_sections_render(self):
        _, renderers = _run_main(active_tab="sys", toggles_open=True)

        renderers["server_logs"].assert_called_once()
        renderers["sensor_logs"].assert_called_once()
        renderers["maintenance"].assert_called_once()

    def test_watch_tab_keeps_the_heavy_sections_closed_by_default(self):
        _, renderers = _run_main(active_tab="watch", toggles_open=False)

        renderers["photos"].assert_called_once()
        assert not renderers["takasago"].called
        assert not renderers["health"].called

    def test_lazy_section_returns_the_toggle_state(self):
        mock_st = MagicMock()
        mock_st.toggle.return_value = True
        with patch.object(view_common, "st", mock_st):
            assert view_common.lazy_section("📜 サーバーログ", key="server_logs") is True

        # key は衝突しないよう接頭辞付きで session_state に入る
        assert mock_st.toggle.call_args.kwargs["key"] == "lazy_section_server_logs"
        assert mock_st.toggle.call_args.kwargs["value"] is False


class TestTabSelection:
    """`?tab=` によるタブの保持・ディープリンク。"""

    def test_query_param_selects_the_initial_tab(self):
        mock_st = _mock_st()
        mock_st.query_params = {"tab": "watch"}
        with patch.object(dashboard, "st", mock_st):
            assert dashboard._requested_tab() == "watch"

    def test_unknown_or_missing_tab_falls_back_to_home(self):
        mock_st = _mock_st()
        with patch.object(dashboard, "st", mock_st):
            mock_st.query_params = {}
            assert dashboard._requested_tab() == dashboard.DEFAULT_TAB_KEY
            mock_st.query_params = {"tab": "../../etc/passwd"}
            assert dashboard._requested_tab() == dashboard.DEFAULT_TAB_KEY

    def test_selector_writes_the_active_tab_back_to_the_url(self):
        """更新ボタン(st.rerun)や再接続でホームタブに戻らないようにするため。"""
        mock_st = _mock_st(active_tab="life")
        mock_st.query_params = {"tab": "life"}
        with patch.object(dashboard, "st", mock_st):
            assert dashboard._render_tab_selector() == "life"
        assert mock_st.query_params["tab"] == "life"

    def test_deselecting_keeps_the_previous_tab(self):
        """`st.segmented_control` は選択中の項目をもう一度押すと None になる。
        タブとしては常にどれか1つが選ばれているのが正しい。"""
        mock_st = _mock_st(active_tab=None)
        mock_st.query_params = {"tab": "sys"}
        mock_st.session_state = {dashboard.TAB_SELECTOR_STATE_KEY: None}
        with patch.object(dashboard, "st", mock_st):
            assert dashboard._render_tab_selector() == "sys"

    def test_jump_to_tab_moves_without_a_page_reload(self):
        """サマリーの「詳しく見る」は、ページ再読み込み(リンク)ではなく
        session_state の書き換えで移動すること(スマホでは再読み込みが数秒かかる)。"""
        mock_st = _mock_st()
        with patch.object(dashboard, "st", mock_st):
            dashboard._jump_to_tab("watch")

        assert mock_st.session_state[dashboard.TAB_SELECTOR_STATE_KEY] == "watch"
        assert mock_st.query_params["tab"] == "watch"


    def test_initial_tab_comes_from_the_url_on_the_first_run(self):
        """`/dashboard?tab=watch` をホーム画面に置けること。"""
        mock_st = _mock_st(active_tab="watch")
        mock_st.query_params = {"tab": "watch"}
        with patch.object(dashboard, "st", mock_st):
            dashboard._render_tab_selector()

        assert mock_st.session_state[dashboard.TAB_SELECTOR_STATE_KEY] == "watch"
        # session_state に入れる方式なので、`default=` は渡さない
        # (両方あると Streamlit が警告をログに出す)
        assert "default" not in mock_st.segmented_control.call_args.kwargs


class TestStructureDoesNotRegress:
    """`st.tabs` / `st.expander` に戻すと、画面は同じに見えるまま重さだけ戻る。"""

    def _source(self) -> str:
        with open(_DASHBOARD_PY, encoding="utf-8") as f:
            return f.read()

    def test_dashboard_does_not_use_st_tabs(self):
        assert "st.tabs(" not in self._source()

    def test_dashboard_does_not_use_st_expander(self):
        assert "st.expander(" not in self._source()
