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
            ("devices.json", "{}"),
        ):
            with open(os.path.join(config.BASE_DIR, name), "w", encoding="utf-8") as f:
                f.write(content)
        monkeypatch.setattr(
            config, "BACKUP_FILES", [config.SQLITE_DB_PATH, "config.py", "devices.json"]
        )

        success, msg, size_mb = backup_service.perform_backup()

        assert success is True
        nas_backup_dir = os.path.join(config.NAS_PROJECT_ROOT, "db_backups")
        backups = os.listdir(nas_backup_dir)
        # DBファイル1件 + 設定ファイル2件
        assert len(backups) == 3
        assert any(name.startswith("config_") and name.endswith(".py") for name in backups)
        assert any(name.startswith("devices_") and name.endswith(".json") for name in backups)

    def test_env_file_is_not_in_default_backup_files(self):
        """Issue #649: .env(全シークレット)を NAS へ平文コピーしない。既定の BACKUP_FILES に
        .env が復活していないことを固定する。"""
        assert ".env" not in config.BACKUP_FILES
        assert not any(str(entry).endswith(".env") for entry in config.BACKUP_FILES)

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


class TestPartialNasFileCleanupOnFailure:
    """Issue #248回帰防止: NAS転送が途中で失敗、または転送後の整合性確認に
    失敗した場合、以前はローカルの一時ファイル(temp_path)のみ削除しており、
    NAS側に書きかけ・破損した不完全なファイルがそのまま残置されていた。"""

    def test_partial_nas_file_is_removed_when_copy_raises_midway(self, monkeypatch):
        """shutil.copy2 自体が例外を送出する前に、NAS側へ部分的に書き込み済みの
        ファイルが存在しているケース(ディスク逼迫等で書き込み途中に失敗)を再現する。"""
        real_copy2 = backup_service.shutil.copy2

        def _copy_then_fail(src, dst, *args, **kwargs):
            # コピー自体は一部完了させてから失敗させる(実運用でのディスク逼迫時、
            # OS側のバッファがフラッシュされファイルの一部が既に書き込まれている
            # 状況を模す)
            real_copy2(src, dst, *args, **kwargs)
            raise OSError("Simulated NAS disk full mid-copy")

        monkeypatch.setattr(backup_service.shutil, "copy2", _copy_then_fail)

        success, msg, size_mb = backup_service.perform_backup()

        assert success is False
        nas_backup_dir = os.path.join(config.NAS_PROJECT_ROOT, "db_backups")
        assert os.listdir(nas_backup_dir) == [], (
            "NAS転送失敗時、NAS側に残置された不完全なファイルが削除されているべき"
        )

    def test_partial_nas_file_is_removed_on_integrity_mismatch(self, monkeypatch):
        """転送後の整合性確認(サイズ比較)に失敗した場合も、NAS側に残った
        破損ファイルが削除されること。"""
        real_getsize = os.path.getsize

        def _mismatched_getsize(path):
            size = real_getsize(path)
            if "db_backups" in str(path):
                return size + 1
            return size

        monkeypatch.setattr(backup_service.os.path, "getsize", _mismatched_getsize)

        success, msg, size_mb = backup_service.perform_backup()

        assert success is False
        nas_backup_dir = os.path.join(config.NAS_PROJECT_ROOT, "db_backups")
        assert os.listdir(nas_backup_dir) == [], (
            "整合性確認失敗時、NAS側に残置された破損ファイルが削除されているべき"
        )

    def test_cleanup_failure_does_not_mask_original_error(self, monkeypatch):
        """NAS側ファイルの削除自体が失敗(NAS切断等)しても、例外処理全体が
        中断せず、元のエラーに基づく戻り値がそのまま返ること。"""
        def _copy_then_fail(src, dst, *args, **kwargs):
            raise OSError("Simulated NAS disk full mid-copy")

        monkeypatch.setattr(backup_service.shutil, "copy2", _copy_then_fail)

        original_remove = os.remove

        def _fail_remove_for_nas(path):
            if "db_backups" in str(path):
                raise OSError("Simulated NAS disconnected during cleanup")
            return original_remove(path)

        monkeypatch.setattr(backup_service.os, "remove", _fail_remove_for_nas)

        success, msg, size_mb = backup_service.perform_backup()

        assert success is False
        assert "Simulated NAS disk full mid-copy" in msg


