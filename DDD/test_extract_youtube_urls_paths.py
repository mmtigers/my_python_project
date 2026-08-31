# DDD/test_extract_youtube_urls_paths.py
"""
H-12: extract_youtube_urls.py のPROJECT_ROOT解決・NASフォールバック挙動の回帰テスト。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_extract_youtube_urls_paths.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。

以前は PROJECT_ROOT = CURRENT_DIR.parent (develop/) を core/ の実位置だと
誤認しており、実リポジトリ配置では `from core.logger import get_logger` が
ImportErrorになっていた。その結果、フォールバック用のローカルスタブ
get_managed_target_directory が使われるが、これが引数を無視してCWD相対の
"./data" を返すバグを持っていたため、実行ディレクトリ次第でDB・データの
保存先が毎回変わる不具合があった(newface_monitor.pyでは既に修正済みの
同一バグ)。
"""
import importlib
import logging
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import extract_youtube_urls as module  # noqa: E402


def test_project_root_points_to_my_home_system_not_repo_root():
    """H-12: PROJECT_ROOTはDDDの単なる親(repoルート)ではなく、
    core/ が実在する develop/MY_HOME_SYSTEM を指すこと。"""
    assert module.PROJECT_ROOT == module.CURRENT_DIR.parent / "MY_HOME_SYSTEM"
    assert module.PROJECT_ROOT.name == "MY_HOME_SYSTEM"


def test_core_module_is_importable_with_fixed_project_root():
    """修正後のPROJECT_ROOTでは実際にcore.loggerがimportでき、
    ローカルフォールバックスタブに落ちないこと。"""
    assert str(module.PROJECT_ROOT) in sys.path
    import core.logger  # noqa: F401  (ImportErrorにならないこと自体が検証)


class TestFileManagerSaveFilenameByteLength:
    """Issue #175の回帰テスト: FileManager.saveは
    "{safe_channel}_{safe_title}.txt" という形式でファイル名を組み立てるが、
    以前は各コンポーネントがそれぞれ既定のmax_length(200文字)で切り詰められる
    だけだったため、日本語のチャンネル名・タイトルでは(3バイト/文字として)
    最大200*3*2+5=1205バイトとなり、ext4等の255バイト上限を大幅に超過して
    ENAMETOOLONGでファイル保存が失敗しうる不具合があった。"""

    def test_long_japanese_channel_and_title_still_saves_successfully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(module.AppConfig, "get_output_base_dir", staticmethod(lambda: tmp_path))

        result = module.ExtractionResult(
            title="あ" * 200,
            urls=["https://example.test/1"],
            source_url="https://example.test/list",
            channel_name="い" * 200,
            is_playlist=True,
        )

        manager = module.FileManager()
        assert manager.save(result) is True

        saved_files = list((tmp_path / module.AppConfig.SUB_DIR_NAME).glob("*.txt"))
        assert len(saved_files) == 1
        # ファイル名(拡張子込み)自体がファイルシステムのNAME_MAX(255バイト)に
        # 収まっていること。
        assert len(saved_files[0].name.encode("utf-8")) <= 255


class TestFallbackStubRespectsExplicitPath:
    """
    core.* のimportが何らかの理由で失敗した場合に使われるローカルスタブ
    get_managed_target_directory() の挙動そのものを、実際にImportErrorを
    発生させてモジュールを再読込することで再現・検証する。
    """

    @contextmanager
    def _reloaded_with_core_unavailable(self):
        """
        core.logger/core.nas_utilsのimportだけをImportErrorにしてモジュールを
        再読込する。sys.pathからPROJECT_ROOTを除去するだけでは、モジュール自身の
        トップレベルコードが `if str(PROJECT_ROOT) not in sys.path: sys.path.append(...)`
        で毎回再追加してしまい、exceptブロックに到達できないため、
        importそのものをブロックする。
        """
        import builtins

        real_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name == "core" or name.startswith("core."):
                raise ImportError(f"blocked for test: {name}")
            return real_import(name, *args, **kwargs)

        removed_modules = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "core" or name.startswith("core.")
        }
        try:
            with patch("builtins.__import__", side_effect=_blocking_import):
                yield importlib.reload(module)
        finally:
            sys.modules.update(removed_modules)
            importlib.reload(module)  # core利用可能な元の状態に戻す

    def test_stub_returns_the_passed_fallback_dir_str_not_cwd_relative(self):
        with self._reloaded_with_core_unavailable() as reloaded:
            result = reloaded.get_managed_target_directory(
                nas_dir_str="/mnt/nas/x", fallback_dir_str="/home/user/develop/DDD/data", mount_point="/mnt/nas"
            )
        assert result == Path("/home/user/develop/DDD/data")

    def test_stub_falls_back_to_relative_data_only_when_no_kwarg_given(self):
        with self._reloaded_with_core_unavailable() as reloaded:
            result = reloaded.get_managed_target_directory()
        assert result == Path("./data")


