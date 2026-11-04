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
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import camera_service


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """モジュールグローバルの辞書がテスト間で干渉しないようにする"""
    # Issue #1088: VOD生成前の空き容量チェックが実行環境の実ディスク(CIランナーは
    # 空きが30GBを切ることがある)に左右されないよう、既定では下限を0にする。
    # 容量チェックそのものは TestVodDiskSpaceGuard が disk_usage を差し替えて検証する。
    monkeypatch.setattr(config, "HLS_VOD_MIN_FREE_GB", 0.0)
    camera_service._active_processes.clear()
    camera_service._live_started_at.clear()
    camera_service._active_vod_processes.clear()
    camera_service._vod_queue.clear()
    camera_service._vod_generation_locks.clear()
    camera_service._rtsp_cache.clear()
    yield
    camera_service._active_processes.clear()
    camera_service._live_started_at.clear()
    camera_service._active_vod_processes.clear()
    camera_service._vod_queue.clear()
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
    ため無限に蓄積し続けていた。参照カウント方式(Issue #661 で
    core.utils.RefCountedLockRegistry へ統合)により、使用後は自動的に
    エントリが削除されることを検証する。"""

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

        assert camera_service._vod_generation_locks.keys() == []

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
        assert camera_service._vod_generation_locks.keys() == []


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

    def test_rtsp_input_has_an_io_timeout(self, tmp_path, monkeypatch):
        """不具合修正: ffmpegのRTSP入力にioタイムアウトが無いと、応答が返らない
        種類のネットワーク不調でffmpegがエラーも出さず無期限にハングし、
        `existing.poll() is None`チェックがハングしたプロセスを「配信中」と
        誤認して再起動しない(固まった時点の映像がライブとして配信され続ける)。
        RTSP/tcpプロトコル自身のソケットI/Oタイムアウトである`-timeout`が
        設定され、`-i`(RTSP URL)より前に渡されること(汎用オプションの
        `-rw_timeout`は`async:`プロトコル専用でRTSP/tcpには効かないことを
        実機相当の検証で確認済みのため使わない)。"""
        monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", str(tmp_path))
        monkeypatch.setattr(camera_service, "get_rtsp_url", lambda cam_conf: "rtsp://u:p@host/stream")

        fake_process = MagicMock()
        fake_process.poll.return_value = None

        with patch.object(camera_service.subprocess, "Popen", return_value=fake_process) as mock_popen:
            camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})

        called_cmd = mock_popen.call_args[0][0]
        assert "-rw_timeout" not in called_cmd
        assert "-timeout" in called_cmd
        timeout_idx = called_cmd.index("-timeout")
        assert called_cmd[timeout_idx + 1] == str(camera_service.FFMPEG_RTSP_TIMEOUT_USEC)
        assert timeout_idx < called_cmd.index("-i")


class TestStartHlsStreamRemovesStaleOutputs:
    """#882: 前回のffmpegが残したstream.m3u8/.tsが残っていると、新しいffmpegが
    最初のセグメントを書くまでの間にルーターが古いプレイリストを返し、前回終了時点
    (例: 前日22時)の映像が再生されていた。新規起動の前に削除すること。"""

    def _prepare_stale_files(self, tmp_path):
        cam_dir = tmp_path / "cam1"
        cam_dir.mkdir()
        (cam_dir / "stream.m3u8").write_text("#EXTM3U\nstream41.ts\n#EXT-X-ENDLIST\n")
        (cam_dir / "stream41.ts").write_bytes(b"old")
        (cam_dir / "stream42.ts").write_bytes(b"old")
        (cam_dir / "stream.m3u8.tmp").write_text("#EXTM3U\n")
        return cam_dir

    def test_stale_playlist_and_segments_are_removed_before_ffmpeg_starts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", str(tmp_path))
        monkeypatch.setattr(camera_service, "get_rtsp_url", lambda cam_conf: "rtsp://u:p@host/stream")
        cam_dir = self._prepare_stale_files(tmp_path)

        remaining_at_popen = []

        def _fake_popen(cmd, *args, **kwargs):
            # ffmpeg起動の時点で古い出力が消えていること(起動後に消すと新しい出力まで消す)
            remaining_at_popen.extend(sorted(os.listdir(cam_dir)))
            proc = MagicMock()
            proc.poll.return_value = None
            return proc

        with patch.object(camera_service.subprocess, "Popen", side_effect=_fake_popen):
            result = camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})

        assert result == str(cam_dir / "stream.m3u8")
        assert remaining_at_popen == ["ffmpeg.log"]

    def test_outputs_of_running_ffmpeg_are_kept(self, tmp_path, monkeypatch):
        """配信中のffmpegがいる場合は再起動しないので、その出力を消してはならない。"""
        monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", str(tmp_path))
        cam_dir = self._prepare_stale_files(tmp_path)
        running = MagicMock()
        running.poll.return_value = None
        camera_service._active_processes["cam1"] = running

        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            result = camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})

        mock_popen.assert_not_called()
        assert result == str(cam_dir / "stream.m3u8")
        assert (cam_dir / "stream.m3u8").exists()
        assert (cam_dir / "stream41.ts").exists()

    def test_restart_after_ffmpeg_exited_removes_previous_outputs(self, tmp_path, monkeypatch):
        """前回のffmpegが終了済み(poll()が終了コードを返す)なら、再起動前に消す。"""
        monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", str(tmp_path))
        monkeypatch.setattr(camera_service, "get_rtsp_url", lambda cam_conf: "rtsp://u:p@host/stream")
        cam_dir = self._prepare_stale_files(tmp_path)
        exited = MagicMock()
        exited.poll.return_value = 0
        camera_service._active_processes["cam1"] = exited

        new_proc = MagicMock()
        new_proc.poll.return_value = None
        with patch.object(camera_service.subprocess, "Popen", return_value=new_proc):
            camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})

        assert not (cam_dir / "stream.m3u8").exists()
        assert not list(cam_dir.glob("*.ts"))
        assert camera_service._active_processes["cam1"] is new_proc


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
                # プレイリストファイルの生成を模倣(#562: 実際のffmpegが`-hls_playlist_type vod`で
                # 正常完走時に書き込む#EXT-X-ENDLISTも含める。無いと2回目の呼び出しが
                # _playlist_is_completeにより「不完全」と判定され、意図せず再生成されてしまう)
                playlist_path = cmd[-1]
                with open(playlist_path, "w") as f:
                    f.write("#EXTM3U\n#EXT-X-ENDLIST\n")
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


class TestGenerateRecordPlaylistPastDateCacheCompleteness:
    """#562回帰防止: 過去日付キャッシュは#EXT-X-ENDLISTの有無で完全性を確認してから
    返すこと。シャットダウン時のterminate等で生成途中のまま終わったプレイリストを
    完成品として恒久的に返し続けない。"""

    def _setup(self, tmp_path, monkeypatch):
        nvr_dir = tmp_path / "nvr"
        cam_nvr_dir = nvr_dir / "TestCam"
        cam_nvr_dir.mkdir(parents=True)
        (cam_nvr_dir / "20260101_100000.mp4").write_bytes(b"x")

        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(nvr_dir), raising=False)
        vod_dir = tmp_path / "vod"
        monkeypatch.setattr(camera_service, "HLS_VOD_DIR", str(vod_dir))
        return vod_dir

    def test_incomplete_past_playlist_is_not_served_from_cache(self, tmp_path, monkeypatch):
        vod_dir = self._setup(tmp_path, monkeypatch)
        cam_dir = vod_dir / "cam1"
        cam_dir.mkdir(parents=True)
        playlist_path = cam_dir / "record_20260101.m3u8"
        # ENDLISTを持たない = ffmpegがterminate等で生成途中のまま終わった状態を模倣
        playlist_path.write_text("#EXTM3U\n#EXT-X-VERSION:3\n")

        popen_call_count = {"n": 0}

        def _fake_popen(cmd, **kwargs):
            popen_call_count["n"] += 1
            with open(cmd[-1], "w") as f:
                f.write("#EXTM3U\n#EXT-X-ENDLIST\n")
            proc = MagicMock()
            proc.poll.return_value = 0
            return proc

        cam_conf = {"id": "cam1", "name": "TestCam"}
        with patch.object(camera_service.subprocess, "Popen", side_effect=_fake_popen):
            result = camera_service.generate_record_playlist(cam_conf, "20260101")

        assert result == str(playlist_path)
        assert popen_call_count["n"] == 1, "不完全なキャッシュはそのまま返さず再生成すること"

    def test_complete_past_playlist_is_served_from_cache(self, tmp_path, monkeypatch):
        vod_dir = self._setup(tmp_path, monkeypatch)
        cam_dir = vod_dir / "cam1"
        cam_dir.mkdir(parents=True)
        playlist_path = cam_dir / "record_20260101.m3u8"
        playlist_path.write_text("#EXTM3U\n#EXT-X-ENDLIST\n")

        cam_conf = {"id": "cam1", "name": "TestCam"}
        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            result = camera_service.generate_record_playlist(cam_conf, "20260101")

        assert result == str(playlist_path)
        mock_popen.assert_not_called()



class TestGenerateRecordPlaylistBasics:
    """Issue #954: `_generate_record_playlist_locked` を分割する前に、未カバーだった
    早期リターン・実行中の待機・ffconcat の duration 計算の挙動を固定する。"""

    def _setup(self, tmp_path, monkeypatch, filenames):
        nvr_dir = tmp_path / "nvr"
        cam_nvr_dir = nvr_dir / "TestCam"
        cam_nvr_dir.mkdir(parents=True)
        for name in filenames:
            (cam_nvr_dir / name).write_bytes(b"x")
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(nvr_dir), raising=False)
        vod_dir = tmp_path / "vod"
        monkeypatch.setattr(camera_service, "HLS_VOD_DIR", str(vod_dir))
        return cam_nvr_dir, vod_dir

    @staticmethod
    def _fake_popen(cmd, **kwargs):
        with open(cmd[-1], "w") as f:
            f.write("#EXTM3U\n#EXT-X-ENDLIST\n")
        proc = MagicMock()
        proc.poll.return_value = 0
        return proc

    def test_missing_record_dir_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path / "nowhere"), raising=False)
        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            assert camera_service.generate_record_playlist({"id": "cam1", "name": "TestCam"}, "20260101") is None
        mock_popen.assert_not_called()

    def test_no_recordings_for_date_returns_none(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["20260102_100000.mp4"])
        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            assert camera_service.generate_record_playlist({"id": "cam1", "name": "TestCam"}, "20260101") is None
        mock_popen.assert_not_called()

    def test_concat_durations_follow_real_gaps_and_fall_back_for_implausible_ones(self, tmp_path, monkeypatch):
        cam_nvr_dir, vod_dir = self._setup(
            tmp_path, monkeypatch,
            # 10:00→10:05 は5分の実時間差。10:05→23:30 は上限を超えるため既定値。末尾も既定値。
            ["20260101_100000.mp4", "20260101_100500.mp4", "20260101_233000.mp4"],
        )
        assert 13 * 3600 > camera_service.MAX_PLAUSIBLE_SEGMENT_GAP_SECONDS
        with patch.object(camera_service.subprocess, "Popen", side_effect=self._fake_popen) as mock_popen:
            result = camera_service.generate_record_playlist({"id": "cam1", "name": "TestCam"}, "20260101")

        assert result == str(vod_dir / "cam1" / "record_20260101.m3u8")
        concat = (vod_dir / "cam1" / "concat_20260101.txt").read_text(encoding="utf-8")
        default = camera_service.DEFAULT_SEGMENT_DURATION_SECONDS
        assert concat == (
            "ffconcat version 1.0\n"
            f"file '{cam_nvr_dir / '20260101_100000.mp4'}'\n"
            "duration 300.0\n"
            f"file '{cam_nvr_dir / '20260101_100500.mp4'}'\n"
            f"duration {default}\n"
            f"file '{cam_nvr_dir / '20260101_233000.mp4'}'\n"
            f"duration {default}\n"
        )
        cmd = mock_popen.call_args.args[0]
        nice_at = cmd.index("nice")  # Issue #1088: ionice が使える環境では先頭に `ionice -c3` が付く
        assert cmd[nice_at:nice_at + 3] == ["nice", "-n", str(camera_service.FFMPEG_NICE_LEVEL)]
        assert cmd[cmd.index("-i") + 1] == str(vod_dir / "cam1" / "concat_20260101.txt")

    def test_waits_for_running_generation_instead_of_spawning_again(self, tmp_path, monkeypatch):
        _, vod_dir = self._setup(tmp_path, monkeypatch, ["20260101_100000.mp4"])
        running = MagicMock()
        running.poll.return_value = None
        camera_service._active_vod_processes["cam1_20260101"] = running
        playlist = vod_dir / "cam1" / "record_20260101.m3u8"
        playlist.parent.mkdir(parents=True)
        playlist.write_text("#EXTM3U\n")

        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            result = camera_service.generate_record_playlist({"id": "cam1", "name": "TestCam"}, "20260101")

        assert result == str(playlist)
        mock_popen.assert_not_called()

    def test_running_generation_without_playlist_gives_up_after_waiting(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["20260101_100000.mp4"])
        running = MagicMock()
        running.poll.return_value = None
        camera_service._active_vod_processes["cam1_20260101"] = running
        sleeps = []
        monkeypatch.setattr(camera_service.time, "sleep", sleeps.append)

        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            result = camera_service.generate_record_playlist({"id": "cam1", "name": "TestCam"}, "20260101")

        assert result is None
        assert len(sleeps) == camera_service.PLAYLIST_WAIT_MAX_ATTEMPTS
        mock_popen.assert_not_called()

class TestStartHlsStreamRestartsStalledFfmpeg:
    """#882: プロセスは生きているのにプレイリストが更新されないffmpeg(ハング)は再起動する。"""

    def _setup(self, tmp_path, monkeypatch, age_seconds):
        monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", str(tmp_path))
        monkeypatch.setattr(camera_service, "get_rtsp_url", lambda cam_conf: "rtsp://u:p@host/stream")
        cam_dir = tmp_path / "cam1"
        cam_dir.mkdir()
        playlist = cam_dir / "stream.m3u8"
        playlist.write_text("#EXTM3U\n")
        old = time.time() - age_seconds
        os.utime(playlist, (old, old))
        hung = MagicMock()
        hung.poll.return_value = None
        camera_service._active_processes["cam1"] = hung
        return hung

    def test_stalled_playlist_kills_and_restarts(self, tmp_path, monkeypatch):
        hung = self._setup(tmp_path, monkeypatch, camera_service.HLS_LIVE_STALL_SECONDS + 10)
        new_proc = MagicMock()
        new_proc.poll.return_value = None
        with patch.object(camera_service.subprocess, "Popen", return_value=new_proc) as mock_popen:
            camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})
        hung.kill.assert_called_once()
        mock_popen.assert_called_once()
        assert camera_service._active_processes["cam1"] is new_proc

    def test_fresh_playlist_is_not_restarted(self, tmp_path, monkeypatch):
        hung = self._setup(tmp_path, monkeypatch, 1)
        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})
        hung.kill.assert_not_called()
        mock_popen.assert_not_called()

    def test_no_playlist_uses_start_time(self, tmp_path, monkeypatch):
        hung = self._setup(tmp_path, monkeypatch, 0)
        (tmp_path / "cam1" / "stream.m3u8").unlink()
        camera_service._live_started_at["cam1"] = time.time() - camera_service.HLS_LIVE_STALL_SECONDS - 5
        new_proc = MagicMock()
        new_proc.poll.return_value = None
        with patch.object(camera_service.subprocess, "Popen", return_value=new_proc):
            camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})
        hung.kill.assert_called_once()

    def test_no_playlist_and_no_start_time_is_not_restarted(self, tmp_path, monkeypatch):
        hung = self._setup(tmp_path, monkeypatch, 0)
        (tmp_path / "cam1" / "stream.m3u8").unlink()
        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            camera_service.start_hls_stream({"id": "cam1", "name": "TestCam"})
        hung.kill.assert_not_called()
        mock_popen.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #1088: 過去日の録画再生で3台分のVOD生成が同時に走り、SDカードへ約70GBを
