# MY_HOME_SYSTEM/tests/test_dashboard_log_tab.py
"""
views/dashboard/log_tab.py の回帰テスト(Issue #754)。

とくに `render_maintenance` は `sudo systemctl restart home_system` を実行する
ため、誤操作防止の2段階(チェックボックス → ボタン)が実際に機能しているかを
コードを読む以外に確認する手段が無かった。`.coveragerc` の omit で
カバレッジ対象外だったことと合わせ、本番サービスを落とせる唯一のUI操作が
まったく検証されていない状態だったため、omit を外すのに合わせて追加した。

#651 で入った `subprocess.run(..., timeout=)` も、無いと systemd 側が応答
しない状況で Streamlit のスクリプト実行スレッドが無限に待ちダッシュボード
全体が固まるため、回帰テストで固定する。
"""
import os
import subprocess
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import log_tab


def _mock_st(**overrides):
    mock = MagicMock()
    mock.columns.side_effect = lambda spec, **kwargs: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    # 既定は「押されていない」。押下をテストする側で True に差し替える。
    mock.button.return_value = False
    mock.checkbox.return_value = False
    for key, value in overrides.items():
        getattr(mock, key).return_value = value
    return mock


class TestRenderLogs:
    def test_empty_dataframe_shows_info_and_returns_early(self):
        mock_st = _mock_st()
        with patch.object(log_tab, "st", mock_st):
            log_tab.render_logs(pd.DataFrame())
        mock_st.info.assert_called_once()
        mock_st.dataframe.assert_not_called()

    def test_selected_locations_filter_the_table(self):
        df = pd.DataFrame([
            {"timestamp": "2026-09-19 10:00", "friendly_name": "玄関", "location": "高砂",
             "contact_state": "open", "power_watts": 0},
            {"timestamp": "2026-09-19 11:00", "friendly_name": "リビング", "location": "伊丹",
             "contact_state": "", "power_watts": 30},
        ])
        mock_st = _mock_st()
        mock_st.multiselect.return_value = ["伊丹"]
        with patch.object(log_tab, "st", mock_st):
            log_tab.render_logs(df)

        shown = mock_st.dataframe.call_args.args[0]
        assert list(shown["location"]) == ["伊丹"]

    def test_all_locations_are_selected_by_default(self):
        df = pd.DataFrame([
            {"timestamp": "2026-09-19 10:00", "friendly_name": "玄関", "location": "高砂",
             "contact_state": "open", "power_watts": 0},
            {"timestamp": "2026-09-19 11:00", "friendly_name": "リビング", "location": "伊丹",
             "contact_state": "", "power_watts": 30},
        ])
        mock_st = _mock_st()
        with patch.object(log_tab, "st", mock_st):
            log_tab.render_logs(df)

        assert sorted(mock_st.multiselect.call_args.kwargs["default"]) == ["伊丹", "高砂"]


class TestRenderResources:
    def test_disk_and_memory_are_rendered_as_progress_bars(self):
        mock_st = _mock_st()
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.view_common, "get_disk_usage_cached", return_value={"percent": 61.4}), \
             patch.object(log_tab.view_common, "get_memory_usage_cached", return_value={"percent": 38.9}):
            log_tab.render_resources()

        assert [c.args[0] for c in mock_st.progress.call_args_list] == [61, 38]

    def test_unavailable_metrics_are_skipped_without_raising(self):
        mock_st = _mock_st()
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.view_common, "get_disk_usage_cached", return_value=None), \
             patch.object(log_tab.view_common, "get_memory_usage_cached", return_value=None):
            log_tab.render_resources()

        mock_st.progress.assert_not_called()


class TestRenderNasStatus:
    def test_missing_data_shows_info_and_returns_early(self):
        mock_st = _mock_st()
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.view_common, "load_nas_status_cached", return_value=None):
            log_tab.render_nas_status()

        mock_st.info.assert_called_once()
        mock_st.metric.assert_not_called()

    def test_ok_status_is_shown_with_check_marks(self):
        nas = pd.Series({"status_ping": "OK", "status_mount": "OK", "timestamp": "2026-09-19 12:00"})
        mock_st = _mock_st()
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.view_common, "load_nas_status_cached", return_value=nas):
            log_tab.render_nas_status()

        values = [c.args[1] for c in mock_st.metric.call_args_list]
        assert values[0] == "✅ OK"
        assert values[1] == "✅ OK"
        assert values[2] == "2026-09-19 12:00"

    def test_ng_status_is_shown_with_cross_marks(self):
        nas = pd.Series({"status_ping": "NG", "status_mount": "NG", "timestamp": "2026-09-19 12:00"})
        mock_st = _mock_st()
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.view_common, "load_nas_status_cached", return_value=nas):
            log_tab.render_nas_status()

        values = [c.args[1] for c in mock_st.metric.call_args_list]
        assert values[0].startswith("❌")
        assert values[1].startswith("❌")


