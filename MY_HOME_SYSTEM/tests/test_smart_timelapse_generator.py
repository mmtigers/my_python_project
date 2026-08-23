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
import textwrap
import threading
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


class TestMotionDetectorStderrDeadlock:
    """M-4-3残り: MotionDetector.detect()がffmpegをstdout=PIPE, stderr=PIPEで起動しつつ
    stdoutのフレーム読み取りループ中はstderrを読まないため、ffmpegがstderrへ大量出力すると
    パイプバッファが満杯になりffmpeg側がブロックし、結果としてstdout側も進まなくなり
    デッドロックする不具合。実ffmpegの代わりに、フレームをstdoutへ書きつつ途中でパイプ
    バッファを超える量をstderrへ一気に書き込む擬似プロセスで再現する。"""

    FRAME_SIZE = stg.WIDTH * stg.HEIGHT

    def _patch_popen_with_stalling_child(self, monkeypatch, total_frames=5, junk_size=5 * 1024 * 1024):
        script = textwrap.dedent(f"""
            import sys
            frame = b'\\x00' * {self.FRAME_SIZE}
            for i in range({total_frames}):
                if i == 1:
                    sys.stderr.buffer.write(b'e' * {junk_size})
                    sys.stderr.buffer.flush()
                sys.stdout.buffer.write(frame)
                sys.stdout.buffer.flush()
            sys.exit(0)
        """)

        real_popen = subprocess.Popen

        def fake_popen(cmd, stdout=None, stderr=None, **kwargs):
            # 実ffmpegの代わりに、パイプの挙動だけを模した擬似プロセスを起動する
            return real_popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        monkeypatch.setattr(stg.subprocess, "Popen", fake_popen)

    def test_detect_does_not_deadlock_when_ffmpeg_stderr_pipe_fills_up(self, monkeypatch, tmp_path):
        self._patch_popen_with_stalling_child(monkeypatch)
        detector = stg.MotionDetector()

        result = {}

        def run():
            try:
                result["records"] = detector.detect(
                    input_path="dummy.mp4", work_dir=str(tmp_path), duration_sec=0
                )
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=15)

        assert not t.is_alive(), (
            "MotionDetector.detect()がffmpegのstderrパイプ満杯でデッドロックした"
            "(stderrを読まずに大量出力を書かせるとstdout側も停止する)"
        )
        assert "error" not in result, f"detect()が例外で終了した: {result.get('error')}"
