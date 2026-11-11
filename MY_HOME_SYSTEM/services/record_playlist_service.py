"""録画VODのプレイリスト(HLS)を、NAS 上の録画 MP4 を直接参照する形で組み立てる (Issue #1091)。

以前は過去日を再生するたびに ffmpeg で丸1日分を HLS(.ts) へ再多重化し、SQLite と同じ
SD カード(`data/hls_streams/vod/`)へ元の MP4 とほぼ同じ量(1台1日で数十GB)を書き込んでいた
(Issue #1088 の障害の原因)。

NVR(`deploy/systemd/nvr-*.service`)は `movflags=+frag_keyframe+empty_moov` で録画しており、
10分ごとの MP4 はもともと fragmented MP4(先頭に ftyp+moov、以降キーフレームごとに moof+mdat)
である。そこで MP4 のボックス構造だけを読み、

- 各ファイル先頭の ftyp+moov を `#EXT-X-MAP` の初期化セグメント(バイト範囲)とし、
- moof+mdat の組(フラグメント)を約 `SEGMENT_TARGET_SECONDS` 秒ずつまとめて1セグメントとし、
- ファイルの切れ目(`-reset_timestamps 1` でタイムスタンプが0に戻る)に `#EXT-X-DISCONTINUITY` を入れる

HLS プレイリストを返す。セグメントは `<ファイル名>?part=<開始>-<長さ>` で参照し、
`read_record_part` が NAS 上の MP4 から該当範囲だけを読んで返す(`routers/camera_router.py`)。
SD カードへ書くのは小さな索引 JSON だけになる。

ffmpeg(NVR)の既定の fragmented MP4 は `tfhd` にファイル先頭からの絶対位置
(base-data-offset)を持つが、ブラウザの MSE(hls.js)はこれを受け付けない。そのため
返すときに `rewrite_fragments` で base-data-offset を外して default-base-is-moof
(moof 起点)に書き換え、`trun` の data_offset をずらす。書き換えるのは数十バイトの見出しだけで、
映像データ(mdat)はそのまま返す。絶対位置を持たないフラグメントは書き換えない。

ボックスの読み取り結果(セグメントのバイト範囲と長さ)は、ファイルのサイズと更新時刻を
キーにした索引 JSON として `INDEX_DIR/<cam_id>/index_<日付>.json` にキャッシュする。
過去日は2回目以降ボックスを読み直さず、当日分も増えたファイル(書き込み中の最新ファイル)
だけを読み直す。索引は `monitors/nas_monitor.py` の保持期間削除の対象。
"""
import glob
import json
import math
import os
import struct
import tempfile
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Dict, Iterator, List, Optional, Tuple

import config
from core.logger import setup_logging
from core.utils import RefCountedLockRegistry

logger = setup_logging("record_playlist_service")

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
# 索引 JSON の置き場所。旧方式の HLS(.ts) キャッシュと同じディレクトリを使い、
# nas_monitor の既存の保持期間削除(HLS_VOD_RETENTION_DAYS)にそのまま乗せる。
INDEX_DIR = os.path.join(BASE_DIR, "data", "hls_streams", "vod")
INDEX_VERSION = 1

SEGMENT_TARGET_SECONDS = 4.0  # 1セグメントにまとめるフラグメントの合計時間の目安(秒)

_BOX_HEADER = struct.Struct(">I4s")
_MAX_INIT_BOX_BYTES = 16 * 1024 * 1024  # moov を丸ごと読む上限(通常は数KB)
_MAX_MOOF_BYTES = 4 * 1024 * 1024  # moof を丸ごと読む上限(通常は1KB前後)

_TFHD_BASE_DATA_OFFSET = 0x000001
_TFHD_DEFAULT_BASE_IS_MOOF = 0x020000
_TRUN_DATA_OFFSET = 0x000001

# 同一カメラ・日付の索引更新(読み取り→JSON書き込み)を直列化する
_index_locks = RefCountedLockRegistry()


class UnsupportedRecording(Exception):
    """fragmented MP4 として解釈できない録画ファイル。"""


