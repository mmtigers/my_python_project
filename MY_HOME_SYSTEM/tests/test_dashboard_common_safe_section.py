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
            with view_common.safe_section("防犯カメラ"):
                raise ValueError("something broke")
            # ここに到達すれば例外が正しく吸収されている


class TestDashboardSectionIsolation:
    """1つのセクションの描画失敗が同じタブの他セクションを止めないことの回帰テスト(#438)。

    タブは `st.tabs` をやめて「選択中のタブだけ描画」になった
    (tests/test_dashboard_lazy_tabs.py)ため、巻き添えの範囲は
    「同じタブ内の後続セクション」になった。隔離の必要性は変わらない。
    """

    def test_one_section_raising_does_not_prevent_the_next_one(self):
        import dashboard
        import pandas as pd

        mock_st = MagicMock()
        mock_st.sidebar.__enter__ = MagicMock(return_value=mock_st)
        mock_st.sidebar.__exit__ = MagicMock(return_value=False)
        mock_st.columns.side_effect = lambda spec, **kwargs: [
            MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
        ]
        mock_st.segmented_control.return_value = "sys"
        mock_st.toggle.return_value = False
        mock_st.query_params = {}

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard, "st", mock_st))
            stack.enter_context(patch.object(view_common, "st", mock_st))
            # Issue #741: 読み込みは view_common のキャッシュ付きラッパー経由
            stack.enter_context(patch.object(view_common, "load_sensor_data_cached", return_value=pd.DataFrame()))
            stack.enter_context(patch.object(view_common, "load_generic_data_cached", return_value=pd.DataFrame()))
            stack.enter_context(patch.object(view_common, "load_nas_status_cached", return_value=None))
            stack.enter_context(patch.object(dashboard.log_tab, "render_resources",
                                             side_effect=RuntimeError("resources exploded")))
            mock_nas = stack.enter_context(patch.object(dashboard.log_tab, "render_nas_status"))
            stack.enter_context(patch.object(dashboard, "logger"))
            dashboard.main()

        # 「リソース状況」が例外を投げても、後続の「NAS状態」は描画が呼ばれること
        mock_nas.assert_called_once()
        # ダッシュボード全体のエラー画面(st.error)ではなく、
        # 失敗したセクションのみのプレースホルダとして扱われていること
        error_calls = [str(c.args[0]) for c in mock_st.error.call_args_list if c.args]
        assert any("リソース状況" in t for t in error_calls), error_calls

