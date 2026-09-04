# MY_HOME_SYSTEM/tests/test_review_2026_09_04_server_fixes.py
"""
2026-09-04 全体コードレビュー(Issue #355 配下)のサーバー/監視系修正の回帰テスト。

- #359 録画VODキャッシュの保持期間削除・当日プレイリストの再利用
- #388 保持期間クリーンアップの「1日1回」判定を状態ファイル基準に
- #381 log_analyzer のトレースバック継続行が start_date フィルタを素通りしていた問題
- #382 camera_monitor.check_camera_time の JST 前提(aware UTC 比較に変更)
- #387 sensor_service の finally が新タスクの参照を消していた問題
- #385 alexa_verifier の証明書キャッシュキー正規化・上限・PEM/SAN エラーの変換
- #384 config.verify_and_initialize_storage の書込テストファイル名をプロセス固有に
- #361 DiscordErrorHandler の 2000 字対策 / notification_service のチャンク分割・429 リトライ
- #360 camera_service.stop_all_processes / lifespan での ffmpeg 停止
- #383 起動時マイグレーション失敗時は監視子プロセスを起動しない
"""
import asyncio
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core import alexa_verifier as av
from core import logger as core_logger
from monitors import log_analyzer as la_module
from monitors.nas_monitor import NasMonitor
from services import camera_service, notification_service, sensor_service


# ---------------------------------------------------------------------------
# #359 / #388 nas_monitor
# ---------------------------------------------------------------------------
class TestVodRetentionAndDailyCleanup:
    def test_retention_targets_include_hls_vod_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "HLS_VOD_RETENTION_DAYS", 3)
        vod_dir = tmp_path / "data" / "hls_streams" / "vod" / "cam1"
        vod_dir.mkdir(parents=True)
        old_seg = vod_dir / "record_20260101_000.ts"
        old_seg.write_bytes(b"x")
        old_t = time.time() - 10 * 86400
        os.utime(old_seg, (old_t, old_t))
        fresh_seg = vod_dir / "record_20260904_000.ts"
        fresh_seg.write_bytes(b"x")

        monitor = NasMonitor()
        with patch("monitors.nas_monitor.send_push", lambda *a, **k: True):
            monitor.run_retention_cleanup()

        assert not old_seg.exists(), "保持期間超過のVODセグメントが削除されていない"
        assert fresh_seg.exists()

    def test_cleanup_runs_once_per_day_regardless_of_exact_hour(self, tmp_path, monkeypatch):
        monitor = NasMonitor()
        monitor.state_file = str(tmp_path / "state.json")
        calls = []
        monkeypatch.setattr(monitor, "check_ping", lambda: True)
        monkeypatch.setattr(monitor, "check_mount", lambda: True)
        monkeypatch.setattr(monitor, "check_write_permission", lambda: True)
        monkeypatch.setattr(monitor, "get_disk_usage", lambda: {"percent": 10.0, "total_gb": 1, "used_gb": 0.1, "free_gb": 0.9})
        monkeypatch.setattr(monitor, "run_retention_cleanup", lambda: calls.append(1))
        monkeypatch.setattr(monitor, "save_to_db", lambda *a, **k: None)

        class _FakeDT:
            _now = datetime(2026, 9, 4, 9, 0, 5)

            @classmethod
            def now(cls):
                return cls._now

        monkeypatch.setattr("monitors.nas_monitor.datetime", _FakeDT)
        with patch("monitors.nas_monitor.send_push", lambda *a, **k: True):
            monitor.run()          # 9:00 台(8時台の実行が無かった日)でも実行される
            monitor.run()          # 同日2回目は実行されない
            _FakeDT._now = datetime(2026, 9, 5, 8, 0, 5)
            monitor.run()          # 翌日は再び実行される

        assert len(calls) == 2
        state = monitor._load_state()
        assert state["last_cleanup_date"] == "2026-09-05"
        assert state["is_healthy"] is True


