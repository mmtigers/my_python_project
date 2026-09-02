# MY_HOME_SYSTEM/tests/test_config_lazy_paths.py
"""
config.py のNASパス遅延解決(Issue #330 PR-B、PEP 562のモジュール__getattr__)のテスト。

以前は import config の時点で ensure_safe_path_with_backoff (書き込みテスト+
Exponential Backoff、最悪 約31秒/パス) がNAS上の ASSETS_DIR / TMP_VIDEO_DIR に対して
実行され、NAS障害時にconfigをimportするだけのプロセスまでブロックしていた。
現在は初回アクセス時に解決してモジュール属性へキャッシュする。
"""
import os
import subprocess
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

_LAZY_NAMES = ["ASSETS_DIR", "TMP_VIDEO_DIR", *config._ASSETS_DERIVED_PATHS]


@pytest.fixture
def clean_lazy_cache():
    """遅延解決のキャッシュ(モジュール属性)をテスト前後で退避・復元する。

    monkeypatch.delattr は「テスト前に存在しなかった属性がテスト中にキャッシュされた」
    ケースを巻き戻せないため、明示的に退避・復元する。
    """
    saved = {n: vars(config)[n] for n in _LAZY_NAMES if n in vars(config)}
    for n in _LAZY_NAMES:
        vars(config).pop(n, None)
    yield
    for n in _LAZY_NAMES:
        vars(config).pop(n, None)
    vars(config).update(saved)


class TestLazyResolution:
    def test_assets_dir_resolves_once_and_caches(self, clean_lazy_cache, tmp_path, monkeypatch):
        calls = []

        def fake_ensure(preferred_path, fallback_name, max_retries=5):
            calls.append(fallback_name)
            path = str(tmp_path / fallback_name)
            os.makedirs(path, exist_ok=True)
            return path

        monkeypatch.setattr(config, "ensure_safe_path_with_backoff", fake_ensure)

        first = config.ASSETS_DIR
        second = config.ASSETS_DIR

        assert first == second == str(tmp_path / "assets")
        # 2回目のアクセスはモジュール属性キャッシュ経由で、検証は1回しか走らないこと
        assert calls == ["assets"]
        # 旧import時ループにあったNAS配下サブディレクトリも解決時に作成されること
        assert os.path.isdir(os.path.join(first, "salary_images"))
        assert os.path.isdir(os.path.join(first, "clinic_html"))

    def test_derived_paths_join_onto_assets_dir(self, clean_lazy_cache, tmp_path, monkeypatch):
        monkeypatch.setattr(
            config, "ensure_safe_path_with_backoff",
            lambda p, f, max_retries=5: str(tmp_path / f),
        )

        assets = config.ASSETS_DIR
        assert config.SOUND_DIR == os.path.join(assets, "sounds")
        assert config.SALARY_IMAGE_DIR == os.path.join(assets, "salary_images")
        assert config.CLINIC_HTML_DIR == os.path.join(assets, "clinic_html")
        assert config.CLINIC_STATS_CSV == os.path.join(assets, "clinic_stats.csv")
        assert config.CLINIC_GRAPH_PATH == os.path.join(assets, "clinic_trend.png")

    def test_tmp_video_dir_resolves_lazily(self, clean_lazy_cache, tmp_path, monkeypatch):
        monkeypatch.setattr(
            config, "ensure_safe_path_with_backoff",
            lambda p, f, max_retries=5: str(tmp_path / f),
        )
        assert config.TMP_VIDEO_DIR == str(tmp_path / "tmp_video")
        # キャッシュされ、以後は__getattr__を経由しない(=モジュール属性に昇格)
        assert vars(config)["TMP_VIDEO_DIR"] == str(tmp_path / "tmp_video")

    def test_unknown_attribute_still_raises_attribute_error(self):
        with pytest.raises(AttributeError):
            _ = config.THIS_CONSTANT_DOES_NOT_EXIST

    def test_prewarm_resolves_all_lazy_paths(self, clean_lazy_cache, tmp_path, monkeypatch):
        monkeypatch.setattr(
            config, "ensure_safe_path_with_backoff",
            lambda p, f, max_retries=5: str(tmp_path / f),
        )
        config.prewarm_nas_paths()
        for name in _LAZY_NAMES:
            assert name in vars(config), f"{name} がプリウォームで解決されていません"


class TestImportDoesNotTouchNasPaths:
    def test_fresh_import_does_not_resolve_nas_paths(self, tmp_path):
        """import config の時点ではNAS依存パスが未解決(=NASアクセスなし)であること。

        別プロセスでconfigを新規importし、遅延対象がモジュール属性として
        存在しない(=__getattr__が一度も呼ばれていない)ことを確認する。
        """
        code = (
            "import config, sys; "
            "lazy = ['ASSETS_DIR', 'TMP_VIDEO_DIR', *config._ASSETS_DERIVED_PATHS]; "
            "resolved = [n for n in lazy if n in vars(config)]; "
            "sys.exit(0 if not resolved else 1)"
        )
        env = dict(os.environ)
        env.update({
            "NAS_MOUNT_POINT": str(tmp_path / "nas"),
            "SQLITE_DB_PATH": ":memory:",
            "NOTIFICATION_TARGET": "none",
        })
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BASE_DIR, env=env,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            "import時点でNAS依存パスが解決されています(遅延化の回帰): "
            f"stdout={result.stdout} stderr={result.stderr}"
        )