@dataclass
class RecordFile:
    """プレイリストに載せる録画ファイル1本分の情報。"""

    name: str  # ファイル名(例: 20260720_210200.mp4)
    start_seconds: int  # 録画開始時刻(0時からの秒数。ファイル名から求める)
    position: float  # プレイリスト上の開始位置(秒)
    duration: float  # 長さ(秒)
    init_size: int  # 初期化セグメント(ftyp+moov)のバイト数(オフセット0から)
    segments: List[Tuple[int, int, float]] = field(default_factory=list)  # (offset, length, 秒)


# ---------------------------------------------------------------------------
# MP4 ボックスの読み取り
# ---------------------------------------------------------------------------

def _iter_boxes(data: bytes, start: int = 0, end: Optional[int] = None) -> Iterator[Tuple[bytes, int, int]]:
    """メモリ上の data[start:end] に並ぶボックスを (type, 本体の開始位置, ボックスの終端) で返す。"""
    for box_type, _box_start, body, box_end in _iter_box_spans(data, start, end):
        yield box_type, body, box_end


def _iter_box_spans(data: bytes, start: int = 0, end: Optional[int] = None) -> Iterator[Tuple[bytes, int, int, int]]:
    """`_iter_boxes` と同じだが、ボックスの開始位置も含めて (type, 開始, 本体の開始, 終端) で返す。"""
    pos = start
    end = len(data) if end is None else end
    while pos + 8 <= end:
        size, box_type = _BOX_HEADER.unpack_from(data, pos)
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield box_type, pos, pos + header, pos + size
        pos += size


def _find_box(data: bytes, start: int, end: int, box_type: bytes) -> Optional[Tuple[int, int]]:
    for t, body, box_end in _iter_boxes(data, start, end):
        if t == box_type:
            return body, box_end
    return None


def _parse_moov(moov: bytes) -> Tuple[int, int, int]:
    """moov から映像トラックの (track_ID, timescale, trex の default_sample_duration) を返す。

    fragmented MP4 でない(mvex が無い)・映像トラックが無い場合は UnsupportedRecording。
    """
    video: Optional[Tuple[int, int]] = None
    for t, body, end in _iter_boxes(moov):
        if t != b"trak":
            continue
        tkhd = _find_box(moov, body, end, b"tkhd")
        mdia = _find_box(moov, body, end, b"mdia")
        if not tkhd or not mdia:
            continue
        hdlr = _find_box(moov, mdia[0], mdia[1], b"hdlr")
        mdhd = _find_box(moov, mdia[0], mdia[1], b"mdhd")
        if not hdlr or not mdhd:
            continue
        if moov[hdlr[0] + 8:hdlr[0] + 12] != b"vide":
            continue
        tkhd_version = moov[tkhd[0]]
        track_id = struct.unpack_from(">I", moov, tkhd[0] + (20 if tkhd_version == 1 else 12))[0]
        mdhd_version = moov[mdhd[0]]
        timescale = struct.unpack_from(">I", moov, mdhd[0] + (20 if mdhd_version == 1 else 12))[0]
        if timescale > 0:
            video = (track_id, timescale)
            break
    if video is None:
        raise UnsupportedRecording("映像トラックが見つかりません")

    mvex = _find_box(moov, 0, len(moov), b"mvex")
    if mvex is None:
        raise UnsupportedRecording("fragmented MP4 ではありません(mvex がありません)")
    default_duration = 0
    for t, body, _end in _iter_boxes(moov, mvex[0], mvex[1]):
        if t == b"trex" and struct.unpack_from(">I", moov, body + 4)[0] == video[0]:
            default_duration = struct.unpack_from(">I", moov, body + 12)[0]
    return video[0], video[1], default_duration


def _moof_video_ticks(moof: bytes, track_id: int, trex_default_duration: int) -> int:
    """moof 内の映像トラックのサンプル長の合計(timescale 単位)を返す。"""
    total = 0
    for t, body, end in _iter_boxes(moof):
        if t != b"traf":
            continue
        tfhd = _find_box(moof, body, end, b"tfhd")
        if tfhd is None:
            continue
        flags = int.from_bytes(moof[tfhd[0] + 1:tfhd[0] + 4], "big")
        if struct.unpack_from(">I", moof, tfhd[0] + 4)[0] != track_id:
            continue
        pos = tfhd[0] + 8
        if flags & 0x01:  # base-data-offset
            pos += 8
        if flags & 0x02:  # sample-description-index
            pos += 4
        default_duration = struct.unpack_from(">I", moof, pos)[0] if flags & 0x08 else trex_default_duration
        for tt, tbody, _tend in _iter_boxes(moof, body, end):
            if tt != b"trun":
                continue
            tr_flags = int.from_bytes(moof[tbody + 1:tbody + 4], "big")
            sample_count = struct.unpack_from(">I", moof, tbody + 4)[0]
            pos = tbody + 8
            if tr_flags & 0x01:  # data-offset
                pos += 4
            if tr_flags & 0x04:  # first-sample-flags
                pos += 4
            if not tr_flags & 0x100:
                total += default_duration * sample_count
                continue
            per_sample = 4 * sum(1 for bit in (0x100, 0x200, 0x400, 0x800) if tr_flags & bit)
            for i in range(sample_count):
                total += struct.unpack_from(">I", moof, pos + i * per_sample)[0]
    return total


