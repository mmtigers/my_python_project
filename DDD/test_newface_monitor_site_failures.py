# DDD/test_newface_monitor_site_failures.py
"""
bellica閉鎖(2026-09-02)を契機とした「恒久的に消失したサイトによる毎時ERROR
発報」の再発防止機構のテスト。

bellicaはサイト閉鎖に伴いドメインがホスティング業者のデフォルト自己署名証明書
+ポータルサイトへの302リダイレクトに変わり、newface_monitorが毎時2件のERROR
ログを出し続けて一次ヘルスチェック(health_watch)が発報し続けた。本テストは、

    1. bellicaが監視対象(MonitorConfig.SITES)から削除されていること
    2. DataManagerによるサイト別連続失敗状態(site_failures.json)の記録・
       解消のライフサイクル
    3. 連続失敗が閾値(CONSECUTIVE_FAILURE_ALERT_THRESHOLD)に達したサイトに
       ついて、Discordへ閉鎖疑いアラートが1回だけ送信されること
    4. アラート送信済みサイトの以降の失敗ログがERRORではなくWARNINGに
       降格されること(ヘルスチェックの再発報抑止)
    5. アラート送信に失敗した場合は送信済み扱いにならず、次回実行で
       再試行されること
    6. 疎通成功時に連続失敗状態がリセットされ、通常のERROR運用へ戻ること

を検証する。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_site_failures.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402

SiteConfig = module.SiteConfig
DataManager = module.DataManager
MonitorConfig = module.MonitorConfig


def _make_site(site_id: str = "failure_test") -> "SiteConfig":
    return SiteConfig(
        site_id=site_id,
        name="Failure Test Site",
        target_url="https://example.test/news.php",
        selector_container="div",
        selector_name="li",
        selector_link="a",
        selector_image="img",
    )


@pytest.fixture
def data_dir(tmp_path):
    """site_failures.jsonの保存先となるテスト用一時ディレクトリ。"""
    return tmp_path


@pytest.fixture
def dm(data_dir):
    """#364: 解決済みdata_dirを束縛したDataManagerインスタンス(以前は静的メソッド群を
    MonitorConfig.get_data_dir のmonkeypatch経由で使っていた)。"""
    return DataManager(data_dir)


@pytest.fixture
def propagating_logger():
    """caplogがnewface_monitorロガーの出力を捕捉できるようにする。

    Issue #122と同じ事情: モノレポ環境ではcore.loggerのpropagate=False設定の
    ロガーが使われ、rootロガーへ伝播しないためcaplogに記録されない。テスト中
    だけpropagate=Trueへ切り替える。
    """
    original = module.logger.propagate
    module.logger.propagate = True
    yield
    module.logger.propagate = original


class TestBellicaRemoved:
    def test_bellica_is_no_longer_monitored(self):
        """閉鎖済みのbellicaが監視対象に残っていないこと。"""
        assert all(site.site_id != "bellica" for site in MonitorConfig.SITES)


class TestSiteFailureLifecycle:
    def test_record_increments_consecutive_count(self, data_dir, dm):
        count1, alerted1 = dm.record_site_failure("site_a")
        count2, alerted2 = dm.record_site_failure("site_a")

        assert (count1, alerted1) == (1, False)
        assert (count2, alerted2) == (2, False)

    def test_counts_are_tracked_per_site(self, data_dir, dm):
        dm.record_site_failure("site_a")
        count_b, _ = dm.record_site_failure("site_b")

        assert count_b == 1

    def test_mark_alerted_is_returned_by_subsequent_record(self, data_dir, dm):
        dm.record_site_failure("site_a")
        dm.mark_site_failure_alerted("site_a")

        _, alerted = dm.record_site_failure("site_a")

        assert alerted is True

    def test_clear_resets_count_and_alerted_flag(self, data_dir, dm):
        dm.record_site_failure("site_a")
        dm.mark_site_failure_alerted("site_a")

        dm.clear_site_failure("site_a")
        count, alerted = dm.record_site_failure("site_a")

        assert (count, alerted) == (1, False)

    def test_clear_without_existing_record_does_not_create_file(self, data_dir, dm):
        """正常巡回のたびに全サイト分のNAS書き込みが発生しないこと。"""
        dm.clear_site_failure("never_failed_site")

        assert not (data_dir / "site_failures.json").exists()

    def test_corrupted_failures_file_is_treated_as_empty_state(self, data_dir, dm):
        failures_file = data_dir / "site_failures.json"
        failures_file.write_bytes(b'{"site_a": \xf9broken')

        assert dm.load_site_failures() == {}


class TestHandleSiteNetworkFailure:
    def _fail_once(self, notifier, site, dm):
        module._handle_site_network_failure(
            notifier, site, requests.RequestException("connection refused"), dm
        )

    def test_below_threshold_logs_error_and_does_not_alert(
        self, data_dir, dm, caplog, propagating_logger
    ):
        site = _make_site()
        notifier = MagicMock()

        with caplog.at_level(logging.WARNING, logger="newface_monitor"):
            self._fail_once(notifier, site, dm)

        notifier.notify_site_failure_alert.assert_not_called()
        assert any(
            record.levelno == logging.ERROR and "network failure" in record.message
            for record in caplog.records
        )

    def test_alert_sent_once_when_threshold_reached(self, data_dir, dm):
        site = _make_site()
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = True

        for _ in range(MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD + 5):
            self._fail_once(notifier, site, dm)

        notifier.notify_site_failure_alert.assert_called_once_with(
            site, MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD
        )

    def test_failures_after_alert_are_logged_as_warning_not_error(
        self, data_dir, dm, caplog, propagating_logger
    ):
        """アラート送信済みサイトの失敗ログはWARNINGに降格され、
        一次ヘルスチェック(ERROR監視)を発報させ続けないこと。"""
        site = _make_site()
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = True

        for _ in range(MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD):
            self._fail_once(notifier, site, dm)

        # 準備段階(閾値到達前)のERRORログを検証対象から除外する
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="newface_monitor"):
            self._fail_once(notifier, site, dm)

        assert all(record.levelno < logging.ERROR for record in caplog.records)
        assert any("closure alert already sent" in record.message for record in caplog.records)

    def test_threshold_failure_itself_is_demoted_when_alert_succeeds(
        self, data_dir, dm, caplog, propagating_logger
    ):
        """閾値到達時の失敗(アラートを送った当回)もWARNING扱いになること。"""
        site = _make_site()
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = True

        for _ in range(MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD - 1):
            self._fail_once(notifier, site, dm)

        # 準備段階(閾値到達前)のERRORログを検証対象から除外する
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="newface_monitor"):
            self._fail_once(notifier, site, dm)

        assert all(record.levelno < logging.ERROR for record in caplog.records)

    def test_failed_alert_send_is_retried_on_next_failure(self, data_dir, dm):
        """Discord送信失敗時はalertedを立てず、次回の失敗時に再試行すること。"""
        site = _make_site()
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = False

        for _ in range(MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD + 2):
            self._fail_once(notifier, site, dm)

        assert notifier.notify_site_failure_alert.call_count == 3


class TestCheckSiteIntegration:
    def test_network_failure_records_and_success_clears(self, data_dir, dm, monkeypatch):
        """_check_site経由で失敗記録→疎通回復でリセットの一連が動くこと。"""
        site = _make_site()
        notifier = MagicMock()

        monitor = MagicMock()
        monitor.fetch_current_casts.side_effect = requests.RequestException("boom")
        module._check_site(monitor, notifier, site, dm)

        assert dm.load_site_failures()[site.site_id]["count"] == 1

        cast = module.CastMember(
            id="c1", name="テスト", detail_url="https://example.test/c1", image_url=""
        )
        monitor.fetch_current_casts = MagicMock(return_value={cast})
        monkeypatch.setattr(module.DataManager, "load_known_casts", MagicMock(return_value={cast}))
        monkeypatch.setattr(module.DataManager, "save_known_casts", MagicMock())
        module._check_site(monitor, notifier, site, dm)

        assert dm.load_site_failures() == {}


class TestNotifySiteFailureAlert:
    def test_returns_true_on_success_and_sends_expected_content(self):
        notifier = module.DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")
        response = MagicMock()
        response.raise_for_status.return_value = None
        notifier.session.post = MagicMock(return_value=response)

        site = _make_site()
        result = notifier.notify_site_failure_alert(site, 24)

        assert result is True
        payload = notifier.session.post.call_args.kwargs["json"]
        assert site.site_id in payload["content"]
        assert "24回連続" in payload["content"]

    def test_returns_false_on_request_exception(self):
        notifier = module.DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")
        notifier.session.post = MagicMock(side_effect=requests.RequestException("boom"))

        result = notifier.notify_site_failure_alert(_make_site(), 24)

        assert result is False

    def test_returns_false_when_webhook_not_configured(self):
        notifier = module.DiscordNotifier(webhook_url="")

        result = notifier.notify_site_failure_alert(_make_site(), 24)

        assert result is False