# 書き込んでDB保存失敗・API無応答を誘発した件の短期対策
# (空き容量チェック・全カメラで同時1本・ionice/readrate・終了ログ)
# ---------------------------------------------------------------------------

_GB = 1024 ** 3


def _setup_recordings(tmp_path, monkeypatch, cams=("cam1",), date="20260101", size=1000):
    """cam_id ごとに NVR 録画(mp4 1本、size バイト)を用意し、VOD 出力先を tmp_path に向ける。"""
    nvr_dir = tmp_path / "nvr"
    for cam_id in cams:
        d = nvr_dir / cam_id
        d.mkdir(parents=True)
        (d / f"{date}_100000.mp4").write_bytes(b"x" * size)
    monkeypatch.setattr(config, "NVR_RECORD_DIR", str(nvr_dir), raising=False)
    vod_dir = tmp_path / "vod"
    monkeypatch.setattr(camera_service, "HLS_VOD_DIR", str(vod_dir))
    return vod_dir


def _cam(cam_id):
    return {"id": cam_id, "name": cam_id}


def _running_popen_factory(calls):
    """実行中のまま終わらない ffmpeg を模倣する Popen。起動時にプレイリストを書く。"""
    def _popen(cmd, **kwargs):
        with open(cmd[-1], "w") as f:
            f.write("#EXTM3U\n")
        proc = MagicMock()
        proc.poll.return_value = None
        calls.append((cmd, kwargs, proc))
        return proc
    return _popen


