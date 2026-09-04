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
    def _fail_once(self, notifier, site, dm, failed_count=1, total_count=79):
        """1回分の失敗を、_run_monitor_locked と同じ流れで処理する。

        #395: アラートの即時送信は _handle_site_network_failure から
        _send_pending_site_failure_alerts(全サイト処理後にまとめて送信判断)へ
        移動したため、本ヘルパーで「記録→保留アラートの送信」までを再現する。
        既定では失敗サイトが 1/79 (自局側障害とはみなされない)として送信する。
        """
        pending = module._handle_site_network_failure(
            notifier, site, requests.RequestException("connection refused"), dm
        )
        if pending is not None:
            module._send_pending_site_failure_alerts(
                notifier, dm, [(site, pending)], failed_count, total_count
            )
        return pending

    def test_below_threshold_logs_error_and_does_not_alert(
        self, data_dir, dm, caplog, propagating_logger
    ):
        site = _make_site()
        notifier = MagicMock()

        with caplog.at_level(logging.WARNING, logger="newface_monitor"):
            pending = self._fail_once(notifier, site, dm)

        assert pending is None
        notifier.notify_site_failure_alert.assert_not_called()
        assert any(
            record.levelno == logging.ERROR and "site failure" in record.message
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

    def test_threshold_reached_is_demoted_to_warning_even_if_alert_send_fails(
        self, data_dir, dm, caplog, propagating_logger
    ):
        """#395: Webhook未設定/失効で送信が失敗し続けても、閾値以上の失敗ログは
        WARNINGに降格されること(以前はalertedが永久に立たず毎時ERROR→Discord
        発報が続いていた)。送信の再試行自体は別管理で継続される。"""
        site = _make_site()
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = False

        for _ in range(MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD - 1):
            self._fail_once(notifier, site, dm)

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="newface_monitor"):
            self._fail_once(notifier, site, dm)
            self._fail_once(notifier, site, dm)

        assert all(record.levelno < logging.ERROR for record in caplog.records)
        assert any("alert pending" in record.message for record in caplog.records)
        # 送信自体は毎回再試行されている
        assert notifier.notify_site_failure_alert.call_count == 2

    def test_count_and_alerted_are_persisted_in_the_same_file(self, data_dir, dm):
        """#395: record_site_failure と mark_site_failure_alerted が同じ解決済み
        ディレクトリ(#364で1回だけ束縛)の同一ファイルへ書き込むこと。"""
        site = _make_site()
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = True

        for _ in range(MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD):
            self._fail_once(notifier, site, dm)

        import json

        stored = json.loads((data_dir / "site_failures.json").read_text(encoding="utf-8"))
        assert stored[site.site_id] == {
            "count": MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD,
            "alerted": True,
        }


class TestSelfOutageSuppression:
    """#395: Pi側の回線断で全サイトが同時に閾値へ到達した場合、79件のアラートが
    一斉送信されないこと(失敗サイトの割合が SELF_OUTAGE_SUPPRESS_RATIO 超なら抑止)。"""

    def test_alerts_are_suppressed_when_most_sites_failed(self, data_dir, dm):
        sites = [_make_site(f"site_{i}") for i in range(4)]
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = True
        pending = [(site, MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD) for site in sites]

        module._send_pending_site_failure_alerts(notifier, dm, pending, failed_count=4, total_count=4)

        notifier.notify_site_failure_alert.assert_not_called()
        # alertedは立てず、回線復旧後の次回実行で改めて送信判断される
        assert dm.load_site_failures() == {}

    def test_alerts_are_sent_when_only_a_minority_failed(self, data_dir, dm):
        site = _make_site("site_0")
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = True

        module._send_pending_site_failure_alerts(
            notifier, dm, [(site, 30)], failed_count=2, total_count=79
        )

        notifier.notify_site_failure_alert.assert_called_once_with(site, 30)
        assert dm.load_site_failures()[site.site_id]["alerted"] is True

    def test_run_monitor_locked_suppresses_alerts_when_all_sites_fail(
        self, data_dir, dm, monkeypatch
    ):
        """_run_monitor_locked 経由で、全サイトが同時に閾値へ到達した実行では
        アラートが送信されないこと(統合確認)。"""
        sites = [_make_site(f"site_{i}") for i in range(3)]
        monkeypatch.setattr(MonitorConfig, "SITES", sites)
        for site in sites:
            for _ in range(MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD - 1):
                dm.record_site_failure(site.site_id)

        monitor = MagicMock()
        monitor.fetch_current_casts.side_effect = requests.RequestException("network is unreachable")
        monkeypatch.setattr(module, "WebMonitor", MagicMock(return_value=monitor))
        notifier = MagicMock()
        notifier.notify_site_failure_alert.return_value = True
        monkeypatch.setattr(module, "DiscordNotifier", MagicMock(return_value=notifier))
        monkeypatch.setattr(module, "get_managed_target_directory", MagicMock(return_value=data_dir))
        monkeypatch.setattr(module, "wait_for_storage_warmup", MagicMock(return_value=True))
        monkeypatch.setattr(module, "_maybe_send_daily_summary", MagicMock())

        module._run_monitor_locked()

        notifier.notify_site_failure_alert.assert_not_called()
        for site in sites:
            entry = dm.load_site_failures()[site.site_id]
            assert entry["count"] == MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD
            assert entry["alerted"] is False


