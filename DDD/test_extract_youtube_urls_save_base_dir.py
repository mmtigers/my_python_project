# DDD/test_extract_youtube_urls_save_base_dir.py
"""
Issue #243の回帰テスト。

FileManager.save()は以前、呼び出しのたびに常にAppConfig.get_output_base_dir()を
呼んでいた。get_output_base_dir()はNASマウント確認・自己修復・障害通知を伴う重い
処理であり、process_subscriptions()自身はこれを巡回開始時に1回だけ呼ぶよう配慮
されていたにもかかわらず、save()内部での再呼び出しにより、1チャンネル/1URLから
複数のExtractionResultが得られる場合に保存件数分だけ再評価されていた。NAS未
マウント時のsudo mount再試行やDiscord/LINE通知が結果件数分だけ重複発生しうる。

本テストは、
    1. FileManager.save()にbase_dirを渡した場合、get_output_base_dir()が
       呼ばれないこと
    2. base_dirを渡さない場合は従来通りget_output_base_dir()が呼ばれること
       (後方互換の確認)
    3. process_subscriptions()が、1URLから複数のExtractionResultが得られても
       get_output_base_dir()を1回しか呼ばないこと
    4. UrlExtractorApp.run()(対話/直接URL実行)も同様に1回しか呼ばないこと
を検証する。

同じディレクトリの test_extract_youtube_urls_paths.py は importlib.reload() で
extract_youtube_urls モジュールを再ロードするテストを含むため、本ファイルでも
モジュール属性(AppConfig等)への動的参照(module.AppConfig等)を用いる。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_extract_youtube_urls_save_base_dir.py` のように直接指定して
実行する(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import extract_youtube_urls as module  # noqa: E402

CHANNEL_URL = "https://www.youtube.com/@test_channel"


def _make_result(title="video"):
    return module.ExtractionResult(
        title=title, urls=["https://www.youtube.com/watch?v=1"], source_url=CHANNEL_URL
    )


class TestFileManagerSaveAcceptsBaseDir:
    def test_uses_provided_base_dir_without_calling_get_output_base_dir(self, tmp_path):
        manager = module.FileManager()
        result = _make_result()

        with patch.object(module.AppConfig, "get_output_base_dir") as mock_get_base:
            saved = manager.save(result, base_dir=tmp_path)

        assert saved is True
        mock_get_base.assert_not_called()
        assert (tmp_path / module.AppConfig.SUB_DIR_NAME / "video.txt").exists()

    def test_falls_back_to_get_output_base_dir_when_omitted(self, tmp_path):
        """後方互換の確認: base_dirを渡さない場合は従来通り呼ばれること。"""
        manager = module.FileManager()
        result = _make_result()

        with patch.object(module.AppConfig, "get_output_base_dir", return_value=tmp_path) as mock_get_base:
            saved = manager.save(result)

        assert saved is True
        mock_get_base.assert_called_once()


class TestProcessSubscriptionsCallsGetOutputBaseDirOnce:
    def _make_db_with_urls(self, tmp_path, urls):
        db_path = tmp_path / "home_system.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE youtube_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_url TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            for url in urls:
                conn.execute(
                    "INSERT INTO youtube_subscriptions (channel_url, is_active) VALUES (?, 1)", (url,)
                )
            conn.commit()
        return db_path

    def test_get_output_base_dir_called_once_for_multiple_results_from_one_url(self, tmp_path):
        self._make_db_with_urls(tmp_path, [CHANNEL_URL])

        extractor = MagicMock()
        # 1つのURLから複数のExtractionResultがyieldされる状況を模す
        # (例: /videosタブと複数プレイリストからそれぞれ結果が得られるケース)
        extractor.extract_iter.return_value = iter([_make_result("v1"), _make_result("v2"), _make_result("v3")])
        extractor.last_extract_internal_failures = 0
        file_manager = module.FileManager()

        manager = module.SubscriptionManager(extractor, file_manager)

        with patch.object(
            module.AppConfig, "get_output_base_dir", return_value=tmp_path / "data"
        ) as mock_get_base, patch("extract_youtube_urls.time.sleep"):
            manager.process_subscriptions()

        assert mock_get_base.call_count == 1, (
            "1URLから複数の結果が得られても、get_output_base_dir()はsave()内で"
            "再評価されず1回だけ呼ばれるべき"
        )


class TestUrlExtractorAppRunCallsGetOutputBaseDirOnce:
    def test_direct_url_mode_calls_get_output_base_dir_once_for_multiple_results(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["extract_youtube_urls.py", CHANNEL_URL])

        app = module.UrlExtractorApp()
        monkeypatch.setattr(
            app.extractor, "extract_iter",
            lambda url: iter([_make_result("v1"), _make_result("v2")])
        )

        with patch.object(
            module.AppConfig, "get_output_base_dir", return_value=tmp_path
        ) as mock_get_base:
            app.run()

        assert mock_get_base.call_count == 1, (
            "対話/直接URL実行でも、複数結果に対してget_output_base_dir()を"
            "1回だけ呼ぶべき"
        )


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
