# MY_HOME_SYSTEM/tests/test_record_playlist_service.py
"""
services/record_playlist_service.py のテスト (Issue #1091)。

NAS 上の fragmented MP4 を直接参照する録画プレイリストについて、
MP4 ボックスの読み取り・索引キャッシュ・プレイリストの組み立て・配信してよいファイルの判定を検証する。
MP4 はテスト内で最小構成のボックスを組み立てて作る(ffmpeg が使える環境では、
NVR と同じオプションで実際に書いた MP4 でも検証する)。
"""
import os
import shutil
import struct
import subprocess
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import record_playlist_service as rps


# ---------------------------------------------------------------------------
# 最小構成の fragmented MP4 を組み立てるヘルパー
# ---------------------------------------------------------------------------

def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def _full(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(box_type, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _trak(track_id: int, handler: bytes, timescale: int, version: int = 0) -> bytes:
    if version == 1:
        tkhd = _full(b"tkhd", 1, 3, struct.pack(">QQI", 0, 0, track_id) + b"\0" * 80)
        mdhd = _full(b"mdhd", 1, 0, struct.pack(">QQIQ", 0, 0, timescale, 0) + b"\0" * 4)
    else:
        tkhd = _full(b"tkhd", 0, 3, struct.pack(">III", 0, 0, track_id) + b"\0" * 68)
        mdhd = _full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, timescale, 0) + b"\0" * 4)
    hdlr = _full(b"hdlr", 0, 0, b"\0" * 4 + handler + b"\0" * 12 + b"x\0")
    return _box(b"trak", tkhd + _box(b"mdia", mdhd + hdlr))


def _init(timescale=90000, trex_duration=0, fragmented=True, track_version=0) -> bytes:
    ftyp = _box(b"ftyp", b"isom" + b"\0\0\x02\0" + b"isomiso6mp41")
    traks = _trak(2, b"soun", 48000) + _trak(1, b"vide", timescale, track_version)
    mvex = b""
    if fragmented:
        mvex = _box(b"mvex", _full(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, trex_duration, 0, 0))
                    + _full(b"trex", 0, 0, struct.pack(">IIIII", 2, 1, 1024, 0, 0)))
    moov = _box(b"moov", _full(b"mvhd", 0, 0, b"\0" * 96) + traks + mvex)
    return ftyp + moov


def _fragment(durations=None, count=0, tfhd_default=None, payload=b"\xab" * 32) -> bytes:
    """映像トラック(ID=1)の moof+mdat。durations があれば trun にサンプルごとの長さを書く。
    なければ count 個のサンプルを tfhd(tfhd_default) または trex の既定長として扱う。"""
    tfhd_flags = 0x020000  # default-base-is-moof
    tfhd_body = struct.pack(">I", 1)
    if tfhd_default is not None:
        tfhd_flags |= 0x08
        tfhd_body += struct.pack(">I", tfhd_default)
    tfhd = _full(b"tfhd", 0, tfhd_flags, tfhd_body)
    if durations is not None:
        # data-offset + sample-duration + sample-size
        trun = _full(b"trun", 0, 0x000301, struct.pack(">Ii", len(durations), 0)
                     + b"".join(struct.pack(">II", d, 10) for d in durations))
    else:
        trun = _full(b"trun", 0, 0x000001, struct.pack(">Ii", count, 0))
    # 音声トラックの traf(長さの計算には使われないこと)
    audio = _box(b"traf", _full(b"tfhd", 0, 0x020008, struct.pack(">II", 2, 1024))
                 + _full(b"trun", 0, 0x000001, struct.pack(">Ii", 50, 0)))
    moof = _box(b"moof", _full(b"mfhd", 0, 0, struct.pack(">I", 1)) + _box(b"traf", tfhd + trun) + audio)
    return moof + _box(b"mdat", payload)


def _one_second_fragment() -> bytes:
    """90kHz で 1 秒分(15fps × 6000)の映像フラグメント。"""
    return _fragment(durations=[6000] * 15)


def _write_mp4(path, n_fragments=10, extra=b""):
    data = _init() + b"".join(_one_second_fragment() for _ in range(n_fragments)) + extra
    path.write_bytes(data)
    return data


