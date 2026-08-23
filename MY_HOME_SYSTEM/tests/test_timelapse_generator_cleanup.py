# MY_HOME_SYSTEM/tests/test_timelapse_generator_cleanup.py
"""
monitors/timelapse_generator.py の一時ディレクトリクリーンアップのテスト (M-4-4の回帰テスト)。

TMP_VIDEO_DIR配下を glob("*") + os.remove() で全消ししていたため、
何らかの理由でディレクトリが混入しているとIsADirectoryErrorでクラッシュしていた。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import timelapse_generator as tg


class TestCleanupTmpVideoDir:
    def test_removes_files_and_stray_directories_without_crashing(self, tmp_path):
        file_path = tmp_path / "clip.ts"
        file_path.write_bytes(b"dummy")

        stray_dir = tmp_path / "stray_subdir"
        stray_dir.mkdir()
        (stray_dir / "inner.txt").write_text("dummy")

        tg.cleanup_tmp_video_dir(str(tmp_path))

        assert not file_path.exists()
        assert not stray_dir.exists()

    def test_missing_directory_does_not_raise(self, tmp_path):
        missing_dir = str(tmp_path / "does_not_exist")
        tg.cleanup_tmp_video_dir(missing_dir)
