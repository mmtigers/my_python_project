# MY_HOME_SYSTEM/tests/test_scheduled_timelapse.py
"""
monitors/scheduled_timelapse.py のテスト (M-4-2の回帰テスト)。

FFmpeg(generate_timelapse)が失敗しても実行済みマーカー(.doneファイル)が
touchされてしまい、当日中の再試行ができなくなる不具合の回帰テスト。
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import scheduled_timelapse as st


def _make_args(**overrides):
    defaults = dict(force=None, date=None, start=None, end=None, cameras=None, dry_run=False)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _record_file(record_dir: str, date_str: str, camera: str, schedule: str) -> str:
    return os.path.join(record_dir, f"{date_str}_{camera}_{schedule}.done")


class _FrozenDateTime(st.datetime):
    """スケジュールのトリガー時刻窓(8:30-9:00)内に固定した datetime.now()"""
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 1, 8, 40, 0)


class TestScheduledTimelapseDoneMarker:
    def _patch_common(self, monkeypatch, tmp_path):
        monkeypatch.setattr(st, "RECORD_DIR", str(tmp_path))
        monkeypatch.setattr(st, "TARGET_CAMERAS", {"entrance": "/dummy/entrance"})
        monkeypatch.setattr(st, "SCHEDULES", {
            "morning": (st.time(7, 50), st.time(8, 30), st.time(8, 30), st.time(9, 0))
        })
        monkeypatch.setattr(st, "datetime", _FrozenDateTime)

    def test_ffmpeg_failure_does_not_mark_done(self, tmp_path, monkeypatch):
        self._patch_common(monkeypatch, tmp_path)

        with patch.object(st, "get_target_files", return_value=["/dummy/entrance/20260101_080000.mp4"]), \
             patch.object(st, "generate_timelapse", return_value=[]), \
             patch.object(st, "cleanup_old_records"), \
             patch.object(st, "cleanup_orphaned_videos"), \
             patch.object(st, "notify_error") as mock_notify:
            st.main(_make_args())

        record_file = _record_file(str(tmp_path), "20260101", "entrance", "morning")
        assert not os.path.exists(record_file), (
            "FFmpeg失敗時にもdoneマーカーがtouchされ、当日中の再試行ができなくなっている"
        )
        mock_notify.assert_called_once()

    def test_ffmpeg_success_marks_done(self, tmp_path, monkeypatch):
        self._patch_common(monkeypatch, tmp_path)

        dummy_part = tmp_path / "part1.mp4"
        dummy_part.write_bytes(b"dummy-video-data")

        with patch.object(st, "get_target_files", return_value=["/dummy/entrance/20260101_080000.mp4"]), \
             patch.object(st, "generate_timelapse", return_value=[str(dummy_part)]), \
             patch.object(st, "cleanup_old_records"), \
             patch.object(st, "cleanup_orphaned_videos"), \
             patch.object(st, "send_push", return_value=True), \
             patch.object(st, "notify_error") as mock_notify:
            st.main(_make_args())

        record_file = _record_file(str(tmp_path), "20260101", "entrance", "morning")
        assert os.path.exists(record_file)
        mock_notify.assert_not_called()
        # 容量節約のため生成した一時ファイルは削除される
        assert not dummy_part.exists()

    def test_no_target_files_still_marks_done_to_avoid_reprocessing(self, tmp_path, monkeypatch):
        """対象期間に動画ファイルが無い場合は、FFmpeg失敗とは異なりデータ自体が
        存在しないため再試行しても無駄。従来通りdoneマーカーをtouchする。"""
        self._patch_common(monkeypatch, tmp_path)

        with patch.object(st, "get_target_files", return_value=[]), \
             patch.object(st, "cleanup_old_records"), \
             patch.object(st, "cleanup_orphaned_videos"):
            st.main(_make_args())

        record_file = _record_file(str(tmp_path), "20260101", "entrance", "morning")
        assert os.path.exists(record_file)
