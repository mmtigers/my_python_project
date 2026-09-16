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