class TestVodDiskSpaceGuard:
    """空き容量が「想定出力量 + 下限」に満たなければ生成を断ること。"""

    def _disk(self, monkeypatch, free_bytes):
        usage = MagicMock(free=free_bytes)
        monkeypatch.setattr(camera_service.shutil, "disk_usage", lambda path: usage)

    def test_refuses_when_free_space_minus_estimate_is_below_threshold(self, tmp_path, monkeypatch):
        _setup_recordings(tmp_path, monkeypatch, size=1000)
        monkeypatch.setattr(config, "HLS_VOD_MIN_FREE_GB", 30.0)
        # 想定出力 = 1000 × 1.1 = 1100 バイト。空きは下限+1050 なので、元サイズ(1000)は
        # 収まるが係数込みの想定出力は収まらない → 断る
        self._disk(monkeypatch, 30 * _GB + 1050)
        mock_logger = MagicMock()
        monkeypatch.setattr(camera_service, "logger", mock_logger)

        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            with pytest.raises(camera_service.VodGenerationUnavailable) as exc:
                camera_service.generate_record_playlist(_cam("cam1"), "20260101")

        mock_popen.assert_not_called()
        assert "空き容量が不足" in exc.value.message
        assert exc.value.retry_after is None
        warned = " ".join(str(c.args[0]) for c in mock_logger.warning.call_args_list)
        assert "空き容量が不足" in warned and "下限 30GB" in warned
        assert not (tmp_path / "vod" / "cam1" / "concat_20260101.txt").exists(), "断ったのに concat を書いている"

    def test_generates_when_enough_free_space(self, tmp_path, monkeypatch):
        vod_dir = _setup_recordings(tmp_path, monkeypatch, size=1000)
        monkeypatch.setattr(config, "HLS_VOD_MIN_FREE_GB", 30.0)
        self._disk(monkeypatch, 30 * _GB + 2000)
        monkeypatch.setattr(camera_service, "_start_vod_watcher", lambda *a, **k: None)
        calls = []

        with patch.object(camera_service.subprocess, "Popen", side_effect=_running_popen_factory(calls)):
            result = camera_service.generate_record_playlist(_cam("cam1"), "20260101")

        assert result == str(vod_dir / "cam1" / "record_20260101.m3u8")
        assert len(calls) == 1

    def test_refuses_when_free_space_cannot_be_read(self, tmp_path, monkeypatch):
        _setup_recordings(tmp_path, monkeypatch)

        def _boom(path):
            raise OSError("stat failed")
        monkeypatch.setattr(camera_service.shutil, "disk_usage", _boom)

        with patch.object(camera_service.subprocess, "Popen") as mock_popen:
            with pytest.raises(camera_service.VodGenerationUnavailable):
                camera_service.generate_record_playlist(_cam("cam1"), "20260101")
        mock_popen.assert_not_called()

    def test_complete_past_cache_is_served_even_when_disk_is_full(self, tmp_path, monkeypatch):
        """生成済みの完全なキャッシュを返すだけなら書き込みは発生しないので、容量不足でも断らない。"""
        vod_dir = _setup_recordings(tmp_path, monkeypatch)
        monkeypatch.setattr(config, "HLS_VOD_MIN_FREE_GB", 30.0)
        self._disk(monkeypatch, 0)
        playlist = vod_dir / "cam1" / "record_20260101.m3u8"
        playlist.parent.mkdir(parents=True)
        playlist.write_text("#EXTM3U\n#EXT-X-ENDLIST\n")

        assert camera_service.generate_record_playlist(_cam("cam1"), "20260101") == str(playlist)


