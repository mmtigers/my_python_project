# MY_HOME_SYSTEM/tests/test_quest_master_sync_marker.py
"""services/quest/master_sync_marker.py のテスト(Issue #700)。

マスタ定義ソース(quest_data.py / routine_data.py)の差分判定は、デプロイ経路から
`sync_strict.py --if-stale` が **破壊的な同期(DELETE ... NOT IN を含む)を走らせるか
どうか**の唯一の判断材料になる。ここでは「差分が無いのに同期してしまう」「差分が
あるのに同期しない」のどちらも起きないことを固定する。
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services.quest import master_sync_marker


def _write_sources(base_dir, quest_body: str = "QUESTS = []", routine_body: str = "FLOWS = []"):
    """ダイジェスト対象の2ファイルを tmp_path 配下に用意する。"""
    (base_dir / "quest_data.py").write_text(quest_body, encoding="utf-8")
    (base_dir / "routine_data.py").write_text(routine_body, encoding="utf-8")


class TestComputeMasterDigest:
    def test_digest_is_stable_for_identical_content(self, tmp_path):
        _write_sources(tmp_path)
        first = master_sync_marker.compute_master_digest(str(tmp_path))
        second = master_sync_marker.compute_master_digest(str(tmp_path))
        assert first == second

    def test_digest_changes_when_quest_data_changes(self, tmp_path):
        _write_sources(tmp_path)
        before = master_sync_marker.compute_master_digest(str(tmp_path))
        _write_sources(tmp_path, quest_body="QUESTS = [{'id': 1}]")
        assert master_sync_marker.compute_master_digest(str(tmp_path)) != before

    def test_digest_changes_when_routine_data_changes(self, tmp_path):
        """退役クエストの報酬の移設先(routine_data.py)の変更も同期のトリガーになること。"""
        _write_sources(tmp_path)
        before = master_sync_marker.compute_master_digest(str(tmp_path))
        _write_sources(tmp_path, routine_body="FLOWS = [{'key': 'am'}]")
        assert master_sync_marker.compute_master_digest(str(tmp_path)) != before

    def test_digest_distinguishes_content_moved_between_files(self, tmp_path):
        """ファイル境界を跨いで内容が入れ替わっただけのケースを同一視しないこと
        (単純な連結だと衝突する)。"""
        _write_sources(tmp_path, quest_body="AB", routine_body="C")
        first = master_sync_marker.compute_master_digest(str(tmp_path))
        _write_sources(tmp_path, quest_body="A", routine_body="BC")
        assert master_sync_marker.compute_master_digest(str(tmp_path)) != first

    def test_missing_source_raises_oserror(self, tmp_path):
        """判定不能を黙って「差分なし」に倒さないこと(呼び出し側が同期をスキップする)。"""
        (tmp_path / "quest_data.py").write_text("QUESTS = []", encoding="utf-8")
        with pytest.raises(OSError):
            master_sync_marker.compute_master_digest(str(tmp_path))

    def test_defaults_to_config_base_dir(self, monkeypatch, tmp_path):
        _write_sources(tmp_path)
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
        assert (
            master_sync_marker.compute_master_digest()
            == master_sync_marker.compute_master_digest(str(tmp_path))
        )


class TestMarkerRoundTrip:
    def test_marker_path_is_under_log_dir(self, monkeypatch, tmp_path):
        """マーカーは health_watch の各マーカーと同じく logs/ 配下(gitignore済み)に置くこと。"""
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path), raising=False)
        assert master_sync_marker.get_marker_path() == str(tmp_path / ".quest_master_sync_marker")

    def test_read_returns_none_when_marker_absent(self, tmp_path):
        assert master_sync_marker.read_recorded_digest(str(tmp_path / "absent")) is None

    def test_write_then_read_round_trips(self, tmp_path):
        marker = str(tmp_path / "marker")
        assert master_sync_marker.write_recorded_digest("deadbeef", marker_path=marker) is True
        assert master_sync_marker.read_recorded_digest(marker) == "deadbeef"

    def test_empty_marker_is_treated_as_absent(self, tmp_path):
        marker = tmp_path / "marker"
        marker.write_text("", encoding="utf-8")
        assert master_sync_marker.read_recorded_digest(str(marker)) is None


class TestIsStale:
    def test_stale_on_first_run_without_marker(self, tmp_path):
        _write_sources(tmp_path)
        stale, digest = master_sync_marker.is_stale(
            base_dir=str(tmp_path), marker_path=str(tmp_path / "marker")
        )
        assert stale is True
        assert digest

    def test_not_stale_after_recording_current_digest(self, tmp_path):
        _write_sources(tmp_path)
        marker = str(tmp_path / "marker")
        _, digest = master_sync_marker.is_stale(base_dir=str(tmp_path), marker_path=marker)
        master_sync_marker.write_recorded_digest(digest, marker_path=marker)

        stale, again = master_sync_marker.is_stale(base_dir=str(tmp_path), marker_path=marker)
        assert stale is False
        assert again == digest

    def test_stale_again_after_source_edit(self, tmp_path):
        _write_sources(tmp_path)
        marker = str(tmp_path / "marker")
        _, digest = master_sync_marker.is_stale(base_dir=str(tmp_path), marker_path=marker)
        master_sync_marker.write_recorded_digest(digest, marker_path=marker)

        _write_sources(tmp_path, quest_body="QUESTS = [{'id': 1023}]")
        stale, new_digest = master_sync_marker.is_stale(base_dir=str(tmp_path), marker_path=marker)
        assert stale is True
        assert new_digest != digest
