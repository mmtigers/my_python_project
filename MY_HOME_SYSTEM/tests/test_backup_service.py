# MY_HOME_SYSTEM/tests/test_backup_service.py
"""
services/backup_service.py の perform_backup のテスト。

「コピーしたつもりが実は壊れていた」という失敗パターンを防ぐため、
成功・NASディレクトリ作成失敗・転送後の整合性チェック失敗の3パターンを検証する。
実際のNASには一切触れず、config.NAS_PROJECT_ROOT等をtmp_pathへ差し替える。
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import backup_service


@pytest.fixture(autouse=True)
def _isolate_backup_paths(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NAS_PROJECT_ROOT", str(tmp_path / "nas_root"))
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path / "app_base"))
    os.makedirs(config.BASE_DIR, exist_ok=True)
    monkeypatch.setattr(backup_service, "send_push", lambda **kwargs: None)


def test_perform_backup_success(monkeypatch):
    success, msg, size_mb = backup_service.perform_backup()

    assert success is True
    assert size_mb > 0

    nas_backup_dir = os.path.join(config.NAS_PROJECT_ROOT, "db_backups")
    backups = os.listdir(nas_backup_dir)
    assert len(backups) == 1
    # ローカル一時ファイルは転送成功後に削除されていること
    temp_dir = os.path.join(config.BASE_DIR, "temp_backups")
    assert os.listdir(temp_dir) == []


def test_perform_backup_notifies_and_returns_false_on_nas_dir_creation_failure(monkeypatch):
    notified = []
    monkeypatch.setattr(
        backup_service, "send_push", lambda **kwargs: notified.append(kwargs) or True
    )

    original_makedirs = os.makedirs

    def _fail_only_for_nas_backup_dir(path, exist_ok=False):
        if "db_backups" in str(path):
            raise PermissionError("Simulated NAS mount failure")
        return original_makedirs(path, exist_ok=exist_ok)

    monkeypatch.setattr(backup_service.os, "makedirs", _fail_only_for_nas_backup_dir)

    success, msg, size_mb = backup_service.perform_backup()

    assert success is False
    assert size_mb == 0.0
    # NASディレクトリ作成失敗時に通知が1回だけ飛ぶこと(以前は内側except+外側exceptで
    # 二重に通知されるバグがあった。再発防止のため厳密に1回であることを検証する)。
    assert len(notified) == 1
    assert "NASディレクトリ作成失敗" in notified[0]["messages"][0]["text"]
    assert all("🚨" in n["messages"][0]["text"] for n in notified)


class TestBackupFilesConfigCopy:
    """Issue #113回帰防止: config.BACKUP_FILESに列挙された設定ファイル
    (config.py/.env/devices.json)は、以前はどのコードからも参照されず
    バックアップ対象になっていなかった(DBファイル単体しか転送されなかった)。"""

    def test_additional_files_in_backup_files_are_copied_to_nas(self, monkeypatch):
        for name, content in (
            ("config.py", "# dummy config"),
            (".env", "SOME_KEY=1"),
            ("devices.json", "{}"),
        ):
            with open(os.path.join(config.BASE_DIR, name), "w", encoding="utf-8") as f:
                f.write(content)
        monkeypatch.setattr(
            config, "BACKUP_FILES", [config.SQLITE_DB_PATH, "config.py", ".env", "devices.json"]
        )

        success, msg, size_mb = backup_service.perform_backup()

        assert success is True
        nas_backup_dir = os.path.join(config.NAS_PROJECT_ROOT, "db_backups")
        backups = os.listdir(nas_backup_dir)
        # DBファイル1件 + 設定ファイル3件
        assert len(backups) == 4
        assert any(name.startswith("config_") and name.endswith(".py") for name in backups)
        assert any(name.startswith(".env_") for name in backups)
        assert any(name.startswith("devices_") and name.endswith(".json") for name in backups)

    def test_missing_additional_file_is_skipped_without_failing_backup(self, monkeypatch):
        """BACKUP_FILESに存在しないファイルが列挙されていても、DBバックアップ自体は成功すること"""
        monkeypatch.setattr(config, "BACKUP_FILES", [config.SQLITE_DB_PATH, "nonexistent.json"])

        success, msg, size_mb = backup_service.perform_backup()

        assert success is True
        nas_backup_dir = os.path.join(config.NAS_PROJECT_ROOT, "db_backups")
        backups = os.listdir(nas_backup_dir)
        assert len(backups) == 1


def test_perform_backup_detects_transfer_integrity_mismatch(monkeypatch):
    """
    shutil.copy2 自体は成功しても、転送後のサイズ検証で不一致が検出された場合は
    失敗として扱われ、破損したバックアップを「成功」と誤報しないこと。
    """
    real_getsize = os.path.getsize

    def _mismatched_getsize(path):
        size = real_getsize(path)
        # NAS側(転送先)のファイルだけサイズがズレて見えるようにし、
        # 「コピーは成功したように見えるが実は壊れていた」ケースを再現する
        if "db_backups" in str(path):
            return size + 1
        return size

    monkeypatch.setattr(backup_service.os.path, "getsize", _mismatched_getsize)

    success, msg, size_mb = backup_service.perform_backup()

    assert success is False
    assert "整合性" in msg
