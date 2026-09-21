# MY_HOME_SYSTEM/tests/test_daily_timelapse_job.py
"""
monitors/daily_timelapse_job.py の run_daily_timelapse() のテスト (Issue #233の回帰テスト)。

all_clip_files は各イベントの _build_clip() 成功時のみ追加される実装のため、
「イベント検知はあったがクリップ抽出が全滅した」場合と「そもそも動き検知イベントが
無かった」場合の両方で all_clip_files が空になり区別できず、前者でも「動きは
ありませんでした」という事実と異なる通知を送っていた不具合。
"""
import datetime
import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import daily_timelapse_job as djt


class _FakeEvent:
    def __init__(self, duration=5.0):
        self.duration = duration
        self.event_id = None


class _FakeLock:
    def __enter__(self):
        return True

    def __exit__(self, *exc):
        return False


class TestRunDailyTimelapseAllClipsFailedVsNoEvents:
    def _patch_common(self, monkeypatch, tmp_path, events_per_chunk):
        camera_name = "entrance"
        nvr_base_dir = getattr(djt.config, "NVR_RECORD_DIR", "/mnt/nas/home_system/nvr_recordings")
        nvr_dir = os.path.join(nvr_base_dir, camera_name)
        chunk_file = os.path.join(nvr_dir, "20260830_060000.mp4")

        monkeypatch.setattr(djt, "check_dependencies", lambda: True)

        real_exists = os.path.exists

        def fake_exists(path):
            if path == nvr_dir:
                return True
            return real_exists(path) if str(tmp_path) in str(path) else False

        monkeypatch.setattr(djt.os.path, "exists", fake_exists)
        monkeypatch.setattr(djt.glob, "glob", lambda pattern: [chunk_file])
        monkeypatch.setattr(djt, "timelapse_job_lock", lambda: _FakeLock())
        monkeypatch.setattr(djt, "setup_directories", lambda: (str(tmp_path), str(tmp_path), str(tmp_path)))
        monkeypatch.setattr(djt, "get_ffmpeg_version", lambda: "test")
        monkeypatch.setattr(djt, "get_video_info", lambda path, retries=3: {"format": {"duration": "10"}})
        monkeypatch.setattr(
            djt, "get_video_start_dt",
            lambda path, info: datetime.datetime(2026, 8, 30, 6, 0, 0)
        )
        monkeypatch.setattr(
            djt, "MotionDetector",
            lambda: type("StubMotionDetector", (), {"detect": lambda self, *a, **k: []})()
        )
        monkeypatch.setattr(
            djt, "EventBuilder",
            lambda: type("StubEventBuilder", (), {
                "build": lambda self, *a, **k: [_FakeEvent() for _ in range(events_per_chunk)]
            })()
        )

        mock_send_push = MagicMock(return_value=True)
        monkeypatch.setattr(djt, "send_push", mock_send_push)
        return mock_send_push

    def test_events_detected_but_all_clip_extraction_failed_sends_error_not_no_motion(
        self, monkeypatch, tmp_path
    ):
        mock_send_push = self._patch_common(monkeypatch, tmp_path, events_per_chunk=1)
        monkeypatch.setattr(
            djt, "VideoBuilder",
            lambda: type("StubVideoBuilder", (), {
                "_build_clip": lambda self, *a, **k: None,
                "_build_concat": MagicMock(),
            })()
        )

        djt.run_daily_timelapse("entrance", target_date_str="2026-08-30")

        mock_send_push.assert_called_once()
        _, kwargs = mock_send_push.call_args
        assert kwargs.get("channel") == "error", (
            "イベント検知はあったがクリップ抽出が全滅した場合はerrorチャンネルに通知すべき"
        )
        text = kwargs["messages"][0]["text"]
        assert "動きはありませんでした" not in text, (
            "動き検知イベント自体はあったのに「動きなし」と誤った通知をしてはならない"
        )

    def test_no_events_detected_at_all_sends_no_motion_report(self, monkeypatch, tmp_path):
        mock_send_push = self._patch_common(monkeypatch, tmp_path, events_per_chunk=0)
        monkeypatch.setattr(
            djt, "VideoBuilder",
            lambda: type("StubVideoBuilder", (), {
                "_build_clip": lambda self, *a, **k: None,
                "_build_concat": MagicMock(),
            })()
        )

        djt.run_daily_timelapse("entrance", target_date_str="2026-08-30")

        mock_send_push.assert_called_once()
        _, kwargs = mock_send_push.call_args
        assert kwargs.get("channel") == "report"
        assert "動きはありませんでした" in kwargs["messages"][0]["text"]


