# MY_HOME_SYSTEM/tests/test_config.py
"""
config.py の環境変数パース・devices.json バリデーションの境界値テスト。

config.py はモジュールロード時に一度だけ環境変数を評価する設計のため、
「特定の環境変数を与えたときの挙動」をテストするには importlib.reload(config) で
モジュールを再実行する必要がある。他のモジュールは `import config` 経由で
`config.XXX` を呼び出し時に参照するため(値を import 時にコピーしていないため)、
reload後は他モジュールにも新しい値が正しく伝播する。
テスト終了後は必ず元の環境変数・モジュール状態に戻す(他のテストファイルに影響しないため)。
"""
import contextlib
import importlib
import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config


@contextlib.contextmanager
def _with_env(**overrides):
    """
    指定した環境変数を一時的に設定/削除して config を再読み込みし、
    テスト終了後は環境変数・configモジュールの両方を元の状態に戻す。
    """
    missing = object()
    original = {key: os.environ.get(key, missing) for key in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(config)
        yield config
    finally:
        for key, value in original.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(config)


class TestSqliteDbPathEnvOverride:
    def test_env_var_overrides_default_path(self):
        with _with_env(SQLITE_DB_PATH="/tmp/custom_test_path.db") as cfg:
            assert cfg.SQLITE_DB_PATH == "/tmp/custom_test_path.db"

    def test_unset_env_var_falls_back_to_default(self):
        with _with_env(SQLITE_DB_PATH=None) as cfg:
            assert cfg.SQLITE_DB_PATH == os.path.join(cfg.BASE_DIR, "home_system.db")

    def test_empty_string_env_var_falls_back_to_default(self):
        """空文字は `os.getenv(...) or default` の or 演算子でFalsy扱いとなり、デフォルトにフォールバックする"""
        with _with_env(SQLITE_DB_PATH="") as cfg:
            assert cfg.SQLITE_DB_PATH == os.path.join(cfg.BASE_DIR, "home_system.db")


class TestTvUnlockQuestIdsParsing:
    def test_valid_comma_separated_ids(self):
        with _with_env(TV_UNLOCK_QUEST_IDS="1,2,3") as cfg:
            assert cfg.TV_UNLOCK_QUEST_IDS == [1, 2, 3]

    def test_malformed_entries_are_silently_skipped(self):
        """数字以外・空要素が混じっていても例外にならず、有効な数字だけが残ること"""
        with _with_env(TV_UNLOCK_QUEST_IDS="1, abc, , 3,, -5") as cfg:
            # "-5" は str.isdigit() が False (先頭の'-'を含むため) なので除外される
            assert cfg.TV_UNLOCK_QUEST_IDS == [1, 3]

    def test_unset_defaults_to_empty_list(self):
        with _with_env(TV_UNLOCK_QUEST_IDS=None) as cfg:
            assert cfg.TV_UNLOCK_QUEST_IDS == []

    def test_empty_string_defaults_to_empty_list(self):
        with _with_env(TV_UNLOCK_QUEST_IDS="") as cfg:
            assert cfg.TV_UNLOCK_QUEST_IDS == []


class TestAllowAllOrigins:
    def test_true_switches_cors_origins_to_wildcard(self):
        with _with_env(ALLOW_ALL_ORIGINS="true") as cfg:
            assert cfg.CORS_ORIGINS == ["*"]

    def test_case_insensitive_true(self):
        with _with_env(ALLOW_ALL_ORIGINS="TRUE") as cfg:
            assert cfg.CORS_ORIGINS == ["*"]

    def test_false_or_unset_keeps_fixed_origin_list(self):
        with _with_env(ALLOW_ALL_ORIGINS=None) as cfg:
            assert cfg.CORS_ORIGINS != ["*"]
            assert "http://localhost:5173" in cfg.CORS_ORIGINS

    def test_arbitrary_string_is_treated_as_false(self):
        with _with_env(ALLOW_ALL_ORIGINS="yes-please") as cfg:
            assert cfg.CORS_ORIGINS != ["*"]


class TestFrontendUrlOriginInCorsOrigins:
    """Issue #112回帰防止: ブラウザのOriginヘッダーはscheme://host[:port]のみで
    パスを含まないため(Starlette CORSMiddlewareは完全一致比較)、パス付きの
    FRONTEND_URLをそのままCORS_ORIGINSに入れると永久に一致しない死にエントリに
    なっていた。"""

    def test_frontend_url_with_path_is_stripped_to_origin_only(self):
        with _with_env(FRONTEND_URL="http://192.168.1.200:8000/quest", ALLOW_ALL_ORIGINS=None) as cfg:
            assert "http://192.168.1.200:8000/quest" not in cfg.CORS_ORIGINS
            assert "http://192.168.1.200:8000" in cfg.CORS_ORIGINS

    def test_frontend_url_attribute_itself_keeps_its_path(self):
        """FRONTEND_URL自体はpost_boot_health_check.py等が実際にHTTPリクエストを
        送る完全なURLとして使われるため、パスを保持したままであること。"""
        with _with_env(FRONTEND_URL="http://192.168.1.200:8000/quest", ALLOW_ALL_ORIGINS=None) as cfg:
            assert cfg.FRONTEND_URL == "http://192.168.1.200:8000/quest"

    def test_frontend_url_without_path_is_unaffected(self):
        with _with_env(FRONTEND_URL="https://example.com", ALLOW_ALL_ORIGINS=None) as cfg:
            assert "https://example.com" in cfg.CORS_ORIGINS


class TestChildrenNamesParsing:
    def test_empty_string_produces_empty_list_not_list_with_empty_string(self):
        """''.split(',') は [''] になってしまうため、空文字を明示的にハンドリングしているか"""
        with _with_env(CHILDREN_NAMES=None) as cfg:
            assert cfg.CHILDREN_NAMES == []

    def test_comma_separated_names(self):
        with _with_env(CHILDREN_NAMES="太郎,花子") as cfg:
            assert cfg.CHILDREN_NAMES == ["太郎", "花子"]


class TestDevicesJsonValidation:
    @contextlib.contextmanager
    def _with_devices_json(self, content: str):
        path = config.DEVICES_JSON_PATH
        existed_before = os.path.exists(path)
        backup = None
        if existed_before:
            with open(path, "r", encoding="utf-8") as f:
                backup = f.read()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            importlib.reload(config)
            yield config
        finally:
            if backup is not None:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(backup)
            elif os.path.exists(path):
                os.remove(path)
            importlib.reload(config)

    def test_valid_devices_json_populates_cameras(self):
        content = """
        {
            "cameras": [
                {"id": "cam1", "name": "Entrance", "location": "Front", "ip": "192.168.1.50"}
            ]
        }
        """
        with self._with_devices_json(content) as cfg:
            assert len(cfg.CAMERAS) == 1
            assert cfg.CAMERAS[0]["id"] == "cam1"

    def test_invalid_camera_missing_required_field_does_not_crash_and_leaves_cameras_empty(self):
        """必須フィールド(ip等)が欠けている場合、ValidationErrorを捕捉して起動を継続すること"""
        content = """
        {
            "cameras": [
                {"id": "cam1"}
            ]
        }
        """
        with self._with_devices_json(content) as cfg:
            assert cfg.CAMERAS == []

    def test_malformed_json_does_not_crash_module_load(self):
        with self._with_devices_json("{not valid json!!!") as cfg:
            assert cfg.CAMERAS == []
            assert cfg.MONITOR_DEVICES == []