# ---------------------------------------------------------------------------
# index_mp4: ボックスの読み取り
# ---------------------------------------------------------------------------

class TestIndexMp4:
    def test_init_segment_and_grouped_segments_cover_fragments_exactly(self, tmp_path):
        path = tmp_path / "a.mp4"
        data = _write_mp4(path, n_fragments=10)
        idx = rps.index_mp4(str(path))

        assert idx["init_size"] == len(_init())
        # 1秒のフラグメント10個 → 4秒・4秒・2秒の3セグメント
        assert [round(s[2], 6) for s in idx["segments"]] == [4.0, 4.0, 2.0]
        # セグメントは初期化セグメントの直後から隙間なく並び、ファイル末尾で終わる
        pos = idx["init_size"]
        for offset, length, _dur in idx["segments"]:
            assert offset == pos
            assert data[offset + 4:offset + 8] == b"moof"
            pos += length
        assert pos == len(data)

    def test_incomplete_trailing_fragment_is_excluded(self, tmp_path):
        """書き込み途中(当日の最新ファイル)の末尾フラグメントは載せない。"""
        path = tmp_path / "growing.mp4"
        partial = _one_second_fragment()[:-10]
        data = _write_mp4(path, n_fragments=4, extra=partial)
        idx = rps.index_mp4(str(path))

        assert sum(s[2] for s in idx["segments"]) == pytest.approx(4.0)
        last_offset, last_len, _ = idx["segments"][-1]
        assert last_offset + last_len == len(data) - len(partial)

    def test_moof_without_mdat_yet_is_excluded(self, tmp_path):
        path = tmp_path / "growing.mp4"
        moof_only = _one_second_fragment()
        moof_only = moof_only[:moof_only.index(b"mdat") - 4]
        _write_mp4(path, n_fragments=2, extra=moof_only)
        assert sum(s[2] for s in rps.index_mp4(str(path))["segments"]) == pytest.approx(2.0)

    def test_duration_from_tfhd_default_sample_duration(self, tmp_path):
        path = tmp_path / "a.mp4"
        path.write_bytes(_init() + _fragment(count=30, tfhd_default=3000))  # 30 × 3000 / 90000 = 1秒
        assert rps.index_mp4(str(path))["segments"][0][2] == pytest.approx(1.0)

    def test_duration_from_trex_default_sample_duration(self, tmp_path):
        path = tmp_path / "a.mp4"
        path.write_bytes(_init(timescale=15360, trex_duration=1024) + _fragment(count=45))  # 45 × 1024 / 15360 = 3秒
        assert rps.index_mp4(str(path))["segments"][0][2] == pytest.approx(3.0)

    def test_version1_track_headers(self, tmp_path):
        path = tmp_path / "a.mp4"
        path.write_bytes(_init(track_version=1) + _one_second_fragment())
        assert rps.index_mp4(str(path))["segments"][0][2] == pytest.approx(1.0)

    def test_non_fragmented_mp4_is_unsupported(self, tmp_path):
        path = tmp_path / "plain.mp4"
        path.write_bytes(_init(fragmented=False) + _box(b"mdat", b"\0" * 100))
        with pytest.raises(rps.UnsupportedRecording):
            rps.index_mp4(str(path))

    def test_missing_moov_is_unsupported(self, tmp_path):
        path = tmp_path / "broken.mp4"
        path.write_bytes(b"\0\0\0\x10ftypisom\0\0\0\0")
        with pytest.raises(rps.UnsupportedRecording):
            rps.index_mp4(str(path))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が無い環境ではスキップ")
