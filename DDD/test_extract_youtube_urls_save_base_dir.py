# DDD/test_extract_youtube_urls_save_base_dir.py
"""
Issue #243の回帰テスト。

FileManager.save()は以前、呼び出しのたびに常にAppConfig.get_output_base_dir()を
呼んでいた。get_output_base_dir()はNASマウント確認・自己修復・障害通知を伴う重い
処理であり、1チャンネル/1URLから複数のExtractionResultが得られる場合に
保存件数分だけ再評価されていた。NAS未マウント時のsudo mount再試行やDiscord/LINE
通知が結果件数分だけ重複発生しうる。

本テストは、
    1. FileManager.save()にbase_dirを渡した場合、get_output_base_dir()が
       呼ばれないこと
    2. base_dirを渡さない場合は従来通りget_output_base_dir()が呼ばれること
       (後方互換の確認)
    3. UrlExtractorApp.run()(対話/直接URL実行)が、1URLから複数のExtractionResultが
       得られても get_output_base_dir()を1回しか呼ばないこと
を検証する。

（#413 D-L11で削除）以前ここには、削除された SubscriptionManager
(process_subscriptions)についても同種の呼び出し回数を検証する項目があったが、
SubscriptionManager自体の削除に伴い該当テストクラスも削除した。

同じディレクトリの test_extract_youtube_urls_paths.py は importlib.reload() で
extract_youtube_urls モジュールを再ロードするテストを含むため、本ファイルでも
モジュール属性(AppConfig等)への動的参照(module.AppConfig等)を用いる。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_extract_youtube_urls_save_base_dir.py` のように直接指定して
実行する(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sys
from pathlib import Path
from unittest.mock import patch

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


# #413 (D-L11): 以前ここにあった TestProcessSubscriptionsCallsGetOutputBaseDirOnce
# は、削除された SubscriptionManager(定期巡回/サブスクリプション機能。事実上の
# デッド機能だったためオーナー判断で撤去)専用のテストだったため削除した。
# process_subscriptions()向けのget_output_base_dir()呼び出し回数検証は不要になった。


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
