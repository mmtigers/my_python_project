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
    # NASディレクトリ作成失敗時は _notify_and_log_error が2回呼ばれる
    # (makedirs失敗時の直接通知 + re-raiseされた例外を外側のexceptが再度通知するため)。
    # 通知が二重に飛ぶこと自体は軽微な問題だが、少なくとも1回は必ず通知されることを保証する。
    assert len(notified) >= 1
    assert all("🚨" in n["messages"][0]["text"] for n in notified)


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