def test_real_ffmpeg_output_with_nvr_options(tmp_path):
    """nvr-*.service と同じ movflags で ffmpeg が書いた MP4 を読めること。"""
    out = tmp_path / "20260101_000000.mp4"
    res = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=160x90:rate=15",
        "-f", "lavfi", "-i", "sine",
        "-t", "9", "-c:v", "libx264", "-g", "15", "-c:a", "aac",
        "-movflags", "+faststart+frag_keyframe+empty_moov", "-f", "mp4", str(out),
    ], capture_output=True)
    if res.returncode != 0:
        pytest.skip(f"ffmpeg でテスト動画を作れません: {res.stderr[-200:]!r}")
    data = out.read_bytes()
    idx = rps.index_mp4(str(out))

    assert data[idx["init_size"] + 4:idx["init_size"] + 8] == b"moof"
    assert sum(s[2] for s in idx["segments"]) == pytest.approx(9.0, abs=0.2)
    assert all(s[2] <= rps.SEGMENT_TARGET_SECONDS + 1.01 for s in idx["segments"])
    last = idx["segments"][-1]
    end = last[0] + last[1]
    # 正常に閉じたファイルは末尾に mfra(ランダムアクセス用の索引)が付くだけ
    assert end == len(data) or data[end + 4:end + 8] == b"mfra"


# ---------------------------------------------------------------------------
# get_record_files / 索引キャッシュ / プレイリスト
# ---------------------------------------------------------------------------

@pytest.fixture
def recordings(tmp_path, monkeypatch):
    nvr = tmp_path / "nvr"
    cam_dir = nvr / "Entrance"
    cam_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "NVR_RECORD_DIR", str(nvr))
    monkeypatch.setattr(rps, "INDEX_DIR", str(tmp_path / "index"))
    return cam_dir


CAM = {"id": "cam1", "name": "Entrance"}


class TestGetRecordFiles:
    def test_positions_are_cumulative_and_gaps_are_compressed(self, recordings):
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=6)
        # 10:00:06 から 10:10:00 まで録画が欠けている
        _write_mp4(recordings / "20260101_101000.mp4", n_fragments=3)
        files = rps.get_record_files(CAM, "20260101")

        assert [(f.name, f.start_seconds) for f in files] == [
            ("20260101_100000.mp4", 36000), ("20260101_101000.mp4", 36600)]
        assert files[0].position == 0
        assert files[1].position == pytest.approx(6.0)
        assert files[1].duration == pytest.approx(3.0)

    def test_unsupported_and_empty_files_are_skipped(self, recordings, caplog):
        (recordings / "20260101_090000.mp4").write_bytes(_init(fragmented=False) + _box(b"mdat", b"\0"))
        (recordings / "20260101_095000.mp4").write_bytes(_init())  # 書き始めでフラグメントがまだ無い
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=2)
        files = rps.get_record_files(CAM, "20260101")
        assert [f.name for f in files] == ["20260101_100000.mp4"]
        assert files[0].position == 0
        assert "再生できない形式" in caplog.text

    def test_other_dates_are_ignored(self, recordings):
        _write_mp4(recordings / "20260102_000000.mp4", n_fragments=1)
        assert rps.get_record_files(CAM, "20260101") is None

    def test_missing_record_dir_returns_none(self, recordings):
        assert rps.get_record_files({"id": "x", "name": "Nowhere"}, "20260101") is None

    def test_index_is_cached_and_only_changed_files_are_reread(self, recordings, monkeypatch):
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=2)
        growing = recordings / "20260101_101000.mp4"
        _write_mp4(growing, n_fragments=1)
        assert len(rps.get_record_files(CAM, "20260101")) == 2
        assert os.path.exists(rps._index_path("cam1", "20260101"))

        calls = []
        real = rps.index_mp4
        monkeypatch.setattr(rps, "index_mp4", lambda p: calls.append(os.path.basename(p)) or real(p))

        rps.get_record_files(CAM, "20260101")
        assert calls == [], "変化していないファイルを読み直している"

        _write_mp4(growing, n_fragments=3)  # 書き込みが進んだ
        files = rps.get_record_files(CAM, "20260101")
        assert calls == ["20260101_101000.mp4"]
        assert files[1].duration == pytest.approx(3.0)

    def test_deleted_file_is_dropped_from_index(self, recordings):
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=1)
        old = recordings / "20260101_090000.mp4"
        _write_mp4(old, n_fragments=1)
        rps.get_record_files(CAM, "20260101")
        old.unlink()
        assert [f.name for f in rps.get_record_files(CAM, "20260101")] == ["20260101_100000.mp4"]
        assert "20260101_090000.mp4" not in rps._load_index(rps._index_path("cam1", "20260101"))

    def test_broken_index_json_is_rebuilt(self, recordings):
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=1)
        path = rps._index_path("cam1", "20260101")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("{not json")
        assert len(rps.get_record_files(CAM, "20260101")) == 1

    def test_index_write_failure_still_returns_files(self, recordings, monkeypatch):
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=1)

        def _fail(*a, **k):
            raise OSError("read-only")
        monkeypatch.setattr(rps.tempfile, "mkstemp", _fail)
        assert len(rps.get_record_files(CAM, "20260101")) == 1


