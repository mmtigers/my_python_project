# MY_HOME_SYSTEM/tests/test_camera_service_unit.py
"""
services/camera_service.py 本体(モック無し)のユニットテスト。

M-3: 既存 test_camera_router*.py は camera_service を全てmonkeypatchしており、
start_hls_stream/generate_record_playlist等の実装(RTSP URLマスク・ffmpeg起動・
ファイルハンドル管理・排他制御・devices.json書込)は未実行だった。
本ファイルは実装そのものを検証する(ffmpeg自体はモックする)。
"""
import json
import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import camera_service


@pytest.fixture(autouse=True)
def _reset_module_state():
    """モジュールグローバルの辞書がテスト間で干渉しないようにする"""
    camera_service._active_processes.clear()
    camera_service._active_vod_processes.clear()
    camera_service._vod_generation_locks.clear()
    camera_service._rtsp_cache.clear()
    yield
    camera_service._active_processes.clear()
    camera_service._active_vod_processes.clear()
    camera_service._vod_generation_locks.clear()
    camera_service._rtsp_cache.clear()


class TestMaskRtspUrlForLog:
    """M-3-1/2: RTSP URLの認証情報マスク処理"""

    def test_masks_user_and_password(self):
        url = "rtsp://admin:sup3rSecret@192.168.1.50:554/stream1"
        masked = camera_service._mask_rtsp_url_for_log(url)
        assert "sup3rSecret" not in masked
        assert "admin" not in masked
        assert "192.168.1.50:554/stream1" in masked

    def test_empty_password_does_not_corrupt_url(self):
        """M-3-2回帰防止: 旧実装は空文字パスワードで
        str.replace('', '***') により文字列を全文字間破壊していた。"""
        url = "rtsp://admin:@192.168.1.50:554/stream1"
        masked = camera_service._mask_rtsp_url_for_log(url)
        # 破壊されていれば '*'だらけの異常に長い文字列になるので、長さで検知する
        assert len(masked) < len(url) + 20
        assert "admin" not in masked

    def test_url_without_credentials_is_returned_unchanged(self):
        url = "rtsp://192.168.1.50:554/stream1"
        assert camera_service._mask_rtsp_url_for_log(url) == url

    def test_malformed_url_does_not_raise(self):
        # 例外を出さずフォールバック文字列を返すこと
        result = camera_service._mask_rtsp_url_for_log("not a url :: at all")
        assert isinstance(result, str)


class TestPruneFinishedVodProcesses:
    def test_removes_only_finished_processes(self):
        running = MagicMock()
        running.poll.return_value = None
        finished = MagicMock()
        finished.poll.return_value = 0

        camera_service._active_vod_processes["cam1_20260101"] = running
        camera_service._active_vod_processes["cam1_20260102"] = finished

        camera_service._prune_finished_vod_processes()

        assert "cam1_20260101" in camera_service._active_vod_processes
        assert "cam1_20260102" not in camera_service._active_vod_processes


