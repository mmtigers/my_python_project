# MY_HOME_SYSTEM/tests/test_quest_router_api.py
"""
routers/quest_router.py をTestClient経由でエンドツーエンドにテストする。

既存の test_quest_service.py / test_quest_authorization.py はサービス層
(QuestService等)を直接呼び出しているが、本ファイルはHTTP層(Pydanticの
リクエストバリデーション、ステータスコード、レスポンススキーマ)を対象にする。
"""
import io
import os
import shutil
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config


def _seed_basic_data():
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 100), ('daughter', 'Daughter', 'Novice', 1, 0, 10)"
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
            "(101, 'TestQuest', 'daily', 10, 5)"
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (201, 'TestReward', 50)"
        )


@pytest.fixture
def seeded_client(isolated_db, api_client):
    _seed_basic_data()
    return api_client


class TestCompleteQuestValidation:
    def test_missing_user_id_returns_422(self, seeded_client):
        res = seeded_client.post("/api/quest/complete", json={"quest_id": 101})
        assert res.status_code == 422

    def test_quest_id_as_string_returns_422(self, seeded_client):
        res = seeded_client.post("/api/quest/complete", json={"user_id": "dad", "quest_id": "not-a-number"})
        assert res.status_code == 422

    def test_extra_unexpected_type_object_for_user_id_returns_422(self, seeded_client):
        res = seeded_client.post("/api/quest/complete", json={"user_id": {"nested": "object"}, "quest_id": 101})
        assert res.status_code == 422

    def test_nonexistent_quest_id_returns_404(self, seeded_client):
        res = seeded_client.post("/api/quest/complete", json={"user_id": "dad", "quest_id": 999999})
        assert res.status_code == 404

    def test_valid_completion_succeeds(self, seeded_client):
        res = seeded_client.post("/api/quest/complete", json={"user_id": "dad", "quest_id": 101})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "success"
        assert body["earnedGold"] == 5
        assert body["earnedExp"] == 10


class TestApproveRejectAuthorizationOverHttp:
    """
    サービス層のテスト(test_quest_authorization.py)はQuestServiceを直接呼び出しているが、
    ここではPydanticスキーマを経由した実際のHTTPリクエストとして同じ認可ルールを確認する。
    """

    def _create_pending_history(self, seeded_client):
        res = seeded_client.post("/api/quest/complete", json={"user_id": "daughter", "quest_id": 101})
        assert res.status_code == 200
        assert res.json()["status"] == "pending"
        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT id FROM quest_history WHERE user_id='daughter' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["id"]

    def test_non_parent_cannot_approve_via_http(self, seeded_client):
        history_id = self._create_pending_history(seeded_client)
        res = seeded_client.post(
            "/api/quest/approve", json={"approver_id": "daughter", "history_id": history_id}
        )
        assert res.status_code == 403

    def test_parent_can_approve_via_http(self, seeded_client):
        history_id = self._create_pending_history(seeded_client)
        res = seeded_client.post("/api/quest/approve", json={"approver_id": "dad", "history_id": history_id})
        assert res.status_code == 200
        assert res.json()["status"] == "success"

    def test_missing_history_id_returns_422(self, seeded_client):
        res = seeded_client.post("/api/quest/approve", json={"approver_id": "dad"})
        assert res.status_code == 422


class TestPurchaseReward:
    def test_insufficient_gold_returns_400(self, seeded_client):
        res = seeded_client.post("/api/quest/reward/purchase", json={"user_id": "daughter", "reward_id": 201})
        assert res.status_code == 400

    def test_sufficient_gold_succeeds_and_deducts(self, seeded_client):
        res = seeded_client.post("/api/quest/reward/purchase", json={"user_id": "dad", "reward_id": 201})
        assert res.status_code == 200
        assert res.json()["newGold"] == 50

    def test_nonexistent_reward_id_returns_404_or_400(self, seeded_client):
        res = seeded_client.post("/api/quest/reward/purchase", json={"user_id": "dad", "reward_id": 999999})
        assert res.status_code in (400, 404)


class TestImageUpload:
    JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
    PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8

    @pytest.fixture(autouse=True)
    def _clean_upload_dir(self, isolated_db, tmp_path, monkeypatch):
        """
        アップロード保存先を一時ディレクトリへ差し替え、テストが実プロジェクトの
        uploads/ ディレクトリを汚さないようにする。
        """
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr(config, "UPLOAD_DIR", str(upload_dir))
        yield upload_dir

    def test_valid_jpeg_upload_succeeds(self, api_client, _clean_upload_dir):
        res = api_client.post(
            "/api/quest/upload",
            files={"file": ("photo.jpg", io.BytesIO(self.JPEG_MAGIC + b"\x00" * 100), "image/jpeg")},
        )
        assert res.status_code == 200
        assert res.json()["url"].startswith("/uploads/")
        saved_files = list(_clean_upload_dir.iterdir())
        assert len(saved_files) == 1
        # 保存ファイル名はUUID化されており、元のファイル名がそのまま使われないこと
        assert saved_files[0].name != "photo.jpg"

    def test_disallowed_extension_returns_400(self, api_client, _clean_upload_dir):
        res = api_client.post(
            "/api/quest/upload",
            files={"file": ("payload.exe", io.BytesIO(self.JPEG_MAGIC), "application/octet-stream")},
        )
        assert res.status_code == 400
        assert list(_clean_upload_dir.iterdir()) == []

    def test_extension_spoofed_content_is_rejected_by_magic_byte_check(self, api_client, _clean_upload_dir):
        """拡張子は.jpgだが中身が画像ではない(マジックバイト不一致)場合は拒否されること"""
        res = api_client.post(
            "/api/quest/upload",
            files={"file": ("fake.jpg", io.BytesIO(b"this is not an image, just text"), "image/jpeg")},
        )
        assert res.status_code == 400
        assert list(_clean_upload_dir.iterdir()) == []

    def test_path_traversal_filename_does_not_escape_upload_dir(self, api_client, _clean_upload_dir):
        """
        ファイル名に ../ を含めても、保存名はUUIDで生成されるため
        UPLOAD_DIR の外側には書き込まれないこと。
        """
        res = api_client.post(
            "/api/quest/upload",
            files={"file": ("../../../../etc/evil.png", io.BytesIO(self.PNG_MAGIC), "image/png")},
        )
        assert res.status_code == 200
        saved_files = list(_clean_upload_dir.iterdir())
        assert len(saved_files) == 1
        assert saved_files[0].parent == _clean_upload_dir

    def test_valid_upload_saves_a_genuinely_decodable_image(self, api_client, _clean_upload_dir):
        """
        マジックバイトチェックを通した実データが、保存後もPillowで正しくデコードできる
        本物の画像であることを確認する(Pillowのバージョン更新に対する回帰ガード)。
        """
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (32, 16), color="blue").save(buf, format="JPEG")
        buf.seek(0)

        res = api_client.post(
            "/api/quest/upload",
            files={"file": ("avatar.jpg", buf, "image/jpeg")},
        )
        assert res.status_code == 200

        saved_files = list(_clean_upload_dir.iterdir())
        assert len(saved_files) == 1

        decoded = Image.open(saved_files[0])
        decoded.load()
        assert decoded.size == (32, 16)
        assert decoded.format == "JPEG"