class TestVerifyEnvironmentDetectsFallback:
    def test_detects_fallback_when_base_dir_matches_local_dir_exactly(self):
        with patch.object(module.AppConfig, "get_output_base_dir", return_value=Path(module.AppConfig.LOCAL_DIR_STR)):
            manager = module.SubscriptionManager.__new__(module.SubscriptionManager)
            assert manager._verify_environment() is False

    def test_does_not_flag_fallback_when_base_dir_is_the_real_nas_path(self):
        with patch.object(
            module.AppConfig, "get_output_base_dir", return_value=Path(module.AppConfig.NAS_DIR_STR)
        ):
            manager = module.SubscriptionManager.__new__(module.SubscriptionManager)
            assert manager._verify_environment() is True

    def test_detects_fallback_even_with_non_normalized_path_representation(self):
        """H-12回帰防止: 旧実装(部分文字列 in チェック)は、フォールバック関数が
        バグって短い相対パス './data' を返した場合にフォールバック状態を
        検知できなかった。パス正規化した比較であれば、表記が違っても
        同一パスとして検知できること。"""
        messy_path = Path(module.AppConfig.LOCAL_DIR_STR + "/../" + Path(module.AppConfig.LOCAL_DIR_STR).name)
        with patch.object(module.AppConfig, "get_output_base_dir", return_value=messy_path):
            manager = module.SubscriptionManager.__new__(module.SubscriptionManager)
            assert manager._verify_environment() is False


class TestProcessSubscriptionsUsesFreshDbPath:
    """
    Issue #123回帰テスト: アプリ起動(SubscriptionManager構築)時点ではNASが
    フォールバック中でも、process_subscriptions()の実行時までにNASが復帰して
    いれば(autofsの再マウント遅延はこのリポジトリで既知の事象)、NAS側の
    home_system.dbを正しく参照してサブスクリプションを処理できること。

    修正前は db_path が __init__ 時点のNAS状態(フォールバック中)で固定され、
    process_subscriptions() 側の環境検証だけが最新状態(復帰済み)を見るため、
    検証は通過するのにDBはローカルの空DBを新規作成してしまい、NAS側に登録
    済みのサブスクリプションが1件も読み込まれず「無言のno-op」になっていた。
    """

    def test_recovers_and_reads_nas_db_when_nas_comes_back_before_processing(self, tmp_path):
        nas_base = tmp_path / "nas" / "youtube_extractor" / "data"
        local_base = tmp_path / "local" / "data"
        nas_base.mkdir(parents=True)
        local_base.mkdir(parents=True)

        nas_db_path = nas_base.parent / "home_system.db"
        # NAS側DBに事前にアクティブなサブスクリプションを1件登録しておく
        # (プロセス起動より前の巡回で既に登録済み、という状況を再現)
        with closing(sqlite3.connect(nas_db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE youtube_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_url TEXT UNIQUE NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "INSERT INTO youtube_subscriptions (channel_url, is_active) VALUES (?, 1)",
                ("https://example.test/channel/known",),
            )
            conn.commit()

        class _RecoverableBaseDir:
            """NASフォールバック→復帰、という時間経過をシミュレートするコールバック。"""

            def __init__(self, local_dir, nas_dir):
                self.local_dir = local_dir
                self.nas_dir = nas_dir
                self.recovered = False

            def __call__(self):
                return self.nas_dir if self.recovered else self.local_dir

        base_dir_resolver = _RecoverableBaseDir(local_base, nas_base)

        extractor = MagicMock()
        extractor.extract_iter.return_value = iter([])
        file_manager = MagicMock()

        with patch.object(module.AppConfig, "get_output_base_dir", side_effect=base_dir_resolver), \
                patch.object(module.AppConfig, "LOCAL_DIR_STR", str(local_base)):
            # UrlExtractorApp.__init__と同じタイミングでSubscriptionManagerを構築する
            # (この時点ではまだNASはフォールバック中)
            manager = module.SubscriptionManager(extractor, file_manager)

            # その後、巡回開始までにNASが復帰する
            base_dir_resolver.recovered = True

            manager.process_subscriptions()

        # NAS側に事前登録されていたサブスクリプションが実際に処理されたこと
        # (ローカルの空DBが使われていれば、ここは一度も呼ばれない)
        extractor.extract_iter.assert_called_once_with("https://example.test/channel/known")

        # ローカル側にはゴミの空DBが作られていないこと
        assert not (local_base.parent / "home_system.db").exists()


class TestProcessSubscriptionsHandlesMkdirOSError:
    """Issue #185の回帰テスト: db_path.parent.mkdir()が送出しうるOSError
    (権限エラー・読み取り専用マウント等)はsqlite3.Errorのサブクラスでは
    ないため、以前は`except sqlite3.Error`節で捕捉されず--cron実行全体が
    未処理例外で異常終了していた。process_subscriptions内の他の失敗経路
    (エラーログ出力+安全なreturn)と同じフェイルソフト方針に統一されている
    ことを確認する。"""

    def test_mkdir_permission_error_is_caught_and_logged_not_raised(self, tmp_path, caplog):
        base = tmp_path / "data"
        base.mkdir(parents=True)

        extractor = MagicMock()
        file_manager = MagicMock()
        manager = module.SubscriptionManager(extractor, file_manager)

        # core.logger.setup_logging() は propagate=False で設定するため、
        # rootロガーに依拠するcaplogがそのままでは記録できない
        # (test_newface_monitor_notifier.pyの同種コメント参照)。
        # テスト実行中だけ強制的にTrueへ切り替え、終了後に元の値へ戻す。
        original_propagate = module.logger.propagate
        module.logger.propagate = True
        try:
            with patch.object(module.AppConfig, "get_output_base_dir", return_value=base), \
                    patch("pathlib.Path.mkdir", side_effect=PermissionError("Permission denied")), \
                    caplog.at_level(logging.ERROR, logger=module.logger.name):
                # 例外を送出せずに完走すること自体が回帰確認の対象
                manager.process_subscriptions()
        finally:
            module.logger.propagate = original_propagate

        extractor.extract_iter.assert_not_called()
        assert any("DB初期化エラー" in r.message for r in caplog.records)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
