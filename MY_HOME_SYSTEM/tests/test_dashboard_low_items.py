# MY_HOME_SYSTEM/tests/test_dashboard_low_items.py
"""
dashboard.py の Low項目(#410)の回帰テスト:

- (旧 L-L2: AIレポートのtimestamp旧フォーマットのパース。Issue #701 で
  AIレポート(セバスチャン)機能ごと退役したため、該当テストは
  「表示されないこと」の回帰テストに置き換えた。)
- L-L5: 例外発生時に traceback.format_exc() を st.code() で画面表示していたが、
  内部のファイルパス・設定値が露出するため、ログにのみ出力するよう変更した
  ことを確認する。

main() はStreamlitのUI呼び出し(st.sidebar, st.tabs等)を多数含む大きな関数の
ため、st・各Viewモジュール・analysis_service・send_pushを広くモックして
テストする。
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import dashboard


def _mock_st():
    mock = MagicMock()
    mock.sidebar.__enter__ = MagicMock(return_value=mock)
    mock.sidebar.__exit__ = MagicMock(return_value=False)
    mock.tabs.return_value = [MagicMock() for _ in range(5)]
    # main()先頭の操作列(_render_header_actions)が st.columns(2) を使うため、
    # 指定された個数ぶんのカラムを返す(MagicMockのままだとアンパックできない)。
    mock.columns.side_effect = lambda spec, **kwargs: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    mock.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock.expander.return_value.__exit__ = MagicMock(return_value=False)
    return mock


def _patch_view_modules():
    """main()内で呼ばれる各Viewモジュールの関数呼び出しをすべて無害化する"""
    return [
        patch.object(dashboard.misc_tab, "render_traffic"),
        patch.object(dashboard.misc_tab, "render_photos"),
        patch.object(dashboard.sensor_tab, "render_electricity"),
        patch.object(dashboard.sensor_tab, "render_temperature"),
        patch.object(dashboard.health_tab, "render"),
        patch.object(dashboard.sensor_tab, "render_takasago"),
        patch.object(dashboard.log_tab, "render_logs"),
        patch.object(dashboard.log_tab, "render_resources"),
        patch.object(dashboard.log_tab, "render_nas_status"),
        patch.object(dashboard.log_tab, "render_server_logs"),
        patch.object(dashboard.log_tab, "render_maintenance"),
        patch.object(dashboard.misc_tab, "render_bicycle"),
        patch.object(dashboard.summary, "render_summary"),
    ]


def _run_main():
    import pandas as pd

    mock_st = _mock_st()
    patches = _patch_view_modules()
    # Issue #741: main() のデータ読み込みは view_common のキャッシュ付き
    # ラッパー経由になった。素の analysis_service を差し替えると
    # @st.cache_data がテスト間で結果を持ち越すため、ラッパー自体を差し替える。
    with patch.object(dashboard, "st", mock_st), \
         patch.object(dashboard.view_common, "load_sensor_data_cached", return_value=pd.DataFrame()), \
         patch.object(dashboard.view_common, "load_generic_data_cached", return_value=pd.DataFrame()), \
         patch.object(dashboard.view_common, "load_bicycle_data_cached", return_value=pd.DataFrame()), \
         patch.object(dashboard.view_common, "load_nas_status_cached", return_value=None), \
         patch.object(dashboard.analysis_service, "apply_friendly_names", return_value=pd.DataFrame()), \
         patch.object(dashboard, "logger") as mock_logger:
        for p in patches:
            p.start()
        try:
            dashboard.main()
        finally:
            for p in patches:
                p.stop()
    return mock_st, mock_logger


class TestAiReportRetired:
    """Issue #701 (2026-09-19): AIレポート(セバスチャン)は書込側が2026-07-16以降
    停止しており、2か月前の内容を「最新の報告」として出し続けていたため、
    オーナー判断で機能ごと退役した。表示・読み出し・テーブル名定数が
    復活していないことを確認する(テーブル ai_report_records 自体は履歴として残す)。"""

    def test_dashboard_does_not_render_ai_report(self):
        mock_st, _ = _run_main()

        labels = [str(c.args[0]) for c in mock_st.expander.call_args_list if c.args]
        assert not any("セバスチャン" in label for label in labels), labels
        assert not hasattr(dashboard, "_render_ai_report")

    def test_reader_and_config_constant_are_removed(self):
        import config
        from services import analysis_service

        assert not hasattr(analysis_service, "load_ai_report")
        assert not hasattr(config, "SQLITE_TABLE_AI_REPORT")


class TestNoTracebackOnScreen:
    def test_exception_is_logged_but_not_shown_via_st_code(self):
        """L-L5 (#410): 例外発生時にtracebackを画面表示(st.code)しないこと。
        ログにのみ出力すること。"""
        mock_st = _mock_st()
        with patch.object(dashboard, "st", mock_st), \
             patch.object(dashboard.view_common, "load_sensor_data_cached", side_effect=RuntimeError("boom")), \
             patch.object(dashboard, "send_push"), \
             patch.object(dashboard, "logger") as mock_logger:
            dashboard.main()

        mock_st.code.assert_not_called()
        mock_st.error.assert_called_once()
        # tracebackはlogger.error経由でのみ出力される
        logged_texts = [str(c.args[0]) for c in mock_logger.error.call_args_list]
        assert any("RuntimeError" in t or "Traceback" in t for t in logged_texts), logged_texts