class TestVodSingleGenerationSlot:
    """VOD生成のffmpegは全カメラ・全日付で同時1本。他は順番待ちに入り、終了時に順に起動される。"""

    @pytest.fixture(autouse=True)
    def _no_watcher(self, monkeypatch):
        # 監視スレッドは TestVodProcessExitLogging で直接検証する。ここでは終了の契機を
        # テストから明示的に起こすため起動しない。
        monkeypatch.setattr(camera_service, "_start_vod_watcher", lambda *a, **k: None)

    def test_three_cameras_at_once_spawn_only_one_ffmpeg(self, tmp_path, monkeypatch):
        cams = ("cam1", "cam2", "cam3")
        _setup_recordings(tmp_path, monkeypatch, cams=cams)
        calls = []
        outcomes = {}
        barrier = threading.Barrier(len(cams))

        def _call(cam_id):
            barrier.wait()
            try:
                outcomes[cam_id] = camera_service.generate_record_playlist(_cam(cam_id), "20260101")
            except camera_service.VodGenerationUnavailable as e:
                outcomes[cam_id] = e

        with patch.object(camera_service.subprocess, "Popen", side_effect=_running_popen_factory(calls)):
            threads = [threading.Thread(target=_call, args=(c,)) for c in cams]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(calls) == 1, "VOD生成のffmpegが同時に2本以上起動された"
        started = [c for c, o in outcomes.items() if isinstance(o, str)]
        queued = [c for c, o in outcomes.items() if isinstance(o, camera_service.VodGenerationUnavailable)]
        assert len(started) == 1 and len(queued) == 2
        assert all(outcomes[c].retry_after == camera_service.VOD_QUEUED_RETRY_AFTER_SEC for c in queued)
        assert sorted(camera_service._vod_queue) == sorted(f"{c}_20260101" for c in queued)

    def test_repeated_request_while_queued_keeps_single_entry_and_reports_position(self, tmp_path, monkeypatch):
        _setup_recordings(tmp_path, monkeypatch, cams=("cam1", "cam2", "cam3"))
        calls = []
        with patch.object(camera_service.subprocess, "Popen", side_effect=_running_popen_factory(calls)):
            camera_service.generate_record_playlist(_cam("cam1"), "20260101")
            for cam_id in ("cam2", "cam3", "cam3"):
                with pytest.raises(camera_service.VodGenerationUnavailable) as exc:
                    camera_service.generate_record_playlist(_cam(cam_id), "20260101")

        assert len(calls) == 1
        assert list(camera_service._vod_queue) == ["cam2_20260101", "cam3_20260101"]
        assert "2番目" in exc.value.message

    def test_queue_is_bounded(self, tmp_path, monkeypatch):
        _setup_recordings(tmp_path, monkeypatch, cams=("cam1", "cam2"))
        monkeypatch.setattr(camera_service, "VOD_QUEUE_MAX", 1)
        calls = []
        with patch.object(camera_service.subprocess, "Popen", side_effect=_running_popen_factory(calls)):
            camera_service.generate_record_playlist(_cam("cam1"), "20260101")
            with pytest.raises(camera_service.VodGenerationUnavailable):
                camera_service.generate_record_playlist(_cam("cam2"), "20260101")  # 順番待ち1件目
            (tmp_path / "nvr" / "cam1" / "20260102_100000.mp4").write_bytes(b"x")
            with pytest.raises(camera_service.VodGenerationUnavailable) as exc:
                camera_service.generate_record_playlist(_cam("cam1"), "20260102")  # 上限超過
        assert "混雑" in exc.value.message
        assert list(camera_service._vod_queue) == ["cam2_20260101"]

    def test_next_queued_job_starts_in_fifo_order_when_running_one_exits(self, tmp_path, monkeypatch):
        _setup_recordings(tmp_path, monkeypatch, cams=("cam1", "cam2", "cam3"))
        calls = []
        with patch.object(camera_service.subprocess, "Popen", side_effect=_running_popen_factory(calls)):
            camera_service.generate_record_playlist(_cam("cam1"), "20260101")
            for cam_id in ("cam2", "cam3"):
                with pytest.raises(camera_service.VodGenerationUnavailable):
                    camera_service.generate_record_playlist(_cam(cam_id), "20260101")

            first = calls[0][2]
            first.poll.return_value = 0
            camera_service._on_vod_process_exit("cam1_20260101", first)
            assert len(calls) == 2 and "cam2" in calls[1][0][-1], "先に並んだ cam2 から起動すること"
            assert "cam1_20260101" not in camera_service._active_vod_processes
            assert list(camera_service._vod_queue) == ["cam3_20260101"]

            # 実行中の cam2 があるうちは、cam3 の要求が来ても起動しない
            with pytest.raises(camera_service.VodGenerationUnavailable):
                camera_service.generate_record_playlist(_cam("cam3"), "20260101")
            assert len(calls) == 2

            second = calls[1][2]
            second.poll.return_value = 0
            camera_service._on_vod_process_exit("cam2_20260101", second)
            assert len(calls) == 3 and "cam3" in calls[2][0][-1]
            assert not camera_service._vod_queue

    def test_queued_job_is_dropped_if_disk_became_full_and_next_one_tried(self, tmp_path, monkeypatch):
        _setup_recordings(tmp_path, monkeypatch, cams=("cam1", "cam2", "cam3"))
        calls = []
        with patch.object(camera_service.subprocess, "Popen", side_effect=_running_popen_factory(calls)):
            camera_service.generate_record_playlist(_cam("cam1"), "20260101")
            for cam_id in ("cam2", "cam3"):
                with pytest.raises(camera_service.VodGenerationUnavailable):
                    camera_service.generate_record_playlist(_cam(cam_id), "20260101")

            real_check = camera_service._check_vod_disk_space

            def _check(cam_dir, mp4_files):
                if cam_dir.endswith("cam2"):
                    raise camera_service.VodGenerationUnavailable("空き容量が不足しているため")
                return real_check(cam_dir, mp4_files)
            monkeypatch.setattr(camera_service, "_check_vod_disk_space", _check)

            first = calls[0][2]
            first.poll.return_value = 0
            camera_service._on_vod_process_exit("cam1_20260101", first)

        assert len(calls) == 2 and "cam3" in calls[1][0][-1]
        assert not camera_service._vod_queue

    def test_stop_all_processes_clears_queue(self, tmp_path, monkeypatch):
        _setup_recordings(tmp_path, monkeypatch, cams=("cam1", "cam2"))
        calls = []
        with patch.object(camera_service.subprocess, "Popen", side_effect=_running_popen_factory(calls)):
            camera_service.generate_record_playlist(_cam("cam1"), "20260101")
            with pytest.raises(camera_service.VodGenerationUnavailable):
                camera_service.generate_record_playlist(_cam("cam2"), "20260101")
            camera_service.stop_all_processes(timeout=0)
        assert not camera_service._vod_queue
        calls[0][2].terminate.assert_called_once()


