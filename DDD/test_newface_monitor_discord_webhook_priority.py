# DDD/test_newface_monitor_discord_webhook_priority.py
"""
Issue #586の回帰テスト。

MY_HOME_SYSTEM/config.pyは`DISCORD_WEBHOOK_NOTIFY`を優先し、未設定時のみ
レガシーの`DISCORD_WEBHOOK_URL`にフォールバックする
(`DISCORD_WEBHOOK_NOTIFY or os.getenv("DISCORD_WEBHOOK_URL")`)。
以前はnewface_monitor.pyが`DISCORD_WEBHOOK_URL`のみを参照しており、
実機で`DISCORD_WEBHOOK_NOTIFY`のみが設定されている運用(config.py側の
優先順位に合わせた設定)では、本ファイルの通知だけが「未設定」として
扱われ、Discord通知が一切送信されなくなっていた。

`MonitorConfig.DISCORD_WEBHOOK_URL`はモジュールimport時に1度だけ評価される
クラス属性のため、環境変数を変えて直接テストすることができない
(importlib.reloadはモジュールを共有する他のテストファイルの状態を壊すため
使わない)。解決ロジックを切り出した`_resolve_discord_webhook_url()`関数を
直接呼び出すことで、モジュール全体のreload無しに単体テストする。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_discord_webhook_priority.py` のように
直接指定して実行する(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sys
from pathlib import Path

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402


class TestResolveDiscordWebhookUrlPriority:
    def test_notify_env_var_takes_priority_when_both_are_set(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_NOTIFY", "https://discord.test/notify")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/legacy-url")

        assert module._resolve_discord_webhook_url() == "https://discord.test/notify"

    def test_legacy_url_is_used_as_fallback_when_notify_is_unset(self, monkeypatch):
        monkeypatch.delenv("DISCORD_WEBHOOK_NOTIFY", raising=False)
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/legacy-url")

        assert module._resolve_discord_webhook_url() == "https://discord.test/legacy-url"

    def test_none_when_neither_is_set(self, monkeypatch):
        monkeypatch.delenv("DISCORD_WEBHOOK_NOTIFY", raising=False)
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

        assert module._resolve_discord_webhook_url() is None


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
