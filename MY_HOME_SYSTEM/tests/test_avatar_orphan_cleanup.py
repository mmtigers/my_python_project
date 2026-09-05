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


def test_update_avatar_keeps_file_still_referenced_by_another_user(isolated_db, tmp_path, monkeypatch):
    """#372: 他ユーザーが同じアップロード画像を参照している間は物理削除しない。"""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))
    shared = upload_dir / "shared-avatar.png"
    shared.write_bytes(b"png")
    _insert_user(user_id="dad", avatar="/uploads/shared-avatar.png")
    _insert_user(user_id="mom", avatar="/uploads/shared-avatar.png")

    user_service.update_avatar("mom", "🙂")

    assert shared.exists(), "dad が参照中のファイルが削除された"

    # dad も別の画像に切り替えれば、参照が無くなるので削除される
    user_service.update_avatar("dad", "🙂")
    assert not shared.exists()


class TestDeleteUnlinkedAvatar:
    """#442: AvatarUploader.tsxの2段階アップロードのうち2段階目(ユーザーへの紐付け)が
    失敗した際、1段階目でアップロード済みの画像をロールバック削除するための
    UserService.delete_unlinked_avatarの回帰テスト。"""

    def test_deletes_a_file_not_referenced_by_any_user(self, isolated_db, tmp_path, monkeypatch):
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))

        orphan = upload_dir / "orphan.png"
        orphan.write_bytes(b"orphan")

        assert user_service.delete_unlinked_avatar("orphan.png") is True
        assert not orphan.exists()

    def test_does_not_delete_a_file_still_referenced_by_a_user(self, isolated_db, tmp_path, monkeypatch):
        """他のリクエストが先に紐付けに成功していた場合、誤って現役のアバターを
        消してしまわないこと。"""
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))

        in_use = upload_dir / "in-use.png"
        in_use.write_bytes(b"in-use")
        _insert_user(avatar="/uploads/in-use.png")

        assert user_service.delete_unlinked_avatar("in-use.png") is False
        assert in_use.exists()

    def test_returns_false_for_a_nonexistent_file(self, isolated_db, tmp_path, monkeypatch):
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))

        assert user_service.delete_unlinked_avatar("does-not-exist.png") is False

    def test_ignores_path_traversal_attempts(self, isolated_db, tmp_path, monkeypatch):
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))

        outside_file = tmp_path / "secret.txt"
        outside_file.write_bytes(b"do-not-delete")

        assert user_service.delete_unlinked_avatar("../secret.txt") is False
        assert outside_file.exists()
