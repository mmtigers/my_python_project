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


class TestYoutubeRewardIdsParsing:
    def test_valid_comma_separated_ids(self):
        with _with_env(YOUTUBE_REWARD_IDS="1,2,3") as cfg:
            assert cfg.YOUTUBE_REWARD_IDS == [1, 2, 3]

    def test_malformed_entries_are_silently_skipped(self):
        """数字以外・空要素が混じっていても例外にならず、有効な数字だけが残ること"""
        with _with_env(YOUTUBE_REWARD_IDS="1, abc, , 3,, -5") as cfg:
            # "-5" は str.isdigit() が False (先頭の'-'を含むため) なので除外される
            assert cfg.YOUTUBE_REWARD_IDS == [1, 3]

    def test_unset_env_var_falls_back_to_youtube_reward_default(self):
        """未設定時はquest_data.pyのYouTube報酬の既定reward_id(10,11,12)にフォールバックする"""
        with _with_env(YOUTUBE_REWARD_IDS=None) as cfg:
            assert cfg.YOUTUBE_REWARD_IDS == [10, 11, 12]

    def test_empty_string_disables_the_cooldown(self):
        """空文字を明示的に指定した場合はデフォルトへフォールバックせず機能を無効化する"""
        with _with_env(YOUTUBE_REWARD_IDS="") as cfg:
            assert cfg.YOUTUBE_REWARD_IDS == []


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


class TestVerifyAndInitializeStorage:
    """verify_and_initialize_storage() のテスト(Issue #292)。

    Issue #292でExponential Backoffのループ自体をcore.utils.retry_with_backoffへ
    委譲するリファクタリングを行ったため、外部から見た挙動(戻り値・リトライ回数・
    最終的にキャッチする例外の種類)が変わっていないことを回帰確認する。
    実際のsleepはcore.utils側で発生するため、core.utils.time.sleepをmonkeypatchで
    無効化する。
    """

    def test_returns_true_when_path_is_writable_on_first_attempt(self, tmp_path, monkeypatch):
        import core.utils as core_utils
        monkeypatch.setattr(core_utils.time, "sleep", lambda s: None)
        target = tmp_path / "nas_dir"

        result = config.verify_and_initialize_storage(str(target))

        assert result is True
        assert target.is_dir()
        # テストファイルはクリーンアップされていること
        assert not (target / ".write_test").exists()

    def test_recovers_after_transient_oserror_then_succeeds(self, tmp_path, monkeypatch):
        import core.utils as core_utils
        sleeps = []
        monkeypatch.setattr(core_utils.time, "sleep", lambda s: sleeps.append(s))
        target = tmp_path / "nas_dir"
        call_count = {"n": 0}
        real_makedirs = os.makedirs

        def flaky_makedirs(path, exist_ok=False):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise OSError("simulated transient mount delay")
            return real_makedirs(path, exist_ok=exist_ok)

        monkeypatch.setattr(config.os, "makedirs", flaky_makedirs)

        result = config.verify_and_initialize_storage(str(target), max_retries=5)

        assert result is True
        assert call_count["n"] == 3
        assert sleeps == [1, 2]

    def test_returns_false_after_exhausting_retries(self, tmp_path, monkeypatch):
        import core.utils as core_utils
        monkeypatch.setattr(core_utils.time, "sleep", lambda s: None)
        target = tmp_path / "nas_dir"

        def always_fails(path, exist_ok=False):
            raise PermissionError("simulated permanent permission error")

        monkeypatch.setattr(config.os, "makedirs", always_fails)

        result = config.verify_and_initialize_storage(str(target), max_retries=2)

        assert result is False


