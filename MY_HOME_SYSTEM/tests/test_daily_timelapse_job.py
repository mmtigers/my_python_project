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
