# MY_HOME_SYSTEM/tests/test_smart_timelapse_generator.py
"""
monitors/smart_timelapse_generator.py のテスト (M-4-3の回帰テスト)。

VideoBuilder._build_concat の `except Exception: return False` が
エラー内容を一切ログに残さず握りつぶしていたため、結合失敗の原因が
サーバーログから追跡できなかった不具合。
"""
import datetime
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestSendPushUsesKeywordArguments:
    """Issue #167の回帰テスト: _run_smart_timelapse_job_locked内の2箇所の
    send_push呼び出しが send_push(user_id, messages, "discord", "report"/"error")
    という位置引数になっていた。当時の実シグネチャは
    send_push(user_id, messages, image_data=None, target="both", channel="notify", ...)
    のため、"discord" が image_data に、"report"/"error" が target に渡ってしまい、
    target が discord/line/both のいずれにも一致せずどこにも送信されないまま
    True が返っていた(沈黙的な通知喪失)。send_pushをモックし、target/channelが
    キーワード引数として渡されていることを検証する。

    Issue #289でsend_pushのシグネチャ自体をmessages以外キーワード専用に
    再設計した後は、messagesのみが唯一の位置引数となる(target=discordの
    ためuser_idも不要になった)。"""

    def _patch_no_motion_pipeline(self, monkeypatch):
        monkeypatch.setattr(stg, "check_dependencies", lambda: True)
        monkeypatch.setattr(stg, "setup_directories", lambda: ("work", "out", "rec"))
        monkeypatch.setattr(stg, "get_video_info", lambda path, retries=3: {"format": {"duration": "10"}})
        monkeypatch.setattr(
            stg, "get_video_start_dt",
            lambda path, info: datetime.datetime(2026, 8, 30, 12, 0, 0)
        )
        monkeypatch.setattr(stg, "get_ffmpeg_version", lambda: "test")
        monkeypatch.setattr(
            stg, "MotionDetector",
            lambda: type("StubMotionDetector", (), {"detect": lambda self, *a, **k: []})()
        )
        # eventsを空にすることで「動きなし」通知の分岐を通す
        monkeypatch.setattr(
            stg, "EventBuilder",
            lambda: type("StubEventBuilder", (), {"build": lambda self, *a, **k: []})()
        )

    def test_no_motion_notification_uses_keyword_target_and_channel(self, monkeypatch, tmp_path):
        self._patch_no_motion_pipeline(monkeypatch)
        mock_send_push = MagicMock(return_value=True)
        monkeypatch.setattr(stg, "send_push", mock_send_push)

        stg._run_smart_timelapse_job_locked(str(tmp_path / "input.mp4"))

        mock_send_push.assert_called_once()
        args, kwargs = mock_send_push.call_args
        # 修正前は positional (user_id, messages, "discord", "report") の4引数呼び出しで、
        # kwargsにtarget/channelが含まれていなかった。
        assert len(args) == 1, "messages以外は必ずキーワード引数で渡すこと"
        assert kwargs.get("target") == "discord"
        assert kwargs.get("channel") == "report"

    def test_exception_notification_uses_keyword_target_and_channel(self, monkeypatch, tmp_path):
        monkeypatch.setattr(stg, "check_dependencies", lambda: True)
        monkeypatch.setattr(stg, "setup_directories", lambda: ("work", "out", "rec"))

        def _raise(*args, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(stg, "get_video_info", _raise)
        mock_send_push = MagicMock(return_value=True)
        monkeypatch.setattr(stg, "send_push", mock_send_push)

        stg._run_smart_timelapse_job_locked(str(tmp_path / "input.mp4"))

        mock_send_push.assert_called_once()
        args, kwargs = mock_send_push.call_args
        assert len(args) == 1, "messages以外は必ずキーワード引数で渡すこと"
        assert kwargs.get("target") == "discord"
        assert kwargs.get("channel") == "error"


class TestVideoBuildFailureNotifiesUser:
    """Issue #233の回帰テスト: VideoBuilder().build()が例外を出さずFalseを返した場合、
    以前はif分岐にもelseにも入らず関数がそのまま正常終了し、動き検知イベントが
    あったにもかかわらず通知が一切送られなかった不具合。"""

    def _patch_pipeline_with_events(self, monkeypatch, build_return_value):
        monkeypatch.setattr(stg, "check_dependencies", lambda: True)
        monkeypatch.setattr(stg, "setup_directories", lambda: ("work", "out", "rec"))
        monkeypatch.setattr(stg, "get_video_info", lambda path, retries=3: {"format": {"duration": "10"}})
        monkeypatch.setattr(
            stg, "get_video_start_dt",
            lambda path, info: datetime.datetime(2026, 8, 30, 12, 0, 0)
        )
        monkeypatch.setattr(stg, "get_ffmpeg_version", lambda: "test")
        monkeypatch.setattr(
            stg, "MotionDetector",
            lambda: type("StubMotionDetector", (), {"detect": lambda self, *a, **k: []})()
        )
        fake_event = type("FakeEvent", (), {"duration": 5.0})()
        monkeypatch.setattr(
            stg, "EventBuilder",
            lambda: type("StubEventBuilder", (), {"build": lambda self, *a, **k: [fake_event]})()
        )
        monkeypatch.setattr(
            stg, "VideoBuilder",
            lambda: type("StubVideoBuilder", (), {"build": lambda self, *a, **k: build_return_value})()
        )
        monkeypatch.setattr(stg, "mark_as_done", MagicMock())

    def test_build_failure_sends_error_notification(self, monkeypatch, tmp_path):
        self._patch_pipeline_with_events(monkeypatch, build_return_value=False)
        mock_send_push = MagicMock(return_value=True)
        monkeypatch.setattr(stg, "send_push", mock_send_push)
        mock_uploader_send = MagicMock()
        monkeypatch.setattr(
            stg, "Uploader",
            lambda: type("StubUploader", (), {"split_and_send": mock_uploader_send})()
        )

        stg._run_smart_timelapse_job_locked(str(tmp_path / "input.mp4"))

        mock_send_push.assert_called_once()
        args, kwargs = mock_send_push.call_args
        assert kwargs.get("target") == "discord"
        assert kwargs.get("channel") == "error"
        mock_uploader_send.assert_not_called()

    def test_build_success_still_uploads_and_does_not_send_error(self, monkeypatch, tmp_path):
        self._patch_pipeline_with_events(monkeypatch, build_return_value=True)
        mock_send_push = MagicMock(return_value=True)
        monkeypatch.setattr(stg, "send_push", mock_send_push)
        mock_uploader_send = MagicMock()
        monkeypatch.setattr(
            stg, "Uploader",
            lambda: type("StubUploader", (), {"split_and_send": mock_uploader_send})()
        )
        monkeypatch.setattr(stg.os.path, "getsize", lambda path: 1234)

        stg._run_smart_timelapse_job_locked(str(tmp_path / "input.mp4"))

        mock_send_push.assert_not_called()
        mock_uploader_send.assert_called_once()


class TestSplitAndSendCleansUpPartFiles:
    """Issue #171の回帰テスト: Uploader.split_and_sendが、Discord送信用に生成した
    分割ファイル(*_part_*.mp4)を送信後も削除せず、元動画(summary.output_path)とは
    別にローカルディスクへ重複して残り続けていた不具合。"""

    def _setup_split_pipeline(self, monkeypatch, tmp_path):
        output_path = tmp_path / "20260830_summary.mp4"
        output_path.write_bytes(b"x" * 100)

        summary = stg.SummaryInfo(target_date="2026-08-30")
        summary.output_path = str(output_path)

        # split_and_send は冒頭で summary.file_size_bytes を os.path.getsize で
        # 上書きするため、実ファイルサイズを大きくする代わりに閾値
        # (MAX_FILE_SIZE_BYTES)を実ファイルサイズ未満に下げて分割分岐を強制する。
        monkeypatch.setattr(stg, "MAX_FILE_SIZE_BYTES", 10)
        monkeypatch.setattr(stg.config, "DISCORD_WEBHOOK_URL", "https://discord.example.com/webhook")
        monkeypatch.setattr(stg.shutil, "which", lambda name: None)  # ioniceなし分岐で単純化

        part_files = [
            tmp_path / "20260830_summary_part_000.mp4",
            tmp_path / "20260830_summary_part_001.mp4",
        ]

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return subprocess.CompletedProcess(cmd, 0, stdout="120.0\n", stderr="")
            # ffmpeg分割コマンドの代わりに、実際にダミーの分割ファイルを生成する
            for p in part_files:
                p.write_bytes(b"y" * 10)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(stg.subprocess, "run", fake_run)
        # C-L2 (Issue #414): split_and_send の time.sleep(5)(APIレートリミット対策)を
        # 分割パートごとに実時間で待たない
        monkeypatch.setattr(stg.time, "sleep", lambda seconds: None)

        return summary, part_files

    def test_part_files_are_deleted_after_successful_send(self, monkeypatch, tmp_path):
        summary, part_files = self._setup_split_pipeline(monkeypatch, tmp_path)

        uploader = stg.Uploader()
        monkeypatch.setattr(uploader, "_send_to_discord", MagicMock())
        monkeypatch.setattr(uploader, "_send_completion_notice", MagicMock())

        uploader.split_and_send(summary, "20260830_summary.mp4")

        for p in part_files:
            assert not p.exists(), f"分割ファイルが送信成功後も削除されていない: {p}"
        # 元動画自体はここでは削除されない(リテンションクリーンアップに委ねる)
        assert Path(summary.output_path).exists()

    def test_part_files_are_deleted_even_when_send_fails(self, monkeypatch, tmp_path):
        summary, part_files = self._setup_split_pipeline(monkeypatch, tmp_path)

        uploader = stg.Uploader()
        monkeypatch.setattr(uploader, "_send_to_discord", MagicMock(side_effect=Exception("network down")))
        monkeypatch.setattr(uploader, "_send_completion_notice", MagicMock())

        uploader.split_and_send(summary, "20260830_summary.mp4")  # 例外は内部でログに握りつぶされる

        for p in part_files:
            assert not p.exists(), f"送信失敗時も分割ファイルは削除されるべき: {p}"
