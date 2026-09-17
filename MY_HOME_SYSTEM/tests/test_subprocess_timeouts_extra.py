# MY_HOME_SYSTEM/tests/test_subprocess_timeouts_extra.py
"""#651 の横展開: 後から見つかった timeout 未指定の subprocess.run 2箇所の回帰テスト。

並行セッションからの指摘(PR #666 のコメント)で、Issue が列挙していた9箇所のほかに
`views/dashboard/log_tab.py` のサービス再起動と `monitors/nas_monitor.check_ping` が
timeout 無しで残っていることが分かった。どちらも「応答しない相手を待ち続ける」形なので、
上限を置いたうえで TimeoutExpired を呼び出し元へ伝播させないことを固定する。
"""
import os
import subprocess
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import nas_monitor


class TestNasMonitorPingTimeout:
    def test_ping_passes_a_timeout_wider_than_the_ping_deadline(self, monkeypatch):
        monitor = nas_monitor.NasMonitor()
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return MagicMock(returncode=0)

        monkeypatch.setattr(nas_monitor.subprocess, "run", fake_run)
        assert monitor.check_ping() is True
        # -W(ping 自身の待ち)より外側の上限が広いこと
        assert captured["timeout"] > monitor.timeout

    def test_ping_timeout_is_treated_as_unreachable_not_an_exception(self, monkeypatch):
        monitor = nas_monitor.NasMonitor()

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

        monkeypatch.setattr(nas_monitor.subprocess, "run", fake_run)
        # 監視ループを落とさず「疎通しない」と返すこと
        assert monitor.check_ping() is False


class TestDashboardRestartTimeout:
    def test_restart_command_declares_a_timeout(self):
        """ダッシュボードの再起動ボタンが timeout 付きで systemctl を呼ぶこと。

        Streamlit のスクリプト実行スレッドで待ち続けると画面全体が固まるため。
        """
        from views.dashboard import log_tab

        with open(log_tab.__file__, encoding="utf-8") as f:
            source = f.read()
        restart_call = source[source.index('"restart", "home_system"'):]
        restart_call = restart_call[: restart_call.index(")")]
        assert "timeout=SUBPROCESS_TIMEOUT_SEC" in restart_call or "timeout=" in restart_call
        assert "except subprocess.TimeoutExpired" in source
