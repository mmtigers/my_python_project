# MY_HOME_SYSTEM/tests/test_smart_timelapse_generator.py
"""
monitors/smart_timelapse_generator.py のテスト (M-4-3の回帰テスト)。

VideoBuilder._build_concat の `except Exception: return False` が
エラー内容を一切ログに残さず握りつぶしていたため、結合失敗の原因が
サーバーログから追跡できなかった不具合。
"""
import os
import subprocess
import sys
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import smart_timelapse_generator as stg


class TestBuildConcatErrorLogging:
    def test_ffmpeg_failure_logs_error_before_returning_false(self, tmp_path):
        builder = stg.VideoBuilder()
        clip_files = [str(tmp_path / "Event001.mp4")]

        err = subprocess.CalledProcessError(1, ["ffmpeg"])
        with patch.object(stg.subprocess, "run", side_effect=err), \
             patch.object(stg, "logger") as mock_logger:
            result = builder._build_concat(clip_files, str(tmp_path / "out.mp4"), str(tmp_path))

        assert result is False
        mock_logger.error.assert_called_once()
        logged_msg = mock_logger.error.call_args[0][0]
        assert "結合" in logged_msg, (
            f"FFmpeg結合失敗の詳細がログに残っていない(エラーが握りつぶされている): {logged_msg!r}"
        )

    def test_ffmpeg_timeout_logs_error_before_returning_false(self, tmp_path):
        builder = stg.VideoBuilder()
        clip_files = [str(tmp_path / "Event001.mp4")]

        with patch.object(stg.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 3600)), \
             patch.object(stg, "logger") as mock_logger:
            result = builder._build_concat(clip_files, str(tmp_path / "out.mp4"), str(tmp_path))

        assert result is False
        mock_logger.error.assert_called_once()
        assert "タイムアウト" in mock_logger.error.call_args[0][0]

    def test_ffmpeg_success_returns_true(self, tmp_path):
        builder = stg.VideoBuilder()
        clip_files = [str(tmp_path / "Event001.mp4")]

        with patch.object(stg.subprocess, "run", return_value=None):
            result = builder._build_concat(clip_files, str(tmp_path / "out.mp4"), str(tmp_path))

        assert result is True
