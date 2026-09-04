# DDD/test_newface_monitor_data_dir.py
"""
Issue #364 (D-H1) の回帰テスト。

以前は DataManager の全メソッドが呼び出しのたびに MonitorConfig.get_data_dir()
(= core.nas_utils.get_managed_target_directory) を再評価していたため、79サイト
×(load/clear/save 最低3回)で1実行あたり240回以上呼ばれていた。NAS未マウント時、
get_managed_target_directory は呼び出しごとに sudo mount サブプロセスと
Discord ERROR 投稿(+ send_push)を行うため、毎時数百回の sudo mount と数百件の
Discord 投稿が発生していた。さらにローカルフォールバック先には known_casts_*.json
が無いため、全サイトの全在籍キャストが「新規」として再通知されていた。

本テストは、
    1. _run_monitor_locked が get_managed_target_directory を1回だけ呼び、
       全サイトの読み書きが同じ解決済みディレクトリに対して行われること
    2. 解決結果がローカルフォールバック先だった場合、サイト処理・通知・
       ストレージウォームアップのいずれにも進まず実行全体を中断すること
    3. DataManager がインスタンス生成後にNAS状態を一切再評価しないこと
を検証する。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_data_dir.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402

SiteConfig = module.SiteConfig
CastMember = module.CastMember
DataManager = module.DataManager
MonitorConfig = module.MonitorConfig


def _make_site(site_id: str) -> "SiteConfig":
    return SiteConfig(
        site_id=site_id,
        name=f"Site {site_id}",
        target_url=f"https://{site_id}.example.test/news.php",
        selector_container="div",
        selector_name="li",
        selector_link="a",
        selector_image="img",
    )


@pytest.fixture
def propagating_logger():
    """caplogがnewface_monitorロガーの出力を捕捉できるようにする(Issue #122参照)。"""
    original = module.logger.propagate
    module.logger.propagate = True
    yield
    module.logger.propagate = original


@pytest.fixture
def stub_run_dependencies(monkeypatch):
    """_run_monitor_locked の外部依存(HTTP・Discord・ストレージ確認)をスタブ化する。

    戻り値の辞書には、各サイトの巡回結果として返すキャストと、差し替えた
    WebMonitor/DiscordNotifier のモックを格納する。
    """
    sites = [_make_site("site_a"), _make_site("site_b"), _make_site("site_c")]
    monkeypatch.setattr(MonitorConfig, "SITES", sites)

    cast = CastMember(id="c1", name="テスト", detail_url="https://example.test/c1", image_url="")
    monitor = MagicMock()
    monitor.fetch_current_casts.return_value = {cast}
    monkeypatch.setattr(module, "WebMonitor", MagicMock(return_value=monitor))

    notifier = MagicMock()
    # D-L9: notify()はint(実際に送信できた件数)を返す契約になったため、
    # 呼び出し元(record_daily_new_casts)が比較演算できるようMagicMockの
    # 戻り値を明示的にintへ固定する。
    notifier.notify.return_value = 1
    monkeypatch.setattr(module, "DiscordNotifier", MagicMock(return_value=notifier))

    warmup = MagicMock(return_value=True)
    monkeypatch.setattr(module, "wait_for_storage_warmup", warmup)
    # 日次サマリ送信は時刻依存のため対象外とする
    monkeypatch.setattr(module, "_maybe_send_daily_summary", MagicMock())

    return {"sites": sites, "monitor": monitor, "notifier": notifier, "warmup": warmup}


class TestDataDirResolvedOncePerRun:
    def test_get_managed_target_directory_is_called_exactly_once(
        self, tmp_path, monkeypatch, stub_run_dependencies
    ):
        """79サイト分の load/clear/save があっても NAS 解決は1回だけであること。"""
        resolver = MagicMock(return_value=tmp_path)
        monkeypatch.setattr(module, "get_managed_target_directory", resolver)

        module._run_monitor_locked()

        assert resolver.call_count == 1
        # 全サイトの巡回が実際に行われ、読み書きが同じ解決済みディレクトリに対して
        # 行われていること(= DataManager が別のディレクトリを再解決していないこと)
        monitor = stub_run_dependencies["monitor"]
        assert monitor.fetch_current_casts.call_count == len(stub_run_dependencies["sites"])
        for site in stub_run_dependencies["sites"]:
            assert (tmp_path / site.get_data_filename()).exists()

    def test_data_manager_never_re_resolves_nas_state(self, tmp_path, monkeypatch):
        """DataManager はコンストラクタで受け取った data_dir だけを使い、
        メソッド呼び出し中に get_managed_target_directory を再評価しないこと。"""

        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("get_managed_target_directory が再評価された")

        monkeypatch.setattr(module, "get_managed_target_directory", _must_not_be_called)
        site = _make_site("site_a")
        dm = DataManager(tmp_path)
        cast = CastMember(id="c1", name="テスト", detail_url="https://example.test/c1", image_url="")

        dm.save_known_casts(site, {cast})
        assert dm.load_known_casts(site) == {cast}
        dm.record_site_failure(site.site_id)
        dm.mark_site_failure_alerted(site.site_id)
        dm.clear_site_failure(site.site_id)
        dm.record_daily_new_casts(site.site_id, 1)
        assert dm.load_daily_summary()["counts"] == {site.site_id: 1}


class TestLocalFallbackAbortsRun:
    def test_fallback_dir_aborts_before_any_site_is_processed_or_notified(
        self, monkeypatch, stub_run_dependencies, caplog, propagating_logger
    ):
        """解決結果がローカルフォールバック先なら、サイト巡回・Discord通知・
        ウォームアップ確認のいずれにも進まず、ERRORログを1回出して中断すること。"""
        fallback_dir = Path(MonitorConfig.LOCAL_DIR_STR)
        monkeypatch.setattr(module, "get_managed_target_directory", MagicMock(return_value=fallback_dir))
        check_site = MagicMock()
        monkeypatch.setattr(module, "_check_site", check_site)

        with caplog.at_level(logging.ERROR, logger="newface_monitor"):
            module._run_monitor_locked()

        check_site.assert_not_called()
        module.WebMonitor.assert_not_called()
        module.DiscordNotifier.assert_not_called()
        stub_run_dependencies["notifier"].notify.assert_not_called()
        stub_run_dependencies["warmup"].assert_not_called()
        assert any(
            record.levelno == logging.ERROR and "ローカルフォールバック中" in record.message
            for record in caplog.records
        )

    def test_nas_dir_proceeds_normally(self, tmp_path, monkeypatch, stub_run_dependencies):
        """フォールバック先でなければ従来どおり全サイトを処理すること(非破壊確認)。"""
        monkeypatch.setattr(module, "get_managed_target_directory", MagicMock(return_value=tmp_path))
        check_site = MagicMock()
        monkeypatch.setattr(module, "_check_site", check_site)

        module._run_monitor_locked()

        assert check_site.call_count == len(stub_run_dependencies["sites"])
        stub_run_dependencies["warmup"].assert_called_once_with(tmp_path)


class TestIsLocalFallbackDir:
    def test_detects_local_dir_regardless_of_path_spelling(self):
        local = Path(MonitorConfig.LOCAL_DIR_STR)
        assert MonitorConfig.is_local_fallback_dir(local) is True
        # 表記揺れ(末尾スラッシュ・相対要素)があっても正規化して検知する
        assert MonitorConfig.is_local_fallback_dir(local / "." ) is True

    def test_nas_dir_is_not_fallback(self, tmp_path):
        assert MonitorConfig.is_local_fallback_dir(tmp_path) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
