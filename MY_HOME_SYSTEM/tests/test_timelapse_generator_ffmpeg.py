# MY_HOME_SYSTEM/tests/test_timelapse_generator_ffmpeg.py
"""
monitors/timelapse_generator.py のFFmpeg呼び出しのテスト (M-4-3の回帰テスト)。

concat/split処理のsubprocess.run呼び出しにcheck/timeoutが無かったため、
FFmpegが失敗またはハングしても検知できず、失敗した(または存在しない)
出力ファイルパスをそのまま後続処理(Discordアップロード等)に渡していた。
"""
import datetime
import os
import subprocess
import sys
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from monitors import timelapse_generator as tg


class TestRunFfmpegSimple:
    def test_success_returns_true(self):
        with patch.object(tg.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stderr="")
            assert tg.run_ffmpeg_simple(["ffmpeg", "-version"]) is True

    def test_nonzero_returncode_logs_error_and_returns_false(self):
        with patch.object(tg.subprocess, "run") as mock_run, \
             patch.object(tg, "logger") as mock_logger:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stderr="broken pipe")
            assert tg.run_ffmpeg_simple(["ffmpeg", "-bad"]) is False
        mock_logger.error.assert_called_once()

    def test_timeout_logs_error_and_returns_false(self):
        with patch.object(tg.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 300)), \
             patch.object(tg, "logger") as mock_logger:
            assert tg.run_ffmpeg_simple(["ffmpeg", "-hang"]) is False
        mock_logger.error.assert_called_once()
        assert "タイムアウト" in mock_logger.error.call_args[0][0]


class TestProcessVideoClipsConcatFailure:
    def test_concat_failure_returns_empty_string(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path))
        cam_dir = tmp_path / "garden"
        cam_dir.mkdir()
        (cam_dir / "20260101_080000.mp4").write_bytes(b"dummy")

        event_time = datetime.datetime(2026, 1, 1, 8, 0, 30)

        with patch.object(tg, "extract_video_clip", return_value=True), \
             patch.object(tg, "run_ffmpeg_simple", return_value=False) as mock_run:
            result = tg.process_video_clips("garden", "garden", [event_time], str(tmp_path))

        assert result == "", "concat失敗時は空文字を返し、破損/存在しない出力パスを後続に渡してはいけない"
        mock_run.assert_called_once()

    def test_concat_success_returns_output_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path))
        cam_dir = tmp_path / "garden"
        cam_dir.mkdir()
        (cam_dir / "20260101_080000.mp4").write_bytes(b"dummy")

        event_time = datetime.datetime(2026, 1, 1, 8, 0, 30)

        with patch.object(tg, "extract_video_clip", return_value=True), \
             patch.object(tg, "run_ffmpeg_simple", return_value=True):
            result = tg.process_video_clips("garden", "garden", [event_time], str(tmp_path))

        assert result == os.path.join(str(tmp_path), "garden_timelapse.mp4")


class TestUploadVideoToDiscordSplitFailure:
    def test_split_failure_does_not_attempt_to_send_files(self, tmp_path):
        big_file = tmp_path / "big.mp4"
        big_file.write_bytes(b"x" * (9 * 1024 * 1024))  # 9MB > 8MBしきい値

        with patch.object(config, "DISCORD_WEBHOOK_REPORT", "https://example.invalid/webhook", create=True), \
             patch.object(tg, "run_ffmpeg_simple", return_value=False), \
             patch.object(tg.requests, "post") as mock_post:
            tg.upload_video_to_discord(str(big_file), "テスト")

        mock_post.assert_not_called()
