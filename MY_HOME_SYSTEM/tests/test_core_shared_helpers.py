# MY_HOME_SYSTEM/tests/test_core_shared_helpers.py
"""Issue #661 で横断的な重複を集約した共通ヘルパーのテスト。

- core/state_file.py: 状態ファイルの原子的な読み書き
- core/discord.py: Webhook 送信(分割・リトライ・URL マスク)
- core/onvif_utils.py: WSDL 探索
- core/database.get_ro_connection: 読み取り専用接続
"""
import json
import os
import sqlite3
import sys
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core import discord as core_discord
from core import onvif_utils, state_file
from core.database import RO_CONNECT_TIMEOUT_SEC, get_ro_connection


class TestStateFile:
    def test_write_then_read_round_trip(self, tmp_path):
        path = str(tmp_path / "sub" / "state.json")  # 親ディレクトリも作られること
        assert state_file.write_json_atomic(path, {"is_healthy": False, "n": 1}) is True
        assert state_file.read_json(path) == {"is_healthy": False, "n": 1}

    def test_missing_file_returns_default(self, tmp_path):
        assert state_file.read_json(str(tmp_path / "absent.json"), default={"x": 1}) == {"x": 1}

    def test_broken_json_returns_default(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        sentinel = object()
        assert state_file.read_json(str(path), default=sentinel) is sentinel

    def test_write_leaves_no_temp_file_behind(self, tmp_path):
        path = str(tmp_path / "state.json")
        state_file.write_json_atomic(path, {"a": 1})
        leftovers = [p for p in os.listdir(tmp_path) if ".tmp." in p]
        assert leftovers == []

    def test_write_failure_returns_false_without_raising(self, tmp_path):
        # 書き込み先を既存ディレクトリにして失敗させる(例外は外に出さない契約)
        path = str(tmp_path / "dir_instead_of_file")
        os.makedirs(path)
        assert state_file.write_json_atomic(path, {"a": 1}) is False

    def test_text_round_trip(self, tmp_path):
        path = str(tmp_path / "last_run.txt")
        assert state_file.write_text_atomic(path, "2026-09-16") is True
        assert state_file.read_text(path) == "2026-09-16"
        assert state_file.read_text(str(tmp_path / "absent.txt"), default=None) is None

    def test_partially_written_file_is_never_observed(self, tmp_path):
        """os.replace による差し替えなので、読み手は旧か新のどちらか完全な内容しか見ない。"""
        path = str(tmp_path / "state.json")
        state_file.write_json_atomic(path, {"generation": 1})
        # 書き込み中に読んでも、少なくとも JSON として壊れていないこと
        state_file.write_json_atomic(path, {"generation": 2})
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["generation"] == 2


class TestDiscordHelper:
    def test_redacts_webhook_token(self):
        masked = core_discord.redact_webhook_url(
            "failed for url: https://discord.com/api/webhooks/123/AbC-dEf_123"
        )
        assert "AbC-dEf_123" not in masked
        assert "<redacted>" in masked

    def test_split_content_keeps_chunks_under_limit(self):
        text = "\n".join("行%d" % i for i in range(2000))
        chunks = core_discord.split_content(text)
        assert len(chunks) > 1
        assert all(len(c) <= core_discord.CONTENT_CHUNK_SIZE for c in chunks)

    def test_short_content_is_single_chunk(self):
        assert core_discord.split_content("短い") == ["短い"]

    def test_post_webhook_returns_false_without_url(self, monkeypatch):
        post = MagicMock()
        monkeypatch.setattr(core_discord.requests, "post", post)
        assert core_discord.post_webhook(None, "x") is False
        post.assert_not_called()

    def test_post_webhook_success(self, monkeypatch):
        post = MagicMock(return_value=MagicMock(status_code=204))
        monkeypatch.setattr(core_discord.requests, "post", post)
        assert core_discord.post_webhook("https://discord.com/api/webhooks/1/t", "hello") is True
        assert post.call_count == 1

    def test_post_webhook_retries_on_429_then_gives_up_cleanly(self, monkeypatch):
        post = MagicMock(return_value=MagicMock(status_code=429, headers={"Retry-After": "0.1"}, text="rate limited"))
        monkeypatch.setattr(core_discord.requests, "post", post)
        monkeypatch.setattr(core_discord, "_retry_sleep", lambda s: None)

        assert core_discord.post_webhook("https://discord.com/api/webhooks/1/t", "hello") is False
        assert post.call_count == 1 + core_discord.RETRY_ATTEMPTS

    def test_post_webhook_swallows_exceptions(self, monkeypatch):
        monkeypatch.setattr(core_discord.requests, "post", MagicMock(side_effect=RuntimeError("down")))
        assert core_discord.post_webhook("https://discord.com/api/webhooks/1/t", "hello") is False


class TestOnvifUtils:
    def test_returns_directory_containing_devicemgmt_wsdl(self, tmp_path, monkeypatch):
        wsdl_dir = tmp_path / "onvif" / "wsdl"
        wsdl_dir.mkdir(parents=True)
        (wsdl_dir / "devicemgmt.wsdl").write_text("x", encoding="utf-8")
        monkeypatch.setattr(onvif_utils.sys, "path", [str(tmp_path)])

        assert onvif_utils.find_wsdl_path() == str(wsdl_dir)

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(onvif_utils.sys, "path", [str(tmp_path)])
        assert onvif_utils.find_wsdl_path() is None

    def test_camera_modules_share_the_same_implementation(self):
        """#661: 以前は camera_service と camera_monitor に同一実装が重複していた。"""
        from monitors import camera_monitor
        from services import camera_service

        assert camera_monitor.find_wsdl_path is onvif_utils.find_wsdl_path
        assert camera_service.find_wsdl_path is onvif_utils.find_wsdl_path


class TestGetRoConnection:
    def test_reads_but_cannot_write(self, tmp_path, monkeypatch):
        db_path = tmp_path / "ro.db"
        with sqlite3.connect(db_path) as setup:
            setup.execute("CREATE TABLE t (v TEXT)")
            setup.execute("INSERT INTO t VALUES ('a')")
            setup.commit()
        monkeypatch.setattr(config, "SQLITE_DB_PATH", str(db_path))

        with get_ro_connection() as conn:
            assert conn.execute("SELECT v FROM t").fetchone()["v"] == "a"
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO t VALUES ('b')")

    def test_default_timeout_matches_get_db_cursor(self):
        """#661: 5s/10s/30s とばらついていた読み取りの timeout を 30 秒へ揃えた。"""
        assert RO_CONNECT_TIMEOUT_SEC == 30.0

    def test_db_path_argument_overrides_config(self, tmp_path, monkeypatch):
        """#661 残件: post_boot_health_check は config の相対パスを自前で絶対パスへ
        解決してから接続するため、パスだけを差し替えられる必要がある。"""
        db_path = tmp_path / "explicit.db"
        with sqlite3.connect(db_path) as setup:
            setup.execute("CREATE TABLE t (v TEXT)")
            setup.execute("INSERT INTO t VALUES ('explicit')")
            setup.commit()
        monkeypatch.setattr(config, "SQLITE_DB_PATH", str(tmp_path / "never_used.db"))

        with get_ro_connection(db_path=str(db_path)) as conn:
            assert conn.execute("SELECT v FROM t").fetchone()["v"] == "explicit"


class TestStateFileCallSitesAreMigrated:
    """#661 残件: 7箇所あった状態ファイルの個別実装を core/state_file.py へ寄せた。

    `monitors/server_watchdog.py` だけは compare-and-set(読み取り→判定→書き込みを
    1つの flock 区間に収める)が必要なため、意図的に独自実装のまま残している。
    """

    def test_switchbot_power_monitor_round_trips_through_state_file(self, tmp_path, monkeypatch):
        from monitors import switchbot_power_monitor as spm

        monkeypatch.setattr(spm, "_STATE_FILE", str(tmp_path / "devices.json"))
        spm._save_persisted_states({"dev1": {"power_state": "ON"}})
        assert spm._load_persisted_states() == {"dev1": {"power_state": "ON"}}

    def test_switchbot_power_monitor_falls_back_to_empty_dict_on_broken_file(self, tmp_path, monkeypatch):
        from monitors import switchbot_power_monitor as spm

        path = tmp_path / "devices.json"
        path.write_text("{ broken", encoding="utf-8")
        monkeypatch.setattr(spm, "_STATE_FILE", str(path))
        assert spm._load_persisted_states() == {}

    def test_health_watch_marker_round_trip(self, tmp_path, monkeypatch):
        import datetime

        import monitors.health_watch as health_watch

        monkeypatch.setattr(health_watch, "MARKER_FILE", str(tmp_path / ".marker"))
        now = datetime.datetime(2026, 9, 17, 3, 0, 0)
        health_watch._write_marker(now)
        assert health_watch._read_marker() == now

    def test_health_watch_marker_falls_back_when_missing_or_broken(self, tmp_path, monkeypatch):
        import monitors.health_watch as health_watch

        missing = tmp_path / ".absent"
        monkeypatch.setattr(health_watch, "MARKER_FILE", str(missing))
        assert health_watch._read_marker() is not None  # 既定の遡り時間で補完される

        broken = tmp_path / ".broken"
        broken.write_text("not-a-timestamp", encoding="utf-8")
        monkeypatch.setattr(health_watch, "MARKER_FILE", str(broken))
        assert health_watch._read_marker() is not None

    def test_health_watch_notify_state_suppresses_repeat_then_allows_change(self, tmp_path, monkeypatch):
        import datetime

        import monitors.health_watch as health_watch

        monkeypatch.setattr(health_watch, "NOTIFY_STATE_FILE", str(tmp_path / ".notify"))
        now = datetime.datetime(2026, 9, 17, 3, 0, 0)
        assert health_watch._should_notify(["異常A"], now) is True
        # 同じ異常セットは RENOTIFY_INTERVAL_SEC の間、再通知しない
        assert health_watch._should_notify(["異常A"], now) is False
        # セットが変われば即座に通知する
        assert health_watch._should_notify(["異常A", "異常B"], now) is True

    def test_health_watch_notify_state_notifies_when_file_is_broken(self, tmp_path, monkeypatch):
        """壊れている場合は「通知する」側へ倒す(取りこぼしより重複の方が安全)。"""
        import datetime

        import monitors.health_watch as health_watch

        path = tmp_path / ".notify"
        path.write_text("{ broken", encoding="utf-8")
        monkeypatch.setattr(health_watch, "NOTIFY_STATE_FILE", str(path))
        assert health_watch._should_notify(["異常A"], datetime.datetime(2026, 9, 17, 3, 0, 0)) is True

    def test_weekly_report_flag_round_trips_through_state_file(self, tmp_path, monkeypatch):
        import weekly_analyze_report as report
        from core import state_file as sf

        path = str(tmp_path / "last_weekly_report.txt")
        monkeypatch.setattr(report, "LAST_RUN_FILE", path)
        assert sf.write_text_atomic(path, "2026-09-14") is True
        assert sf.read_text(path) == "2026-09-14"


class TestRefCountedLockRegistryConsolidation:
    """#661 残件: camera_service の _RefCountedLock は core.utils の同名実装と
    完全重複していたため、レジストリへ一本化した。"""

    def test_camera_service_uses_the_shared_registry(self):
        from core.utils import RefCountedLockRegistry
        from services import camera_service

        assert isinstance(camera_service._vod_generation_locks, RefCountedLockRegistry)
        assert isinstance(camera_service._live_stream_locks, RefCountedLockRegistry)
        assert not hasattr(camera_service, "_RefCountedLock")

    def test_registry_supports_len_and_clear(self):
        from core.utils import RefCountedLockRegistry

        registry = RefCountedLockRegistry()
        with registry.acquire("k"):
            assert len(registry) == 1
        assert len(registry) == 0

        with registry.acquire("k"):
            registry.clear()
            assert len(registry) == 0