class TestPlaylist:
    def test_playlist_references_nas_mp4_by_byte_range(self, recordings):
        a = _write_mp4(recordings / "20260101_100000.mp4", n_fragments=5)
        _write_mp4(recordings / "20260101_101000.mp4", n_fragments=2)
        text = rps.get_record_playlist(CAM, "20260101")
        lines = text.splitlines()

        assert lines[0] == "#EXTM3U"
        assert "#EXT-X-VERSION:7" in lines
        assert "#EXT-X-PLAYLIST-TYPE:VOD" in lines
        assert "#EXT-X-TARGETDURATION:4" in lines
        assert lines[-1] == "#EXT-X-ENDLIST"
        init = len(_init())
        assert f'#EXT-X-MAP:URI="20260101_100000.mp4",BYTERANGE="{init}@0"' in lines
        assert f'#EXT-X-MAP:URI="20260101_101000.mp4",BYTERANGE="{init}@0"' in lines
        # ファイルの切れ目(タイムスタンプが0に戻る)にだけ DISCONTINUITY
        assert lines.count("#EXT-X-DISCONTINUITY") == 1
        assert lines.index("#EXT-X-DISCONTINUITY") < lines.index('#EXT-X-MAP:URI="20260101_101000.mp4",BYTERANGE="%d@0"' % init)
        # 1本目: 4秒 + 1秒
        frag = len(_one_second_fragment())
        assert f"#EXT-X-BYTERANGE:{4 * frag}@{init}" in lines
        assert f"#EXT-X-BYTERANGE:{frag}@{init + 4 * frag}" in lines
        assert init + 5 * frag == len(a)
        assert lines.count("#EXTINF:4.000,") == 1
        assert lines.count("20260101_100000.mp4") == 2

    def test_no_playable_recordings_returns_none(self, recordings):
        (recordings / "20260101_100000.mp4").write_bytes(_init(fragmented=False))
        assert rps.get_record_playlist(CAM, "20260101") is None

    def test_timeline(self, recordings):
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=2)
        _write_mp4(recordings / "20260101_102000.mp4", n_fragments=3)
        assert rps.get_record_timeline(CAM, "20260101") == [
            {"start_seconds": 36000, "position": 0.0, "duration": 2.0},
            {"start_seconds": 37200, "position": 2.0, "duration": 3.0},
        ]

    def test_timeline_without_recordings_is_empty(self, recordings):
        assert rps.get_record_timeline(CAM, "20260101") == []


class TestResolveRecordFile:
    def test_existing_recording_is_resolved(self, recordings):
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=1)
        path = rps.resolve_record_file(CAM, "20260101", "20260101_100000.mp4")
        assert path == os.path.realpath(recordings / "20260101_100000.mp4")

    @pytest.mark.parametrize("filename", [
        "20260102_100000.mp4",
        "20260101_100001.mp4",
        "20260101_246000.mp4",
        "20260101_10000.mp4",
        "20260101_100000.mp4.mp4",
        "../20260101_100000.mp4",
        "20260101_100000.ts",
    ])
    def test_other_names_are_rejected(self, recordings, filename):
        _write_mp4(recordings / "20260101_100000.mp4", n_fragments=1)
        assert rps.resolve_record_file(CAM, "20260101", filename) is None

    def test_symlink_outside_record_dir_is_rejected(self, recordings, tmp_path):
        secret = tmp_path / "secret.mp4"
        secret.write_bytes(b"x")
        os.symlink(secret, recordings / "20260101_100000.mp4")
        assert rps.resolve_record_file(CAM, "20260101", "20260101_100000.mp4") is None