class TestVodFfmpegCommand:
    """ffmpeg に ionice -c3 と -readrate が付き、stderr を捨てずに受け取ること。"""

    def test_ionice_idle_class_is_prefixed_when_available(self, monkeypatch):
        monkeypatch.setattr(camera_service.shutil, "which", lambda name: "/usr/bin/ionice")
        cmd = camera_service._build_vod_ffmpeg_cmd("/c.txt", "/p.m3u8")
        assert cmd[:5] == ["ionice", "-c3", "nice", "-n", str(camera_service.FFMPEG_NICE_LEVEL)]
        assert cmd[-1] == "/p.m3u8"

    def test_ionice_is_omitted_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(camera_service.shutil, "which", lambda name: None)
        cmd = camera_service._build_vod_ffmpeg_cmd("/c.txt", "/p.m3u8")
        assert cmd[0] == "nice" and "ionice" not in cmd

    def test_readrate_is_an_input_option_from_config(self, monkeypatch):
        monkeypatch.setattr(config, "HLS_VOD_READRATE", 10.0)
        cmd = camera_service._build_vod_ffmpeg_cmd("/c.txt", "/p.m3u8")
        assert cmd[cmd.index("-readrate") + 1] == "10"
        assert cmd.index("-readrate") < cmd.index("-i"), "-readrate は入力オプションとして -i より前に置く"

    def test_readrate_zero_disables_it(self, monkeypatch):
        monkeypatch.setattr(config, "HLS_VOD_READRATE", 0.0)
        assert "-readrate" not in camera_service._build_vod_ffmpeg_cmd("/c.txt", "/p.m3u8")

    def test_ffmpeg_stderr_is_captured_not_discarded(self, tmp_path, monkeypatch):
        _setup_recordings(tmp_path, monkeypatch)
        monkeypatch.setattr(camera_service, "_start_vod_watcher", lambda *a, **k: None)
        calls = []
        with patch.object(camera_service.subprocess, "Popen", side_effect=_running_popen_factory(calls)):
            camera_service.generate_record_playlist(_cam("cam1"), "20260101")
        assert calls[0][1]["stderr"] is camera_service.subprocess.PIPE