def _read_box_header(f: BinaryIO, pos: int, file_size: int) -> Optional[Tuple[bytes, int, int]]:
    """ファイル上の pos にあるボックスの (type, ヘッダ長, ボックス長) を返す。末尾が欠けていれば None。"""
    if pos + 8 > file_size:
        return None
    f.seek(pos)
    raw = f.read(16 if pos + 16 <= file_size else 8)
    if len(raw) < 8:
        return None
    size, box_type = _BOX_HEADER.unpack_from(raw, 0)
    header = 8
    if size == 1:
        if len(raw) < 16:
            return None
        size = struct.unpack_from(">Q", raw, 8)[0]
        header = 16
    elif size == 0:
        size = file_size - pos
    if size < header or pos + size > file_size:
        return None  # 書き込み途中のボックス
    return box_type, header, size


def _group_fragments(fragments: List[Tuple[int, int, float]]) -> List[Tuple[int, int, float]]:
    """連続するフラグメントを約 SEGMENT_TARGET_SECONDS 秒ずつ1セグメントにまとめる。"""
    segments: List[Tuple[int, int, float]] = []
    cur_start: Optional[int] = None
    cur_end = 0
    cur_dur = 0.0
    for offset, length, duration in fragments:
        if cur_start is None:
            cur_start, cur_end, cur_dur = offset, offset + length, duration
        else:
            cur_end = offset + length
            cur_dur += duration
        if cur_dur >= SEGMENT_TARGET_SECONDS:
            segments.append((cur_start, cur_end - cur_start, cur_dur))
            cur_start = None
    if cur_start is not None and cur_dur > 0:
        segments.append((cur_start, cur_end - cur_start, cur_dur))
    return segments


def index_mp4(path: str) -> Dict[str, Any]:
    """fragmented MP4 1本のボックス構造を読み、初期化セグメント長とセグメント一覧を返す。

    戻り値: {"init_size": int, "segments": [[offset, length, 秒], ...]}
    書き込み途中の末尾フラグメントは含めない。fragmented MP4 でなければ UnsupportedRecording。
    映像データ(mdat の中身)は読まない。
    """
    file_size = os.path.getsize(path)
    fragments: List[Tuple[int, int, float]] = []
    init_size: Optional[int] = None
    video: Optional[Tuple[int, int, int]] = None
    with open(path, "rb") as f:
        pos = 0
        moof_start: Optional[int] = None
        moof_ticks = 0
        while True:
            hdr = _read_box_header(f, pos, file_size)
            if hdr is None:
                break
            box_type, header, size = hdr
            if box_type == b"moov":
                if size > _MAX_INIT_BOX_BYTES:
                    raise UnsupportedRecording(f"moov が大きすぎます({size} bytes)")
                f.seek(pos + header)
                video = _parse_moov(f.read(size - header))
                init_size = pos + size
            elif box_type == b"moof":
                if video is None or init_size is None:
                    raise UnsupportedRecording("moov より前に moof があります")
                if size > _MAX_MOOF_BYTES:
                    raise UnsupportedRecording(f"moof が大きすぎます({size} bytes)")
                f.seek(pos + header)
                moof_start = pos
                moof_ticks = _moof_video_ticks(f.read(size - header), video[0], video[2])
            elif box_type == b"mdat" and moof_start is not None and video is not None:
                fragments.append((moof_start, pos + size - moof_start, moof_ticks / video[1]))
                moof_start = None
            pos += size
    if video is None or init_size is None:
        raise UnsupportedRecording("moov がありません")
    return {"init_size": init_size, "segments": [list(s) for s in _group_fragments(fragments)]}


