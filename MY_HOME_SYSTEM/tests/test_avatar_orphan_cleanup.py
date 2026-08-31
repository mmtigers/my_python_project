# MY_HOME_SYSTEM/tests/test_avatar_orphan_cleanup.py
"""
services/quest_service.py UserService.update_avatar() の孤立ファイル対策の回帰テスト。
(docsバックログ B6: アバター再アップロード時に旧ファイルがUPLOAD_DIRへ残り続ける問題)
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import common
from services.quest_service import user_service


def _insert_user(user_id="test_user", avatar="🙂"):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, avatar, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, "テスト", avatar, common.get_now_iso()),
        )


def test_update_avatar_removes_old_uploaded_file(isolated_db, tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))

    old_file = upload_dir / "old-avatar.png"
    old_file.write_bytes(b"old")
    _insert_user(avatar="/uploads/old-avatar.png")

    result = user_service.update_avatar("test_user", "/uploads/new-avatar.png")

    assert result["avatar"] == "/uploads/new-avatar.png"
    assert not old_file.exists()


def test_update_avatar_does_not_delete_emoji_default(isolated_db, tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))

    _insert_user(avatar="🙂")

    # 例外が出ないこと(絵文字はファイルパスとして扱われない)を確認するだけでよい
    result = user_service.update_avatar("test_user", "/uploads/new-avatar.png")
    assert result["avatar"] == "/uploads/new-avatar.png"


def test_update_avatar_keeps_file_when_url_unchanged(isolated_db, tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))

    same_file = upload_dir / "same-avatar.png"
    same_file.write_bytes(b"same")
    _insert_user(avatar="/uploads/same-avatar.png")

    user_service.update_avatar("test_user", "/uploads/same-avatar.png")

    assert same_file.exists()


def test_update_avatar_ignores_path_traversal_in_old_value(isolated_db, tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))

    outside_file = tmp_path / "secret.txt"
    outside_file.write_bytes(b"do-not-delete")
    _insert_user(avatar="/uploads/../secret.txt")

    user_service.update_avatar("test_user", "/uploads/new-avatar.png")

    assert outside_file.exists()