# ---------------------------------------------------------------------------
# #381 log_analyzer
# ---------------------------------------------------------------------------
class TestLogAnalyzerContinuationLines:
    def test_old_traceback_lines_are_filtered_by_preceding_timestamp(self, tmp_path):
        log = tmp_path / "home_system.log"
        log.write_text(
            "2026-08-01 10:00:00 [ERROR] x: old failure\n"
            "Traceback (most recent call last):\n"
            "  File \"x.py\", line 1, in <module>\n"
            "ValueError: old boom\n"
            "2026-09-04 08:00:00 [INFO] x: fine\n",
            encoding="utf-8",
        )
        analyzer = la_module.LogAnalyzer.__new__(la_module.LogAnalyzer)
        analyzer.report_data = {}
        analyzer.start_date = datetime(2026, 9, 1)
        analyzer.IGNORE_PATTERNS = getattr(la_module.LogAnalyzer, "IGNORE_PATTERNS", [])
        analyzer.ERROR_KEYWORDS = getattr(la_module.LogAnalyzer, "ERROR_KEYWORDS", ["ERROR", "Traceback", "Exception"])
        analyzer.WARN_KEYWORDS = getattr(la_module.LogAnalyzer, "WARN_KEYWORDS", ["WARNING"])

        analyzer._analyze_file(str(log))

        assert analyzer.report_data == {}, "start_date より前のトレースバック継続行がカウントされている"

    def test_recent_traceback_lines_are_still_counted(self, tmp_path):
        log = tmp_path / "home_system.log"
        log.write_text(
            "2026-09-03 10:00:00 [ERROR] x: new failure\n"
            "Traceback (most recent call last):\n"
            "ValueError: new boom\n",
            encoding="utf-8",
        )
        analyzer = la_module.LogAnalyzer.__new__(la_module.LogAnalyzer)
        analyzer.report_data = {}
        analyzer.start_date = datetime(2026, 9, 1)
        analyzer.IGNORE_PATTERNS = getattr(la_module.LogAnalyzer, "IGNORE_PATTERNS", [])
        analyzer.ERROR_KEYWORDS = getattr(la_module.LogAnalyzer, "ERROR_KEYWORDS", ["ERROR", "Traceback", "Exception"])
        analyzer.WARN_KEYWORDS = getattr(la_module.LogAnalyzer, "WARN_KEYWORDS", ["WARNING"])

        analyzer._analyze_file(str(log))

        assert analyzer.report_data["home_system.log"]["errors"] >= 1


# ---------------------------------------------------------------------------
# #382 camera_monitor.check_camera_time
# ---------------------------------------------------------------------------
class TestCameraTimeCheckIsTimezoneAware:
    def test_matching_utc_time_passes_even_if_host_tz_is_not_jst(self, monkeypatch):
        from monitors import camera_monitor

        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        utc_ns = SimpleNamespace(
            Date=SimpleNamespace(Year=now_utc.year, Month=now_utc.month, Day=now_utc.day),
            Time=SimpleNamespace(Hour=now_utc.hour, Minute=now_utc.minute, Second=now_utc.second),
        )
        devicemgmt = MagicMock()
        devicemgmt.GetSystemDateAndTime.return_value = SimpleNamespace(UTCDateTime=utc_ns)
        # ホストTZに依存しないことを確認するため TZ=UTC 相当で評価する
        monkeypatch.setenv("TZ", "UTC")
        time.tzset()
        try:
            assert camera_monitor.check_camera_time(devicemgmt, "cam") is True
        finally:
            monkeypatch.delenv("TZ", raising=False)
            time.tzset()

    def test_nine_hour_drift_is_detected(self):
        from monitors import camera_monitor

        drifted = datetime.now(timezone.utc).replace(microsecond=0)
        drifted = drifted.replace(hour=(drifted.hour + 9) % 24)
        utc_ns = SimpleNamespace(
            Date=SimpleNamespace(Year=drifted.year, Month=drifted.month, Day=drifted.day),
            Time=SimpleNamespace(Hour=drifted.hour, Minute=drifted.minute, Second=drifted.second),
        )
        devicemgmt = MagicMock()
        devicemgmt.GetSystemDateAndTime.return_value = SimpleNamespace(UTCDateTime=utc_ns)
        assert camera_monitor.check_camera_time(devicemgmt, "cam") is False


# ---------------------------------------------------------------------------
# #387 sensor_service finally guard
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inactive_notification_finally_does_not_remove_newer_task():
    mac = "mac_guard"
    sensor_service.IS_ACTIVE[mac] = True
    started = asyncio.Event()
    proceed = asyncio.Event()

    def _slow_send(*a, **k):
        # to_thread 内で新しい検知が来るのを待つ
        loop.call_soon_threadsafe(started.set)
        while not proceed.is_set():
            time.sleep(0.01)
        return True

    loop = asyncio.get_running_loop()
    with patch.object(sensor_service, "send_push", _slow_send):
        old_task = asyncio.create_task(sensor_service.send_inactive_notification(mac, "t", "l", 0))
        sensor_service.MOTION_TASKS[mac] = old_task
        await started.wait()
        # 次の検知: 新しいタイマータスクを登録(旧タスクは置き換えられる)
        new_task = asyncio.create_task(asyncio.sleep(10))
        sensor_service.MOTION_TASKS[mac] = new_task
        sensor_service.IS_ACTIVE[mac] = True
        proceed.set()
        await old_task

    assert sensor_service.MOTION_TASKS.get(mac) is new_task, "旧タスクの finally が新タスクの参照を消した"
    assert sensor_service.IS_ACTIVE[mac] is True
    new_task.cancel()
    sensor_service.MOTION_TASKS.pop(mac, None)
    sensor_service.IS_ACTIVE.pop(mac, None)