# ---------------------------------------------------------------------------
# 録画ファイルの列挙と索引キャッシュ
# ---------------------------------------------------------------------------

def record_dir(cam_conf: Dict[str, Any]) -> str:
    """カメラの NVR 録画ディレクトリ(NAS 上)。"""
    return os.path.join(config.NVR_RECORD_DIR, cam_conf.get("nas_folder", cam_conf["name"]))


def find_record_mp4_files(cam_conf: Dict[str, Any], target_date: str) -> Optional[List[str]]:
    """指定日の10分分割 mp4 ファイル一覧(昇順)を返す。保存先や録画が無ければ警告して None。"""
    search_dir = record_dir(cam_conf)
    if not os.path.exists(search_dir):
        logger.warning(f"⚠️ [{cam_conf['name']}] 録画保存先が存在しません: {search_dir}")
        return None
    mp4_files = sorted(glob.glob(os.path.join(search_dir, f"{target_date}_*.mp4")))
    if not mp4_files:
        logger.warning(f"⚠️ [{cam_conf['name']}] {target_date} の録画ファイルが存在しません")
        return None
    return mp4_files


def _start_seconds_from_name(name: str) -> Optional[int]:
    """録画ファイル名(nvr-*.service の strftime 出力 `%Y%m%d_%H%M%S.mp4`)から
    0時からの秒数を返す(`20260720_210200.mp4` → 76920)。読めなければ None。"""
    try:
        time_str = name.split("_")[1].split(".")[0]
        if len(time_str) != 6 or not time_str.isdigit():
            return None
        h, m, s = int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6])
    except (IndexError, ValueError):
        return None
    if h > 23 or m > 59 or s > 59:
        return None
    return h * 3600 + m * 60 + s


def _index_path(cam_id: str, target_date: str) -> str:
    return os.path.join(INDEX_DIR, cam_id, f"index_{target_date}.json")