class TestVodProcessExitLogging:
    """ffmpeg の終了コード・所要時間・出力サイズ・stderr 末尾がログに出ること。"""

    def _run(self, tmp_path, monkeypatch, returncode, stderr_lines):
        from freezegun import freeze_time

        cam_dir = tmp_path / "vod" / "cam1"
        cam_dir.mkdir(parents=True)
        (cam_dir / "record_20260101.m3u8").write_bytes(b"m" * 100)
        (cam_dir / "record_202601010.ts").write_bytes(b"t" * 2 * 1024 ** 2)
        (cam_dir / "record_202601011.ts").write_bytes(b"t" * 3 * 1024 ** 2)
        (cam_dir / "concat_20260101.txt").write_bytes(b"c" * 10 ** 6)  # 出力サイズには数えない
        mock_logger = MagicMock()
        monkeypatch.setattr(camera_service, "logger", mock_logger)
        monkeypatch.setattr(camera_service, "_BYTES_PER_GB", 1024 ** 2)  # MB 単位で表示させて値を検証する
        exits = []
        monkeypatch.setattr(camera_service, "_on_vod_process_exit", lambda key, proc: exits.append((key, proc)))

        with freeze_time("2026-11-02 19:46:05", tz_offset=9) as frozen:
            started_at = camera_service.time.monotonic()
            proc = MagicMock()
            proc.stderr = iter(stderr_lines)

            def _wait():
                frozen.tick(2820)  # 47分かかった体
                return returncode
            proc.wait.side_effect = _wait
            camera_service._watch_vod_process(proc, "玄関カメラ", "20260101", "cam1_20260101", str(cam_dir), started_at)
        return mock_logger, exits, proc

    def test_success_is_logged_with_exit_code_duration_and_size(self, tmp_path, monkeypatch):
        mock_logger, exits, proc = self._run(tmp_path, monkeypatch, 0, [])
        msg = mock_logger.info.call_args.args[0]
        assert "[玄関カメラ] 20260101 の録画プレイリスト生成が完了" in msg
        assert "終了コード 0" in msg and "所要 2820秒" in msg
        assert "出力 5.00GB" in msg  # .ts 2MB + 3MB + m3u8 100B(_BYTES_PER_GB を MB に差し替え済み)
        mock_logger.warning.assert_not_called()
        assert exits == [("cam1_20260101", proc)], "終了時に次の順番待ちの起動処理を呼ぶこと"

    def test_failure_is_logged_as_warning_with_stderr_tail(self, tmp_path, monkeypatch):
        # 空行は数えない・デコードできないバイトは置換する
        lines = [f"line {i}\n".encode() for i in range(30)] + [b"\n", b"bad \xff byte\n"]
        mock_logger, exits, _ = self._run(tmp_path, monkeypatch, 1, lines)
        msg = mock_logger.warning.call_args.args[0]
        assert "異常終了" in msg and "終了コード 1" in msg and "所要 2820秒" in msg
        tail = msg.split("ffmpeg stderr 末尾:\n", 1)[1].splitlines()
        assert len(tail) == camera_service.VOD_STDERR_TAIL_LINES
        assert tail[0] == "line 11" and tail[-1] == "bad � byte"
        assert len(exits) == 1

    def test_slot_is_released_even_if_waiting_fails(self, tmp_path, monkeypatch):
        cam_dir = tmp_path / "vod" / "cam1"
        cam_dir.mkdir(parents=True)
        monkeypatch.setattr(camera_service, "logger", MagicMock())
        exits = []
        monkeypatch.setattr(camera_service, "_on_vod_process_exit", lambda key, proc: exits.append(key))
        proc = MagicMock()
        proc.stderr = None
        proc.wait.side_effect = RuntimeError("boom")
        camera_service._watch_vod_process(proc, "cam1", "20260101", "cam1_20260101", str(cam_dir), time.monotonic())
        assert exits == ["cam1_20260101"]

    def test_real_watcher_thread_releases_slot_and_starts_next(self, tmp_path, monkeypatch):
        """監視スレッドを実際に動かし、1本目の終了で順番待ちの2本目が自動起動されること。"""
        _setup_recordings(tmp_path, monkeypatch, cams=("cam1", "cam2"))
        monkeypatch.setattr(camera_service, "logger", MagicMock())
        release = threading.Event()
        calls = []

        def _popen(cmd, **kwargs):
            with open(cmd[-1], "w") as f:
                f.write("#EXTM3U\n")
            proc = MagicMock()
            proc.stderr = iter([])
            state = {"rc": None}

            def _wait():
                release.wait(5)
                state["rc"] = 0
                return 0
            proc.wait.side_effect = _wait
            proc.poll.side_effect = lambda: state["rc"]
            calls.append(proc)
            return proc

        with patch.object(camera_service.subprocess, "Popen", side_effect=_popen):
            camera_service.generate_record_playlist(_cam("cam1"), "20260101")
            with pytest.raises(camera_service.VodGenerationUnavailable):
                camera_service.generate_record_playlist(_cam("cam2"), "20260101")
            assert len(calls) == 1
            release.set()
            deadline = time.time() + 5
            while len(calls) < 2 and time.time() < deadline:
                time.sleep(0.01)
            assert len(calls) == 2