# ---------------------------------------------------------------------------
# #385 alexa_verifier cache
# ---------------------------------------------------------------------------
class TestAlexaCertCache:
    def setup_method(self):
        av._cert_cache.clear()

    def test_query_string_is_rejected(self):
        with pytest.raises(av.AlexaVerificationError):
            av._validate_cert_chain_url("https://s3.amazonaws.com/echo.api/echo-api-cert.pem?x=1")

    def test_cache_key_ignores_query_and_normalizes_path(self):
        a = av._cert_cache_key("https://s3.amazonaws.com/echo.api/echo-api-cert.pem")
        b = av._cert_cache_key("https://S3.amazonaws.com/echo.api/./echo-api-cert.pem?x=2")
        assert a == b

    def test_cache_is_bounded(self, monkeypatch):
        fake_cert = object()
        monkeypatch.setattr(av.x509, "load_pem_x509_certificates", lambda content: [fake_cert])
        monkeypatch.setattr(av.requests, "get", lambda url, timeout: MagicMock(content=b"pem", raise_for_status=lambda: None))
        for i in range(av.CERT_CACHE_MAX_ENTRIES + 5):
            av._fetch_leaf_certificate(f"https://s3.amazonaws.com/echo.api/cert-{i}.pem")
        assert len(av._cert_cache) <= av.CERT_CACHE_MAX_ENTRIES

    def test_invalid_pem_is_verification_error(self, monkeypatch):
        monkeypatch.setattr(av.requests, "get", lambda url, timeout: MagicMock(content=b"not pem", raise_for_status=lambda: None))
        def _raise(content):
            raise ValueError("bad pem")
        monkeypatch.setattr(av.x509, "load_pem_x509_certificates", _raise)
        with pytest.raises(av.AlexaVerificationError):
            av._fetch_leaf_certificate("https://s3.amazonaws.com/echo.api/cert.pem")


# ---------------------------------------------------------------------------
# #384 config write test file name
# ---------------------------------------------------------------------------
def test_write_test_uses_process_unique_filename(tmp_path):
    seen = []
    real_open = open

    def _spy_open(path, *a, **k):
        if ".write_test" in str(path):
            seen.append(os.path.basename(str(path)))
        return real_open(path, *a, **k)

    with patch("builtins.open", _spy_open):
        assert config.verify_and_initialize_storage(str(tmp_path), max_retries=1) is True
    assert seen and seen[0] != ".write_test" and str(os.getpid()) in seen[0]
    assert not any(name.startswith(".write_test") for name in os.listdir(tmp_path))


# ---------------------------------------------------------------------------
# #361 DiscordErrorHandler / notification_service
# ---------------------------------------------------------------------------
class TestDiscordErrorHandlerContentLimit:
    def _emit_and_capture(self, monkeypatch, record):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/webhook")
        handler = core_logger.DiscordErrorHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        with patch("core.logger.requests.post") as mock_post:
            handler.emit(record)
            deadline = time.monotonic() + 3
            while mock_post.call_count == 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert mock_post.call_count == 1
            return mock_post.call_args.kwargs["json"]["content"]

    def test_long_message_is_truncated_under_2000(self, monkeypatch):
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "x" * 5000, (), None)
        content = self._emit_and_capture(monkeypatch, rec)
        assert len(content) <= 2000
        assert "切り詰め" in content

    def test_plain_error_has_no_stack_trace(self, monkeypatch):
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "plain error", (), None)
        content = self._emit_and_capture(monkeypatch, rec)
        assert "Stack Trace" not in content

    def test_error_with_exc_info_includes_trace_within_limit(self, monkeypatch):
        try:
            raise RuntimeError("boom " * 500)
        except RuntimeError:
            rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "with exc", (), sys.exc_info())
        content = self._emit_and_capture(monkeypatch, rec)
        assert "Stack Trace" in content
        assert len(content) <= 2000