class TestIssue488DeadConstantsRemoved:
    """Issue #488: 未実装機能(給与PDF処理・小児科予約監視・SUUMO/地価監視・
    Google Photos連携・買い物/美容院予約監視)向けの定数と、廃止済み機能
    (旧timelapse_runner.py等)の残骸定数など、アプリ内のどこからも参照されて
    いなかった53件の定数を削除した(オーナー判断により削除確定)。
    再度復活しないよう、モジュールから消えていることを回帰テストで固定する。
    """

    _REMOVED_NAMES = [
        # 給与(Salary)PDF処理 (8件、未実装)
        "SALARY_PDF_PASSWORDS", "SALARY_DATA_DIR", "SALARY_CSV_PATH", "BONUS_CSV_PATH",
        "SALARY_MAIL_SENDER", "GMAIL_USER", "GMAIL_APP_PASSWORD", "SALARY_IMAGE_DIR",
        # 小児科予約監視 (8件、未実装)
        "CLINIC_MONITOR_URL", "CLINIC_MONITOR_START_HOUR", "CLINIC_MONITOR_END_HOUR",
        "CLINIC_REQUEST_TIMEOUT", "CLINIC_USER_AGENT",
        "CLINIC_HTML_DIR", "CLINIC_STATS_CSV", "CLINIC_GRAPH_PATH",
        # SUUMO/地価/不動産監視 (6件、未実装)
        "SUUMO_SEARCH_URL", "SUUMO_MAX_BUDGET", "SUUMO_MONITOR_INTERVAL",
        "LAND_PRICE_TARGETS", "REINFOLIB_API_KEY", "REINFOLIB_WEB_URL",
        # Google Photos連携 (3件、未実装)
        "GOOGLE_PHOTOS_CREDENTIALS", "GOOGLE_PHOTOS_TOKEN", "GOOGLE_PHOTOS_SCOPES",
        # 買い物/美容院予約監視 (3件、未実装)
        "SHOPPING_TARGETS", "HAIRCUT_TARGETS", "HAIRCUT_CYCLE_DAYS",
        # 子供の健康チェック関連 (3件、未実装)
        "CHILDREN_NAMES", "CHILD_CHECK_TIME", "CHILD_SYMPTOMS",
        # ENV / DISCORD_WEBHOOK_ERROR_CAM (2件)
        "ENV", "DISCORD_WEBHOOK_ERROR_CAM",
        # 旧timelapse_runner.py/timelapse_generator.py(#485で削除済み)専用の残骸 (10件)
        "CAMERA_IP", "CAMERA_USER", "CAMERA_PASS",
        "TIMELAPSE_CAMERAS", "TIMELAPSE_SCHEDULES", "TIMELAPSE_FPS",
        "TIMELAPSE_BITRATE", "TIMELAPSE_MAXRATE", "TIMELAPSE_SEGMENT_TIME",
        "TMP_VIDEO_DIR",
        # その他、直接・間接いずれからも未参照だったもの (10件)
        "OHAYO_KEYWORDS", "CAR_RULE_KEYWORDS", "CHECK_ZOROME", "IMPORTANT_DATES",
        "MENU_OPTIONS", "MESSAGE_LENGTH_LIMIT",
        "SQLITE_TABLE_HEALTH", "SQLITE_TABLE_OHAYO",
        "BICYCLE_PARKING_URL", "DEFAULT_ASSETS_DIR",
    ]

    def test_53_confirmed_dead_constants_are_gone(self):
        assert len(self._REMOVED_NAMES) == 53
        still_present = [name for name in self._REMOVED_NAMES if hasattr(config, name)]
        assert still_present == []

    def test_allow_all_origins_was_kept_despite_looking_unreferenced(self):
        """ALLOW_ALL_ORIGINS は直接参照するコードは無いが、import時に
        CORS_ORIGINS を書き換えるため削除してはならない(このテストが常に
        参照する形にすることで、今後の類似の棚卸しでの誤削除を防ぐ)。"""
        assert hasattr(config, "ALLOW_ALL_ORIGINS")
        with _with_env(ALLOW_ALL_ORIGINS="true") as cfg:
            assert cfg.CORS_ORIGINS == ["*"]

    def test_default_sound_source_still_resolves_without_default_assets_dir(self):
        """DEFAULT_ASSETS_DIR という名前のモジュール属性は削除したが、
        実際に使われているのは派生値の DEFAULT_SOUND_SOURCE 側であり、
        そちらは BASE_DIR から直接組み立てる形にして残している。"""
        assert config.DEFAULT_SOUND_SOURCE == os.path.join(config.BASE_DIR, "defaults", "sounds")