class TestOffsiteCopy:
    """2026-09-19: NAS への転送成功後、最新1世代をオフサイト(rclone)へ複製する。

    以前はバックアップが NAS にしか無く、NAS 故障時に DB 本体とバックアップを同時に失う
    構成だった。オフサイト複製の失敗は NAS バックアップの成否に影響させない。
    """

    def _fake_rclone(self, monkeypatch, *, fail=None):
        calls = []
        monkeypatch.setattr(backup_service.shutil, "which", lambda name: "/usr/bin/rclone")

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if fail == "error":
                raise backup_service.subprocess.CalledProcessError(1, cmd, stderr="Failed to copy: quota\n")
            if fail == "timeout":
                raise backup_service.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
            return backup_service.subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(backup_service.subprocess, "run", fake_run)
        return calls

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.setattr(config, "DB_BACKUP_OFFSITE_REMOTE", "", raising=False)
        calls = self._fake_rclone(monkeypatch)

        success, _msg, _size = backup_service.perform_backup()

        assert success is True
        assert calls == []

    def test_copies_the_new_nas_backup_as_latest(self, monkeypatch):
        monkeypatch.setattr(config, "DB_BACKUP_OFFSITE_REMOTE", "gdrive:backup/", raising=False)
        calls = self._fake_rclone(monkeypatch)

        success, _msg, _size = backup_service.perform_backup()

        assert success is True
        assert len(calls) == 1
        cmd, kwargs = calls[0]
        nas_backup_dir = os.path.join(config.NAS_PROJECT_ROOT, "db_backups")
        (backup_name,) = os.listdir(nas_backup_dir)
        assert cmd[:2] == ["/usr/bin/rclone", "copyto"]
        assert cmd[2] == os.path.join(nas_backup_dir, backup_name)
        assert cmd[3] == "gdrive:backup/home_system_latest.db"  # 末尾の / は二重にしない
        assert kwargs["timeout"] == backup_service.OFFSITE_TIMEOUT_SEC

    @pytest.mark.parametrize("fail", ["error", "timeout"])
    def test_offsite_failure_does_not_fail_the_nas_backup(self, monkeypatch, fail):
        monkeypatch.setattr(config, "DB_BACKUP_OFFSITE_REMOTE", "gdrive:backup", raising=False)
        self._fake_rclone(monkeypatch, fail=fail)
        errors = []
        monkeypatch.setattr(backup_service.logger, "error", lambda msg: errors.append(msg))

        success, _msg, _size = backup_service.perform_backup()

        assert success is True
        assert any("オフサイト複製" in m for m in errors)

    def test_not_attempted_when_nas_backup_fails(self, monkeypatch):
        monkeypatch.setattr(config, "DB_BACKUP_OFFSITE_REMOTE", "gdrive:backup", raising=False)
        calls = self._fake_rclone(monkeypatch)
        original_makedirs = os.makedirs

        def _fail_nas(path, exist_ok=False):
            if "db_backups" in str(path):
                raise PermissionError("Simulated NAS mount failure")
            return original_makedirs(path, exist_ok=exist_ok)

        monkeypatch.setattr(backup_service.os, "makedirs", _fail_nas)

        success, _msg, _size = backup_service.perform_backup()

        assert success is False
        assert calls == []

    def test_missing_rclone_is_logged(self, monkeypatch):
        monkeypatch.setattr(config, "DB_BACKUP_OFFSITE_REMOTE", "gdrive:backup", raising=False)
        monkeypatch.setattr(backup_service.shutil, "which", lambda name: None)
        errors = []
        monkeypatch.setattr(backup_service.logger, "error", lambda msg: errors.append(msg))

        assert backup_service._copy_latest_offsite(backup_service.Path("/nas/x.db")) is False
        assert any("rclone" in m for m in errors)
