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
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
