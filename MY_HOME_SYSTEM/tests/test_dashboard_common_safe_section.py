# MY_HOME_SYSTEM/tests/test_dashboard_common_safe_section.py
"""
views/dashboard/common.py の safe_section (Issue #438) のテスト。

以前はダッシュボード各所で列存在チェック・try/exceptの方針が関数ごとに
バラバラで、特にdashboard.pyはmain()全体を1つのtry/exceptで囲んでいたため、
いずれか1つのタブの描画で例外が起きるとダッシュボード全体がエラー画面になり、
無関係な他のタブまで巻き込んでいた。safe_sectionはセクション単位に例外を
隔離し、失敗したセクションだけプレースホルダを表示する共通ヘルパー。
"""
import os
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import common as view_common


class TestSafeSection:
    def test_success_path_does_not_call_st_error(self):
        mock_st = MagicMock()
        with patch.object(view_common, "st", mock_st):
            with view_common.safe_section("テスト"):
                pass

        mock_st.error.assert_not_called()

    def test_exception_is_caught_and_shows_placeholder_with_section_name(self):
        mock_st = MagicMock()
        with patch.object(view_common, "st", mock_st), \
             patch.object(view_common, "logger") as mock_logger:
            with view_common.safe_section("クエスト"):
                raise RuntimeError("boom")

        mock_st.error.assert_called_once()
        assert "クエスト" in mock_st.error.call_args[0][0]
        # L-L5 (#410)と同じ方針: tracebackは画面に出さずログにのみ残す
        logged_texts = [str(c.args[0]) for c in mock_logger.error.call_args_list]
        assert any("RuntimeError" in t or "Traceback" in t for t in logged_texts), logged_texts

    def test_exception_does_not_propagate_past_the_context_manager(self):
        mock_st = MagicMock()
        with patch.object(view_common, "st", mock_st), \
             patch.object(view_common, "logger"):
            with view_common.safe_section("駐輪場"):
                raise ValueError("something broke")
            # ここに到達すれば例外が正しく吸収されている


class TestDashboardTabIsolation:
    """1つのタブの描画失敗が他のタブの描画を止めないことの回帰テスト(#438)。"""

    def test_one_tab_raising_does_not_prevent_other_tabs_from_rendering(self):
        import dashboard
        import pandas as pd

        mock_st = MagicMock()
        mock_st.sidebar.__enter__ = MagicMock(return_value=mock_st)
        mock_st.sidebar.__exit__ = MagicMock(return_value=False)
        mock_st.tabs.return_value = [MagicMock() for _ in range(10)]
        mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard, "st", mock_st))
            stack.enter_context(patch.object(view_common, "st", mock_st))
            stack.enter_context(patch.object(dashboard.analysis_service, "load_sensor_data", return_value=pd.DataFrame()))
            stack.enter_context(patch.object(dashboard.analysis_service, "load_generic_data", return_value=pd.DataFrame()))
            stack.enter_context(patch.object(dashboard.analysis_service, "apply_friendly_names", return_value=pd.DataFrame()))
            stack.enter_context(patch.object(dashboard.analysis_service, "load_bicycle_data", return_value=pd.DataFrame()))
            stack.enter_context(patch.object(dashboard.analysis_service, "load_nas_status", return_value=None))
            stack.enter_context(patch.object(dashboard.analysis_service, "load_ai_report", return_value=None))
            stack.enter_context(patch.object(dashboard.summary, "render_summary"))
            stack.enter_context(patch.object(dashboard.quest_tab, "render", side_effect=RuntimeError("quest tab exploded")))
            stack.enter_context(patch.object(dashboard.misc_tab, "render_traffic"))
            stack.enter_context(patch.object(dashboard.misc_tab, "render_photos"))
            mock_electricity = stack.enter_context(patch.object(dashboard.sensor_tab, "render_electricity"))
            stack.enter_context(patch.object(dashboard.sensor_tab, "render_temperature"))
            stack.enter_context(patch.object(dashboard.health_tab, "render"))
            stack.enter_context(patch.object(dashboard.sensor_tab, "render_takasago"))
            stack.enter_context(patch.object(dashboard.log_tab, "render_logs"))
            stack.enter_context(patch.object(dashboard.log_tab, "render_system"))
            stack.enter_context(patch.object(dashboard.misc_tab, "render_bicycle"))
            stack.enter_context(patch.object(dashboard, "logger"))
            dashboard.main()

        # クエストタブが例外を投げても、後続の電力・環境タブは描画が呼ばれること
        mock_electricity.assert_called_once()
        # ダッシュボード全体のエラー画面(st.error)ではなく、
        # クエストタブのみのプレースホルダとして扱われていること
        error_calls = [str(c.args[0]) for c in mock_st.error.call_args_list if c.args]
        assert any("クエスト" in t for t in error_calls), error_calls
