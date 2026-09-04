# MY_HOME_SYSTEM/tests/test_dashboard_low_items.py
"""
dashboard.py の Low項目(#410)の回帰テスト:

- L-L2: AIレポートのtimestampが"T"を含まない旧フォーマット
  ("YYYY-MM-DD HH:MM:SS")の場合、以前は常に現在時刻(datetime.now())に
  フォールバックしており、レポートの実際の生成時刻に関わらず「たった今」の
  報告であるかのように表示されていた。実際のタイムスタンプをJSTとしてパース
  するようになったことを確認する。
- L-L5: 例外発生時に traceback.format_exc() を st.code() で画面表示していたが、
  内部のファイルパス・設定値が露出するため、ログにのみ出力するよう変更した
  ことを確認する。

main() はStreamlitのUI呼び出し(st.sidebar, st.tabs等)を多数含む大きな関数の
ため、st・各Viewモジュール・analysis_service・commonを広くモックして
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
    mock.tabs.return_value = [MagicMock() for _ in range(11)]
    mock.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock.expander.return_value.__exit__ = MagicMock(return_value=False)
    return mock


def _patch_view_modules():
    """main()内で呼ばれる各Viewモジュールの関数呼び出しをすべて無害化する"""
    return [
        patch.object(dashboard.quest_tab, "render"),
        patch.object(dashboard.misc_tab, "render_traffic"),
        patch.object(dashboard.misc_tab, "render_photos"),
        patch.object(dashboard.sensor_tab, "render_electricity"),
        patch.object(dashboard.sensor_tab, "render_temperature"),
        patch.object(dashboard.health_tab, "render"),
        patch.object(dashboard.sensor_tab, "render_takasago"),
        patch.object(dashboard.log_tab, "render_logs"),
        patch.object(dashboard.log_tab, "render_trends"),
        patch.object(dashboard.log_tab, "render_system"),
        patch.object(dashboard.misc_tab, "render_bicycle"),
        patch.object(dashboard.summary, "render_summary"),
    ]


def _run_main_with_report(report):
    import pandas as pd

    mock_st = _mock_st()
    patches = _patch_view_modules()
    with patch.object(dashboard, "st", mock_st), \
         patch.object(dashboard.analysis_service, "load_sensor_data", return_value=pd.DataFrame()), \
         patch.object(dashboard.analysis_service, "load_generic_data", return_value=pd.DataFrame()), \
         patch.object(dashboard.analysis_service, "apply_friendly_names", return_value=pd.DataFrame()), \
         patch.object(dashboard.analysis_service, "load_bicycle_data", return_value=pd.DataFrame()), \
         patch.object(dashboard.analysis_service, "load_nas_status", return_value=None), \
         patch.object(dashboard.analysis_service, "load_ai_report", return_value=report), \
         patch.object(dashboard, "logger") as mock_logger:
        for p in patches:
            p.start()
        try:
            dashboard.main()
        finally:
            for p in patches:
                p.stop()
    return mock_st, mock_logger


class TestAiReportTimestampFallbackTimezone:
    def test_legacy_timestamp_without_t_is_parsed_not_replaced_with_now(self):
        """L-L2 (#410): 'T'を含まない旧フォーマットのtimestampが、現在時刻ではなく
        実際の値からパースされること(深夜0時台のレポートなら🌙アイコンになるはず)。"""
        report = {"timestamp": "2020-01-01 02:00:00", "message": "テスト報告"}

        mock_st, _ = _run_main_with_report(report)

        # expanderのラベルに実際の時刻(02:00)が使われ、現在時刻ではないこと
        expander_calls = [str(c.args[0]) for c in mock_st.expander.call_args_list if c.args]
        assert any("02:00" in label for label in expander_calls), expander_calls

    def test_iso_timestamp_with_t_still_parses_correctly(self):
        """既存動作(T区切りのISO形式)が壊れていないこと"""
        report = {"timestamp": "2026-01-01T15:30:00+09:00", "message": "テスト報告"}

        mock_st, _ = _run_main_with_report(report)

        expander_calls = [str(c.args[0]) for c in mock_st.expander.call_args_list if c.args]
        assert any("15:30" in label for label in expander_calls), expander_calls


class TestNoTracebackOnScreen:
    def test_exception_is_logged_but_not_shown_via_st_code(self):
        """L-L5 (#410): 例外発生時にtracebackを画面表示(st.code)しないこと。
        ログにのみ出力すること。"""
        mock_st = _mock_st()
        with patch.object(dashboard, "st", mock_st), \
             patch.object(dashboard.analysis_service, "load_sensor_data", side_effect=RuntimeError("boom")), \
             patch.object(dashboard, "common") as mock_common, \
             patch.object(dashboard, "logger") as mock_logger:
            dashboard.main()

        mock_st.code.assert_not_called()
        mock_st.error.assert_called_once()
        # tracebackはlogger.error経由でのみ出力される
        logged_texts = [str(c.args[0]) for c in mock_logger.error.call_args_list]
        assert any("RuntimeError" in t or "Traceback" in t for t in logged_texts), logged_texts
