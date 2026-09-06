# DDD/test_newface_monitor_concurrency.py
"""
Issue #458: newface_monitor.py が79サイトを単一プロセスで逐次処理しており、
サイト数に比例して実行時間が増大していた問題への対応(ThreadPoolExecutorに
よる並列化)の回帰テスト。

並列化に伴い、以下の共有状態が複数スレッドから同時にアクセスされるように
なったため、それぞれのスレッドセーフティを検証する。

- DataManagerが読み書きする全サイト共通ファイル(daily_summary.json/
  site_failures.json)のread-modify-write(DataManager._shared_file_lock)
- DiscordNotifierが保持するDiscordCircuitBreakerの状態(file_utils.py)
- _run_monitor_locked自体が並列実行でも全サイトを漏れなく処理すること

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_concurrency.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import requests

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402
from file_utils import DiscordCircuitBreaker  # noqa: E402

SiteConfig = module.SiteConfig
DataManager = module.DataManager
MonitorConfig = module.MonitorConfig

# CIのようなコア数が少ない環境でも競合を再現しやすいよう、実際の
# SITE_CHECK_MAX_WORKERSより多めのスレッド数で負荷をかける。
_CONCURRENCY = 20
_ITERATIONS_PER_THREAD = 25


def _make_site(site_id: str) -> "SiteConfig":
    return SiteConfig(
        site_id=site_id,
        name=f"Concurrency Test Site {site_id}",
        target_url="https://example.test/news.php",
        selector_container="div",
        selector_name="li",
        selector_link="a",
        selector_image="img",
    )


class TestDataManagerSharedFileConcurrency:
    def test_record_daily_new_casts_does_not_lose_updates_under_concurrency(self, tmp_path):
        """複数スレッドが同時にrecord_daily_new_castsを呼んでも、
        read-modify-writeの競合でカウントが失われないこと。"""
        dm = DataManager(tmp_path)

        def _worker():
            for _ in range(_ITERATIONS_PER_THREAD):
                dm.record_daily_new_casts("site_a", 1)

        with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
            futures = [executor.submit(_worker) for _ in range(_CONCURRENCY)]
            for f in futures:
                f.result()

        summary = dm.load_daily_summary()
        assert summary["counts"]["site_a"] == _CONCURRENCY * _ITERATIONS_PER_THREAD

    def test_record_site_failure_across_many_sites_does_not_lose_updates(self, tmp_path):
        """異なるsite_idへの同時書き込みでも、他サイトのエントリを
        上書き消失させないこと(共有ファイルへの直列化が機能していること)。"""
        dm = DataManager(tmp_path)
        site_ids = [f"site_{i}" for i in range(_CONCURRENCY)]

        def _worker(site_id):
            for _ in range(_ITERATIONS_PER_THREAD):
                dm.record_site_failure(site_id)

        with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
            futures = [executor.submit(_worker, site_id) for site_id in site_ids]
            for f in futures:
                f.result()

        failures = dm.load_site_failures()
        assert set(failures.keys()) == set(site_ids)
        for site_id in site_ids:
            assert failures[site_id]["count"] == _ITERATIONS_PER_THREAD


class TestDiscordCircuitBreakerConcurrency:
    def test_record_failure_count_is_exact_under_concurrent_calls(self):
        """複数スレッドが同時にrecord_failure()を呼んでも、内部カウンタの
        インクリメントが失われないこと(#458でnotify()が並列に呼ばれうるようになった)。"""
        breaker = DiscordCircuitBreaker(failure_threshold=10_000)  # 閾値到達で自動リセットしないよう高く設定

        def _worker():
            for _ in range(_ITERATIONS_PER_THREAD):
                breaker.record_failure()

        with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
            futures = [executor.submit(_worker) for _ in range(_CONCURRENCY)]
            for f in futures:
                f.result()

        assert breaker._consecutive_failures == _CONCURRENCY * _ITERATIONS_PER_THREAD

    def test_trips_open_exactly_once_threshold_reached_under_concurrency(self):
        breaker = DiscordCircuitBreaker(failure_threshold=50)
        barrier = threading.Barrier(_CONCURRENCY)

        def _worker():
            barrier.wait()
            for _ in range(_ITERATIONS_PER_THREAD):
                breaker.record_failure()

        with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
            futures = [executor.submit(_worker) for _ in range(_CONCURRENCY)]
            for f in futures:
                f.result()

        assert breaker.is_open is True


class TestRunMonitorLockedParallelExecution:
    def test_all_sites_are_processed_exactly_once_when_parallelized(self, tmp_path, monkeypatch):
        """ThreadPoolExecutorによる並列実行でも、全サイトが漏れなく1回ずつ
        _check_siteに渡されること(#458の統合確認)。"""
        sites = [_make_site(f"site_{i}") for i in range(30)]
        monkeypatch.setattr(MonitorConfig, "SITES", sites)
        monkeypatch.setattr(MonitorConfig, "SITE_CHECK_MAX_WORKERS", 8)

        monitor = MagicMock()
        monitor.fetch_current_casts.side_effect = requests.RequestException("network is unreachable")
        monkeypatch.setattr(module, "WebMonitor", MagicMock(return_value=monitor))
        notifier = MagicMock()
        monkeypatch.setattr(module, "DiscordNotifier", MagicMock(return_value=notifier))
        monkeypatch.setattr(module, "get_managed_target_directory", MagicMock(return_value=tmp_path))
        monkeypatch.setattr(module, "wait_for_storage_warmup", MagicMock(return_value=True))
        monkeypatch.setattr(module, "_maybe_send_daily_summary", MagicMock())

        module._run_monitor_locked()

        processed_site_ids = {
            call.args[0].site_id for call in monitor.fetch_current_casts.call_args_list
        }
        assert processed_site_ids == {site.site_id for site in sites}
        assert monitor.fetch_current_casts.call_count == len(sites)