class TestDisappearedSiteReturning200:
    """#395: 200を返す消失サイト(別ドメインへのリダイレクト・キャスト0件)も
    連続失敗として計上されること。"""

    def _monitor_with_response(self, monkeypatch, final_url, body=b"<html></html>"):
        monitor = module.WebMonitor()
        response = MagicMock()
        response.url = final_url
        response.content = body
        response.raise_for_status.return_value = None
        monitor.session.get = MagicMock(return_value=response)
        monkeypatch.setattr(module.time, "sleep", lambda *_: None)
        return monitor

    def test_redirect_to_other_domain_raises_site_unavailable(self, monkeypatch):
        site = _make_site()
        monitor = self._monitor_with_response(monkeypatch, "https://portal.hosting.test/parked")

        with pytest.raises(module.SiteUnavailableError):
            monitor.fetch_current_casts(site)

    def test_www_prefix_redirect_is_not_treated_as_relocation(self, monkeypatch):
        site = _make_site()
        monitor = self._monitor_with_response(
            monkeypatch, "https://www.example.test/news.php",
            body=b'<div><li>Alice</li><a href="/p/1"></a><img src="/1.jpg"></div>',
        )

        casts = monitor.fetch_current_casts(site)

        assert len(casts) == 1

    def test_check_site_records_failure_on_redirect(self, data_dir, dm):
        site = _make_site()
        monitor = MagicMock()
        monitor.fetch_current_casts.side_effect = module.SiteUnavailableError("redirected")

        result = module._check_site(monitor, MagicMock(), site, dm)

        assert result.failed is True
        assert dm.load_site_failures()[site.site_id]["count"] == 1

    def test_check_site_records_failure_on_empty_result_as_warning(
        self, data_dir, dm, caplog, propagating_logger
    ):
        """キャスト0件はレイアウト変更の可能性もあるため、閾値未満ではWARNINGで
        計上し(ERRORにしない)、連続失敗の記録は解消せず加算されること。"""
        site = _make_site()
        dm.record_site_failure(site.site_id)
        monitor = MagicMock()
        monitor.fetch_current_casts.return_value = set()

        with caplog.at_level(logging.WARNING, logger="newface_monitor"):
            result = module._check_site(monitor, MagicMock(), site, dm)

        assert result.failed is True
        assert dm.load_site_failures()[site.site_id]["count"] == 2
        assert all(record.levelno < logging.ERROR for record in caplog.records)


class TestMalformedSiteFailureEntries:
    """#395: {"site": 5} のような辞書でないエントリが混入しても、
    record_site_failure が AttributeError で毎時CRITICALにならないこと。"""

    def test_non_dict_entries_are_ignored(self, data_dir, dm):
        (data_dir / "site_failures.json").write_text(
            '{"site_a": 5, "site_b": {"count": 2, "alerted": false}}', encoding="utf-8"
        )

        assert dm.load_site_failures() == {"site_b": {"count": 2, "alerted": False}}
        assert dm.record_site_failure("site_a") == (1, False)


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