class TestVodGenerationLockPruning:
    """#247: _vod_generation_locksは以前、cam_id×target_dateのキーを一度登録すると
    二度と削除されず、_active_vod_processesに対応するような剪定処理も存在しなかった
    ため無限に蓄積し続けていた。参照カウント方式(_RefCountedLock)により、使用後は
    自動的にエントリが削除されることを検証する。"""

    def test_entry_is_removed_after_use(self):
        with camera_service._vod_generation_lock("cam1_20260101"):
            assert "cam1_20260101" in camera_service._vod_generation_locks

        assert "cam1_20260101" not in camera_service._vod_generation_locks, (
            "使用後はエントリが辞書から削除され、無限蓄積しないこと"
        )

    def test_multiple_keys_each_removed_independently(self):
        with camera_service._vod_generation_lock("cam1_20260101"):
            pass
        with camera_service._vod_generation_lock("cam2_20260102"):
            pass

        assert camera_service._vod_generation_locks == {}

    def test_entry_not_removed_while_another_thread_is_waiting(self):
        """使用中(参照カウント>0)のエントリは、他スレッドが利用中の間は
        削除されないこと(削除してしまうと、同一process_keyに対して2つの
        別々のLockオブジェクトが生成され、排他制御が破られてしまう)。"""
        key = "cam1_20260103"
        first_holder_ready = threading.Event()
        release_first_holder = threading.Event()
        second_thread_done = threading.Event()

        def hold_first():
            with camera_service._vod_generation_lock(key):
                first_holder_ready.set()
                release_first_holder.wait(timeout=5)

        def wait_second():
            first_holder_ready.wait(timeout=5)
            # この時点でfirst_holderがロックを保持中。ここでref_countが2になるはず。
            with camera_service._vod_generation_lock(key):
                pass
            second_thread_done.set()

        t1 = threading.Thread(target=hold_first)
        t2 = threading.Thread(target=wait_second)
        t1.start()
        t1_ready = first_holder_ready.wait(timeout=5)
        assert t1_ready
        t2.start()

        # t2はt1がロックを保持している間ブロックされ、まだ完了していないはず。
        # この間、エントリは(t1のref_countにより)辞書に残り続けているべき。
        import time as _time
        _time.sleep(0.2)
        assert key in camera_service._vod_generation_locks, (
            "他スレッドが同一キーを取得待ちの間はエントリを削除してはならない"
        )

        release_first_holder.set()
        t1.join(timeout=5)
        assert second_thread_done.wait(timeout=5)
        t2.join(timeout=5)

        assert key not in camera_service._vod_generation_locks, (
            "全ての利用者が使い終わったらエントリは削除されるべき"
        )

    def test_mutual_exclusion_still_enforced_across_concurrent_calls(self):
        """回帰防止: 参照カウント方式に変更しても、同一process_keyに対する
        排他制御(同時に2スレッドがクリティカルセクションへ入らないこと)が
        引き続き機能すること。"""
        key = "cam1_20260104"
        concurrent_count = 0
        max_concurrent = 0
        lock_for_counter = threading.Lock()

        def critical_section():
            nonlocal concurrent_count, max_concurrent
            with camera_service._vod_generation_lock(key):
                with lock_for_counter:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                import time as _time
                _time.sleep(0.05)
                with lock_for_counter:
                    concurrent_count -= 1

        threads = [threading.Thread(target=critical_section) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert max_concurrent == 1, "同一process_keyに対する排他制御が破られている"
        assert camera_service._vod_generation_locks == {}


class TestSetCameraEnabledAtomicWrite:
    def test_writes_atomically_and_no_tmp_file_left_behind(self, tmp_path, monkeypatch):
        devices_path = tmp_path / "devices.json"
        devices_path.write_text(
            json.dumps({"cameras": [{"id": "cam1", "enabled": True}]}), encoding="utf-8"
        )
        monkeypatch.setattr(config, "DEVICES_JSON_PATH", str(devices_path))
        monkeypatch.setattr(config, "CAMERAS", [{"id": "cam1", "enabled": True}])

        result = camera_service.set_camera_enabled("cam1", False)

        assert result is True
        assert not os.path.exists(str(devices_path) + ".tmp")
        saved = json.loads(devices_path.read_text(encoding="utf-8"))
        assert saved["cameras"][0]["enabled"] is False
        assert config.CAMERAS[0]["enabled"] is False

    def test_returns_false_when_devices_json_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DEVICES_JSON_PATH", str(tmp_path / "nope.json"))
        assert camera_service.set_camera_enabled("cam1", False) is False

    def test_returns_false_when_camera_not_found(self, tmp_path, monkeypatch):
        devices_path = tmp_path / "devices.json"
        devices_path.write_text(json.dumps({"cameras": []}), encoding="utf-8")
        monkeypatch.setattr(config, "DEVICES_JSON_PATH", str(devices_path))
        assert camera_service.set_camera_enabled("cam1", False) is False


class TestStartHlsStreamLogFileHandling:
    def test_log_file_is_closed_in_parent_after_popen(self, tmp_path, monkeypatch):
        """M-3-3回帰防止: 親プロセス側でファイルハンドルをcloseし、
        プロセス再起動のたびにfdがリークしないこと。"""
        monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", str(tmp_path))
        monkeypatch.setattr(camera_service, "get_rtsp_url", lambda cam_conf: "rtsp://u:p@host/stream")

        captured_files = []
        real_open = open

        def _tracking_open(path, *args, **kwargs):
            f = real_open(path, *args, **kwargs)
            if str(path).endswith("ffmpeg.log"):
                captured_files.append(f)
            return f

        fake_process = MagicMock()
        fake_process.poll.return_value = None

        with patch("builtins.open", side_effect=_tracking_open), \
             patch.object(camera_service.subprocess, "Popen", return_value=fake_process) as mock_popen:
            result = camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})

        assert result.endswith("stream.m3u8")
        assert len(captured_files) == 1
        assert captured_files[0].closed is True
        # ffmpegコマンドにログレベル抑制オプションが含まれること(URLがログに漏れない対策)
        called_cmd = mock_popen.call_args[0][0]
        assert "-loglevel" in called_cmd


class TestGenerateRecordPlaylistConcurrency:
    def test_concurrent_calls_for_same_key_spawn_ffmpeg_only_once(self, tmp_path, monkeypatch):
        """M-3-4回帰防止: 同一cam_id・日付への同時リクエストでffmpegが
        二重起動しないこと(check-then-act競合のレース修正)。"""
        nvr_dir = tmp_path / "nvr"
        cam_nvr_dir = nvr_dir / "TestCam"
        cam_nvr_dir.mkdir(parents=True)
        (cam_nvr_dir / "20260101_100000.mp4").write_bytes(b"x")

        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(nvr_dir), raising=False)
        monkeypatch.setattr(camera_service, "HLS_VOD_DIR", str(tmp_path / "vod"))

        popen_call_count = {"n": 0}
        lock_for_playlist = threading.Lock()

        def _fake_popen(cmd, **kwargs):
            with lock_for_playlist:
                popen_call_count["n"] += 1
                # プレイリストファイルの生成を模倣
                playlist_path = cmd[-1]
                with open(playlist_path, "w") as f:
                    f.write("#EXTM3U\n")
            proc = MagicMock()
            proc.poll.return_value = 0  # 即完了扱い
            return proc

        cam_conf = {"id": "cam1", "name": "TestCam"}

        with patch.object(camera_service.subprocess, "Popen", side_effect=_fake_popen):
            results = [None, None]

            def _call(idx):
                results[idx] = camera_service.generate_record_playlist(cam_conf, "20260101")

            t1 = threading.Thread(target=_call, args=(0,))
            t2 = threading.Thread(target=_call, args=(1,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        assert popen_call_count["n"] == 1
        assert all(r is not None for r in results)