class TestRenderServerLogs:
    def test_recent_mode_passes_selected_line_count_and_no_date(self):
        mock_st = _mock_st()
        mock_st.radio.return_value = "直近のログを表示"
        mock_st.selectbox.side_effect = [200, "全て"]
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.view_common, "get_system_logs_cached", return_value="log body") as mock_logs:
            log_tab.render_server_logs()

        assert mock_logs.call_args.kwargs == {"lines": 200, "priority": None, "target_date": None}
        mock_st.code.assert_called_once()

    def test_date_mode_passes_the_selected_date(self):
        target = date(2026, 9, 1)
        mock_st = _mock_st()
        mock_st.radio.return_value = "日付を指定して検索"
        mock_st.date_input.return_value = target
        mock_st.selectbox.return_value = "エラー"
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.view_common, "get_system_logs_cached", return_value="log body") as mock_logs:
            log_tab.render_server_logs()

        assert mock_logs.call_args.kwargs["target_date"] == target
        assert mock_logs.call_args.kwargs["priority"] == "err"

    def test_empty_logs_show_info_instead_of_an_empty_code_block(self):
        mock_st = _mock_st()
        mock_st.radio.return_value = "直近のログを表示"
        mock_st.selectbox.side_effect = [50, "警告"]
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.view_common, "get_system_logs_cached", return_value=""):
            log_tab.render_server_logs()

        mock_st.info.assert_called_once()
        mock_st.code.assert_not_called()


class TestRenderMaintenance:
    """本番サービスを落とせる唯一のUI操作の確認フロー。"""

    def test_restart_is_not_executed_without_the_confirmation_checkbox(self):
        mock_st = _mock_st()
        mock_st.checkbox.return_value = False
        # チェックが外れていてもボタン自体は押せる状態にして、
        # 「ボタンの描画すらされない」ことを確かめる。
        mock_st.button.return_value = True
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.subprocess, "run") as mock_run, \
             patch("services.backup_service.perform_backup", return_value=(True, "ok", 1.0)):
            log_tab.render_maintenance()

        mock_run.assert_not_called()
        button_labels = [str(c.args[0]) for c in mock_st.button.call_args_list if c.args]
        assert not any("システム再起動" in label for label in button_labels)

    def test_restart_requires_both_the_checkbox_and_the_button(self):
        mock_st = _mock_st()
        mock_st.checkbox.return_value = True
        mock_st.button.side_effect = lambda label, **kwargs: "システム再起動" in label
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.subprocess, "run") as mock_run, \
             patch("services.backup_service.perform_backup", return_value=(True, "ok", 1.0)):
            log_tab.render_maintenance()

        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == ["sudo", "systemctl", "restart", "home_system"]
        mock_st.success.assert_called()

    def test_restart_command_is_bounded_by_a_timeout(self):
        """#651: timeout が無いと systemd 側が応答しない状況で
        ダッシュボード全体が固まる。"""
        mock_st = _mock_st()
        mock_st.checkbox.return_value = True
        mock_st.button.side_effect = lambda label, **kwargs: "システム再起動" in label
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.subprocess, "run") as mock_run, \
             patch("services.backup_service.perform_backup", return_value=(True, "ok", 1.0)):
            log_tab.render_maintenance()

        assert mock_run.call_args.kwargs["timeout"] == log_tab.SUBPROCESS_TIMEOUT_SEC
        assert mock_run.call_args.kwargs["check"] is True

    def test_timeout_is_reported_with_the_limit_in_the_message(self):
        mock_st = _mock_st()
        mock_st.checkbox.return_value = True
        mock_st.button.side_effect = lambda label, **kwargs: "システム再起動" in label
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=30)), \
             patch("services.backup_service.perform_backup", return_value=(True, "ok", 1.0)):
            log_tab.render_maintenance()

        errors = [str(c.args[0]) for c in mock_st.error.call_args_list if c.args]
        assert any(str(log_tab.SUBPROCESS_TIMEOUT_SEC) in e for e in errors), errors

    def test_other_restart_failures_are_reported_without_crashing_the_section(self):
        mock_st = _mock_st()
        mock_st.checkbox.return_value = True
        mock_st.button.side_effect = lambda label, **kwargs: "システム再起動" in label
        with patch.object(log_tab, "st", mock_st), \
             patch.object(log_tab.subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, "systemctl")), \
             patch("services.backup_service.perform_backup", return_value=(True, "ok", 1.0)):
            log_tab.render_maintenance()

        mock_st.error.assert_called()

    def test_backup_success_reports_the_size(self):
        mock_st = _mock_st()
        mock_st.checkbox.return_value = False
        mock_st.button.side_effect = lambda label, **kwargs: "バックアップ" in label
        with patch.object(log_tab, "st", mock_st), \
             patch("services.backup_service.perform_backup", return_value=(True, "done", 12.34)):
            log_tab.render_maintenance()

        successes = [str(c.args[0]) for c in mock_st.success.call_args_list if c.args]
        assert any("12.3MB" in s for s in successes), successes

    def test_backup_failure_reports_the_reason(self):
        mock_st = _mock_st()
        mock_st.checkbox.return_value = False
        mock_st.button.side_effect = lambda label, **kwargs: "バックアップ" in label
        with patch.object(log_tab, "st", mock_st), \
             patch("services.backup_service.perform_backup", return_value=(False, "NASが見つかりません", 0.0)):
            log_tab.render_maintenance()

        errors = [str(c.args[0]) for c in mock_st.error.call_args_list if c.args]
        assert any("NASが見つかりません" in e for e in errors), errors