class TestParseTime:
    """`--start` / `--end` に渡す時刻のパース。

    不正な書式を黙って通すと、意図しない範囲のチャンクだけでタイムラプスが
    作られ、「その時間帯は何も映っていなかった」ように見えてしまう。
    """

    def test_accepts_hhmm_with_and_without_colon(self):
        assert djt.parse_time("06:30") == datetime.time(6, 30)
        assert djt.parse_time("0630") == datetime.time(6, 30)

    def test_accepts_hhmmss(self):
        assert djt.parse_time("06:30:45") == datetime.time(6, 30, 45)

    def test_accepts_hour_only(self):
        assert djt.parse_time("06") == datetime.time(6, 0)

    def test_empty_means_unspecified(self):
        assert djt.parse_time("") is None
        assert djt.parse_time(None) is None

    def test_rejects_unsupported_format(self):
        import pytest

        for bad in ("6:3", "123", "06:30:45:12"):
            with pytest.raises(ValueError):
                djt.parse_time(bad)


class TestTimeRangeFiltering:
    """`--start`/`--end` によるチャンクの絞り込み。

    チャンクは10分ごとのファイルで、ファイル名は開始時刻しか持たない。
    そのため「05:56 開始のチャンク」は 06:00 以降にも跨っており、
    `--start 06:00` でも対象に含めないと冒頭が欠ける。この重なり判定を固定する。
    """

    def _run_and_capture_targets(self, monkeypatch, tmp_path, chunk_names, start=None, end=None):
        camera_name = "entrance"
        nvr_base_dir = getattr(djt.config, "NVR_RECORD_DIR", "/mnt/nas/home_system/nvr_recordings")
        nvr_dir = os.path.join(nvr_base_dir, camera_name)
        chunks = [os.path.join(nvr_dir, n) for n in chunk_names]

        monkeypatch.setattr(djt, "check_dependencies", lambda: True)
        monkeypatch.setattr(djt.os.path, "exists", lambda p: p == nvr_dir)
        monkeypatch.setattr(djt.glob, "glob", lambda pattern: list(chunks))
        monkeypatch.setattr(djt, "timelapse_job_lock", lambda: _FakeLock())

        seen = {}

        def fake_setup_directories():
            # 絞り込み後にだけ到達する。ここまで来た＝対象が1件以上あった。
            seen["reached"] = True
            return (str(tmp_path), str(tmp_path), str(tmp_path))

        monkeypatch.setattr(djt, "setup_directories", fake_setup_directories)
        monkeypatch.setattr(djt, "get_ffmpeg_version", lambda: "test")
        # 以降の重い処理には入らないよう、動画情報の取得で止める
        monkeypatch.setattr(djt, "get_video_info", lambda path, retries=3: seen.setdefault("files", []).append(path) or None)
        monkeypatch.setattr(djt, "send_push", lambda **kwargs: None)

        djt.run_daily_timelapse(camera_name, "2026-08-30", start, end)
        return seen

    def test_includes_chunk_that_starts_before_but_overlaps_the_range(self, monkeypatch, tmp_path):
        seen = self._run_and_capture_targets(
            monkeypatch, tmp_path, ["20260830_055600.mp4"], start="06:00"
        )
        assert seen.get("reached"), "06:00 に跨る 05:56 開始のチャンクが除外されている"

    def test_excludes_chunk_that_ends_before_the_range(self, monkeypatch, tmp_path):
        seen = self._run_and_capture_targets(
            monkeypatch, tmp_path, ["20260830_050000.mp4"], start="06:00"
        )
        assert not seen.get("reached")

    def test_excludes_chunk_that_starts_after_the_range(self, monkeypatch, tmp_path):
        seen = self._run_and_capture_targets(
            monkeypatch, tmp_path, ["20260830_090000.mp4"], end="07:00"
        )
        assert not seen.get("reached")

    def test_keeps_chunks_inside_the_range(self, monkeypatch, tmp_path):
        seen = self._run_and_capture_targets(
            monkeypatch, tmp_path, ["20260830_063000.mp4"], start="06:00", end="07:00"
        )
        assert seen.get("reached")

    def test_invalid_time_format_aborts_without_processing(self, monkeypatch, tmp_path):
        seen = self._run_and_capture_targets(
            monkeypatch, tmp_path, ["20260830_063000.mp4"], start="6:3"
        )
        assert not seen.get("reached")


class TestRunDailyTimelapseGuards:
    def test_aborts_when_dependencies_are_missing(self, monkeypatch):
        monkeypatch.setattr(djt, "check_dependencies", lambda: False)
        notified = []
        monkeypatch.setattr(djt, "send_push", lambda **kwargs: notified.append(kwargs))
        monkeypatch.setattr(djt, "setup_directories", lambda: (_ for _ in ()).throw(AssertionError("到達してはいけない")))

        djt.run_daily_timelapse("entrance")

        assert notified, "FFmpeg 不在は通知しないと誰も気づけない"
        assert notified[0]["channel"] == "error"

    def test_aborts_on_malformed_target_date(self, monkeypatch):
        monkeypatch.setattr(djt, "check_dependencies", lambda: True)
        monkeypatch.setattr(djt, "setup_directories", lambda: (_ for _ in ()).throw(AssertionError("到達してはいけない")))

        djt.run_daily_timelapse("entrance", "2026/08/30")  # YYYY-MM-DD ではない