class TestDiscordWebhookChunkingAndRetry:
    def test_long_content_is_split_into_multiple_posts(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")
        posts = []
        monkeypatch.setattr(
            notification_service.requests, "post",
            lambda url, **kw: posts.append(kw) or MagicMock(status_code=204, headers={}),
        )
        text = "\n".join("line %04d " % i + "a" * 60 for i in range(80))  # 約 5,600 字
        assert notification_service._send_discord_webhook([{"type": "text", "text": text}]) is True
        assert len(posts) >= 3
        assert all(len(p["json"]["content"]) <= 2000 for p in posts)

    def test_429_is_retried_once_using_retry_after(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")
        responses = [MagicMock(status_code=429, headers={"Retry-After": "0.01"}, text="rate limited"),
                     MagicMock(status_code=204, headers={})]
        sleeps = []
        monkeypatch.setattr(notification_service, "_retry_sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(notification_service.requests, "post", lambda url, **kw: responses.pop(0))
        assert notification_service._send_discord_webhook([{"type": "text", "text": "hi"}]) is True
        assert sleeps == [0.01]

    def test_persistent_5xx_returns_false_after_retry(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")
        calls = []
        monkeypatch.setattr(notification_service, "_retry_sleep", lambda s: None)
        monkeypatch.setattr(
            notification_service.requests, "post",
            lambda url, **kw: calls.append(1) or MagicMock(status_code=500, headers={}, text="err"),
        )
        assert notification_service._send_discord_webhook([{"type": "text", "text": "hi"}]) is False
        assert len(calls) == 1 + notification_service.DISCORD_RETRY_ATTEMPTS


# ---------------------------------------------------------------------------
# #360 camera_service.stop_all_processes / #359 today reuse
# ---------------------------------------------------------------------------
class _FakeProc:
    def __init__(self, alive=True):
        self._alive = alive
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def test_stop_all_processes_terminates_live_and_vod_ffmpeg(monkeypatch):
    live, vod, done = _FakeProc(), _FakeProc(), _FakeProc(alive=False)
    monkeypatch.setattr(camera_service, "_active_processes", {"cam1": live})
    monkeypatch.setattr(camera_service, "_active_vod_processes", {"cam1_20260101": vod, "cam2_20260101": done})

    assert camera_service.stop_all_processes() == 2
    assert live.terminated and vod.terminated and not done.terminated
    assert camera_service._active_processes == {}
    assert camera_service._active_vod_processes == {}


def test_lifespan_shutdown_stops_ffmpeg(isolated_db, monkeypatch):
    import unified_server
    from fastapi.testclient import TestClient

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(unified_server, "apply_pending_migrations", lambda conn: None)
    calls = []
    monkeypatch.setattr(unified_server.camera_service, "stop_all_processes", lambda: calls.append(1) or 0)
    with TestClient(unified_server.app):
        pass
    assert calls == [1]


def test_today_playlist_is_reused_within_grace_period(tmp_path, monkeypatch):
    monkeypatch.setattr(camera_service, "HLS_VOD_DIR", str(tmp_path / "vod"))
    monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path / "nvr"))
    today = datetime.now().strftime("%Y%m%d")
    nvr = tmp_path / "nvr" / "cam1"
    nvr.mkdir(parents=True)
    (nvr / f"{today}_000000.mp4").write_bytes(b"x")
    cam_dir = tmp_path / "vod" / "cam1"
    cam_dir.mkdir(parents=True)
    playlist = cam_dir / f"record_{today}.m3u8"
    playlist.write_text("#EXTM3U\n")

    popen_calls = []
    monkeypatch.setattr(camera_service.subprocess, "Popen", lambda *a, **k: popen_calls.append(1) or _FakeProc(alive=False))
    cam_conf = {"id": "cam1", "name": "cam1", "nas_folder": "cam1"}

    result = camera_service.generate_record_playlist(cam_conf, today)

    assert result == str(playlist)
    assert popen_calls == [], "生成直後の当日プレイリストが再生成されている"


# ---------------------------------------------------------------------------
# #383 migration failure → no monitor subprocesses
# ---------------------------------------------------------------------------
def test_migration_failure_skips_monitor_subprocesses(isolated_db, monkeypatch):
    import unified_server
    from fastapi.testclient import TestClient

    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a) or _FakeProc())

    def _boom(conn):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(unified_server, "apply_pending_migrations", _boom)
    monkeypatch.setattr(unified_server.camera_service, "stop_all_processes", lambda: 0)
    with TestClient(unified_server.app) as client:
        assert client.get("/health").status_code == 200
        assert unified_server.app.state.migration_ok is False
    assert spawned == [], "マイグレーション失敗時に監視子プロセスが起動されている"