def _load_index(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != INDEX_VERSION or not isinstance(data.get("files"), dict):
        return {}
    return data["files"]


def _save_index(path: str, files: Dict[str, Any]) -> None:
    """索引 JSON を tmp + os.replace で書き込む(失敗してもプレイリストは返せるので警告のみ)。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".index_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": INDEX_VERSION, "files": files}, f, separators=(",", ":"))
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning(f"⚠️ 録画索引の保存に失敗しました ({path}): {e}")


def _file_index(path: str, cached: Optional[Dict[str, Any]], cam_name: str) -> Tuple[Dict[str, Any], bool]:
    """1ファイル分の索引を返す。(索引, 読み直したか)。サイズ・更新時刻が同じならキャッシュを使う。"""
    st = os.stat(path)
    if cached and cached.get("size") == st.st_size and cached.get("mtime_ns") == st.st_mtime_ns:
        return cached, False
    entry: Dict[str, Any] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    try:
        entry.update(index_mp4(path))
    except UnsupportedRecording as e:
        logger.warning(f"⚠️ [{cam_name}] {os.path.basename(path)} は再生できない形式のためプレイリストから外します: {e}")
        entry["unsupported"] = str(e)
    except (OSError, struct.error) as e:
        logger.warning(f"⚠️ [{cam_name}] {os.path.basename(path)} の読み取りに失敗しました: {e}")
        entry["unsupported"] = str(e)
        # 一時的な読み取り失敗はキャッシュせず次回読み直す
        entry["size"] = -1
    return entry, True


def get_record_files(cam_conf: Dict[str, Any], target_date: str) -> Optional[List[RecordFile]]:
    """指定日の再生可能な録画ファイル一覧(プレイリスト上の位置つき)を返す。1本も無ければ None。"""
    mp4_files = find_record_mp4_files(cam_conf, target_date)
    if not mp4_files:
        return None

    cam_id = cam_conf["id"]
    index_path = _index_path(cam_id, target_date)
    with _index_locks.acquire((cam_id, target_date)):
        cached_files = _load_index(index_path)
        new_files: Dict[str, Any] = {}
        # 消えたファイルがあれば索引から外して保存し直す
        changed = bool(set(cached_files) - {os.path.basename(p) for p in mp4_files})
        for path in mp4_files:
            name = os.path.basename(path)
            try:
                entry, reread = _file_index(path, cached_files.get(name), cam_conf["name"])
            except OSError as e:  # stat 失敗(NAS の瞬断・ファイル削除直後など)
                logger.warning(f"⚠️ [{cam_conf['name']}] {name} を参照できません: {e}")
                continue
            new_files[name] = entry
            changed = changed or reread
        if changed:
            _save_index(index_path, new_files)

    result: List[RecordFile] = []
    position = 0.0
    for name, entry in new_files.items():
        start = _start_seconds_from_name(name)
        segments = entry.get("segments") or []
        if entry.get("unsupported") or start is None or not segments:
            continue
        duration = sum(seg[2] for seg in segments)
        result.append(RecordFile(
            name=name,
            start_seconds=start,
            position=position,
            duration=duration,
            init_size=int(entry["init_size"]),
            segments=[(int(o), int(n), float(d)) for o, n, d in segments],
        ))
        position += duration
    return result or None


def build_playlist(files: List[RecordFile]) -> str:
    """録画ファイル一覧から HLS(VOD) プレイリストを組み立てる。

    セグメントの URI は `<ファイル名>?part=<開始>-<長さ>`(プレイリストと同じ
    `/api/cameras/record/<cam>/<日付>/` からの相対)にする。範囲は索引のセグメントそのもので、
    `read_record_part` はこれと完全に一致する範囲だけを返す。
    """
    max_duration = max((seg[2] for f in files for seg in f.segments), default=SEGMENT_TARGET_SECONDS)
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{max(1, math.ceil(max_duration))}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for i, f in enumerate(files):
        if i > 0:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f'#EXT-X-MAP:URI="{f.name}?part=0-{f.init_size}"')
        for offset, length, duration in f.segments:
            lines.append(f"#EXTINF:{duration:.3f},")
            lines.append(f"{f.name}?part={offset}-{length}")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def get_record_playlist(cam_conf: Dict[str, Any], target_date: str) -> Optional[str]:
    """指定日の録画プレイリスト(m3u8 の本文)を返す。再生できる録画が無ければ None。

    当日分は呼び出し時点で書き終わっているフラグメントまでを載せ、`#EXT-X-ENDLIST` を付ける
    (それ以降の録画は、再読み込みで新しいプレイリストを取り直せば見られる)。
    """
    files = get_record_files(cam_conf, target_date)
    if not files:
        return None
    return build_playlist(files)


def get_record_timeline(cam_conf: Dict[str, Any], target_date: str) -> List[Dict[str, float]]:
    """録画の時刻とプレイリスト上の位置の対応表(フロントエンドのシーク位置の計算用)。

    録画が欠けている時間帯はプレイリスト上では詰まるため、フロントエンドは
    「指定時刻 → 再生位置」をこの表で変換する(`RecordView.tsx`)。
    """
    files = get_record_files(cam_conf, target_date) or []
    return [
        {"start_seconds": f.start_seconds, "position": round(f.position, 3), "duration": round(f.duration, 3)}
        for f in files
    ]


def resolve_record_file(cam_conf: Dict[str, Any], target_date: str, filename: str) -> Optional[str]:
    """配信してよい録画 MP4 の絶対パスを返す。ファイル名が録画の命名規則・日付と合わない、
    録画ディレクトリの外を指す、存在しない場合は None。"""
    if not (filename.startswith(f"{target_date}_") and filename.endswith(".mp4")
            and _start_seconds_from_name(filename) is not None
            and len(filename) == len("YYYYMMDD_HHMMSS.mp4")):
        return None
    base = os.path.realpath(record_dir(cam_conf))
    candidate = os.path.realpath(os.path.join(base, filename))
    if os.path.commonpath([base, candidate]) != base or not os.path.isfile(candidate):
        return None
    return candidate


# ---------------------------------------------------------------------------
# セグメントの読み出しと moof の書き換え
# ---------------------------------------------------------------------------

def _rewrite_moof(moof: bytes, moof_abs: int) -> bytes:
    """moof 内の tfhd から base-data-offset を外して default-base-is-moof にし、
    trun の data_offset を「moof 先頭からの位置」に直す。絶対位置を持たなければそのまま返す。

    書き換えで moof は (base-data-offset を持つ traf の数 × 8) バイト縮み、直後の mdat も
    同じだけ前へずれるので、data_offset は「元の絶対位置 − moof の位置 − 縮んだ量」になる。
    """
    children = [(t, moof[box_start:end]) for t, box_start, _body, end in _iter_box_spans(moof, 8, len(moof))]
    with_base = 0
    for t, raw in children:
        if t != b"traf":
            continue
        tfhd = _find_box(raw, 8, len(raw), b"tfhd")
        if tfhd and int.from_bytes(raw[tfhd[0] + 1:tfhd[0] + 4], "big") & _TFHD_BASE_DATA_OFFSET:
            with_base += 1
    if with_base == 0:
        return moof
    shrink = 8 * with_base

    out: List[bytes] = []
    for t, raw in children:
        if t != b"traf":
            out.append(raw)
            continue
        base: Optional[int] = None
        parts: List[bytes] = []
        for ct, cstart, _cbody, cend in _iter_box_spans(raw, 8, len(raw)):
            child = bytearray(raw[cstart:cend])
            if ct == b"tfhd":
                flags = int.from_bytes(child[9:12], "big")
                if flags & _TFHD_BASE_DATA_OFFSET:
                    base = struct.unpack_from(">Q", child, 16)[0]
                    child = child[:16] + child[24:]
                    flags = (flags & ~_TFHD_BASE_DATA_OFFSET) | _TFHD_DEFAULT_BASE_IS_MOOF
                    child[9:12] = flags.to_bytes(3, "big")
                    struct.pack_into(">I", child, 0, len(child))
            elif ct == b"trun" and base is not None:
                flags = int.from_bytes(child[9:12], "big")
                if not flags & _TRUN_DATA_OFFSET:
                    # data_offset が無いと、データ位置を moof 起点で表し直せない
                    raise UnsupportedRecording("base-data-offset を持つ trun に data_offset がありません")
                offset = struct.unpack_from(">i", child, 16)[0]
                struct.pack_into(">i", child, 16, base + offset - moof_abs - shrink)
            parts.append(bytes(child))
        body = b"".join(parts)
        out.append(struct.pack(">I4s", 8 + len(body), b"traf") + body)
    body = b"".join(out)
    return struct.pack(">I4s", 8 + len(body), b"moof") + body


def rewrite_fragments(chunk: bytes, abs_offset: int) -> bytes:
    """ファイル上の abs_offset から読んだ chunk(moof+mdat の並び)の各 moof を `_rewrite_moof` で直す。"""
    out: List[bytes] = []
    pos = 0
    for t, start, _body, end in _iter_box_spans(chunk):
        if t == b"moof":
            out.append(_rewrite_moof(chunk[start:end], abs_offset + start))
        else:
            out.append(chunk[start:end])
        pos = end
    if pos != len(chunk):
        raise UnsupportedRecording("セグメントの範囲がボックスの境界と一致しません")
    return b"".join(out)


def read_record_part(cam_conf: Dict[str, Any], target_date: str, filename: str,
                     offset: int, length: int) -> Optional[bytes]:
    """プレイリストが参照する範囲(初期化セグメントかセグメント1つ)を NAS の MP4 から読んで返す。

    範囲は索引にあるものと完全に一致する場合だけ受け付ける(任意の範囲を読ませない)。
    セグメントは `rewrite_fragments` で MSE が受け付ける形に直す。該当しなければ None。
    """
    path = resolve_record_file(cam_conf, target_date, filename)
    if path is None:
        return None
    cam_id = cam_conf["id"]
    with _index_locks.acquire((cam_id, target_date)):
        files = _load_index(_index_path(cam_id, target_date))
        cached = files.get(filename)
        try:
            entry, reread = _file_index(path, cached, cam_conf["name"])
        except OSError as e:
            logger.warning(f"⚠️ [{cam_conf['name']}] {filename} を参照できません: {e}")
            return None
        if reread:
            files[filename] = entry
            _save_index(_index_path(cam_id, target_date), files)
    if entry.get("unsupported"):
        return None
    is_init = offset == 0 and length == entry.get("init_size")
    if not is_init and not any(int(o) == offset and int(n) == length for o, n, _d in entry.get("segments") or []):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(length)
        if len(data) != length:
            return None
        return data if is_init else rewrite_fragments(data, offset)
    except (OSError, struct.error, UnsupportedRecording) as e:
        logger.warning(f"⚠️ [{cam_conf['name']}] {filename} の {offset}-{length} を返せません: {e}")
        return None
