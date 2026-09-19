"""Issue #703: NVR録画からのスナップショット切り出しの回帰テスト。

録画は fragmented MP4 で、CIFS 越しに見えるファイル末尾は常に最後の mdat の途中で
切れている。以前は末尾1秒前(-sseof -1)を読んでいたため不完全な末尾フラグメントに
当たって失敗し(実機計測: 庭カメラ 0/20、駐車場カメラ 4/20)、動体検知の約3割で
通知画像が落ちていた。-3 秒なら両カメラとも 20/20 で成功する。
"""
import datetime
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor


@pytest.fixture
def nvr(tmp_path, monkeypatch):
    """当日分の録画ファイルを新しい順に並べて返すNVRフォルダを用意する。"""
    cam_name = "TestCam"
    folder = tmp_path / cam_name
    folder.mkdir()
    monkeypatch.setattr(camera_monitor.config, "NVR_RECORD_DIR", str(tmp_path), raising=False)
    fake_tmpdir = tmp_path / "tmp"
    fake_tmpdir.mkdir()
    monkeypatch.setattr(camera_monitor.tempfile, "gettempdir", lambda: str(fake_tmpdir))

    today = datetime.datetime.now().strftime("%Y%m%d")

    def make(*names):
        # 先頭ほど新しい mtime にする(書き込み中の最新セグメント → 完成済みの1つ前 …)
        paths = []
        for i, name in enumerate(names):
            path = folder / f"{today}_{name}.mp4"
            path.write_bytes(b"fake")
            mtime = 1_000_000 - i * 600
            os.utime(path, (mtime, mtime))
            paths.append(path)
        return paths

    return {"conf": {"name": cam_name, "nas_folder": cam_name}, "make": make}


def _ffmpeg_args(cmd):
    return {"sseof": cmd[cmd.index("-sseof") + 1], "src": os.path.basename(cmd[cmd.index("-i") + 1])}


def test_first_attempt_reads_newest_segment_three_seconds_before_eof(nvr):
    newest, _prev = nvr["make"]("120000", "115000")
    calls = []

    def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
        calls.append(_ffmpeg_args(cmd))
        with open(cmd[-1], "wb") as f:
            f.write(b"jpeg")

    with patch("subprocess.run", side_effect=fake_run):
        assert camera_monitor.capture_snapshot_from_nvr(nvr["conf"]) == b"jpeg"

    assert calls == [{"sseof": "-3", "src": newest.name}], (
        "末尾1秒前(-1)は不完全な末尾フラグメントに当たるため、3秒手前から読むこと"
    )


def test_final_attempt_falls_back_to_completed_previous_segment(nvr):
    newest, prev = nvr["make"]("120000", "115000")
    calls = []

    def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
        calls.append(_ffmpeg_args(cmd))
        if len(calls) < 3:
            raise subprocess.CalledProcessError(69, cmd, stderr=b"x\npartial file")
        with open(cmd[-1], "wb") as f:
            f.write(b"jpeg")

    with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
        assert camera_monitor.capture_snapshot_from_nvr(nvr["conf"]) == b"jpeg"

    assert calls == [
        {"sseof": "-3", "src": newest.name},
        {"sseof": "-6", "src": newest.name},
        {"sseof": "-3", "src": prev.name},
    ]


def test_single_segment_does_not_index_past_the_list(nvr):
    (only,) = nvr["make"]("000500")
    calls = []

    def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
        calls.append(_ffmpeg_args(cmd))
        raise subprocess.CalledProcessError(69, cmd, stderr=b"")

    with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
        assert camera_monitor.capture_snapshot_from_nvr(nvr["conf"]) is None

    assert [c["src"] for c in calls] == [only.name] * 3


def test_failure_warning_includes_ffmpeg_reason(nvr):
    nvr["make"]("120000")

    def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
        raise subprocess.CalledProcessError(
            69, cmd, stderr=b"[h264] Invalid NAL unit size\n[mov] stream 0, offset 0x401634: partial file\n"
        )

    # core.logger のロガーは伝播しない設定のため caplog では拾えない。ロガーを直接差し替える。
    with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"), \
         patch.object(camera_monitor.logger, "warning") as warning:
        camera_monitor.capture_snapshot_from_nvr(nvr["conf"])

    messages = [c.args[0] for c in warning.call_args_list if "FFmpeg extraction failed" in c.args[0]]
    assert messages, "失敗時の警告が出ていない"
    assert "rc=69" in messages[0]
    assert "partial file" in messages[0], "stderr の最終行(原因)がログに載っていない"
