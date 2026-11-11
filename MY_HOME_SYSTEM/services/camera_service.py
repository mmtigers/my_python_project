import collections
import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import glob
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
# #661: WSDL 探索は core/onvif_utils.py に一本化(以前は本ファイルと camera_monitor/camera_service に同一実装が重複)
from core.onvif_utils import find_wsdl_path
from core.logger import setup_logging
from core.utils import get_now_jst, RefCountedLockRegistry
import config

try:
    from onvif import ONVIFCamera
except ImportError:
    ONVIFCamera = Any

logger = setup_logging("camera_service")

# /tmp (RAM) から物理ストレージ（プロジェクト直下のdataディレクトリ）へ変更
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
HLS_LIVE_DIR = os.path.join(BASE_DIR, "data", "hls_streams", "live")
HLS_VOD_DIR = os.path.join(BASE_DIR, "data", "hls_streams", "vod")

# #359: 当日分の録画VODプレイリストを再生成せずに再利用する猶予秒数
VOD_TODAY_REUSE_SECONDS = 300

# Issue #451: ffmpeg起動コマンドに直書きされていたマジックナンバー群を集約
FFMPEG_NICE_LEVEL = 15  # ffmpegの優先度(nice値)。他プロセスを圧迫しない程度に低優先度化
HLS_LIVE_SEGMENT_SECONDS = 2  # ライブHLS配信のセグメント長(秒)
HLS_LIVE_LIST_SIZE = 5  # ライブHLS配信でプレイリストに保持するセグメント数
HLS_VOD_SEGMENT_SECONDS = 4  # 録画VODのHLSセグメント長(秒)
FFMPEG_LOG_FILE_MODE = 0o600  # ffmpeg.logのパーミッション(所有者のみ読み書き可)
DEFAULT_SEGMENT_DURATION_SECONDS = 600.0  # 正常な10分ファイルのデフォルトduration(秒)
MAX_PLAUSIBLE_SEGMENT_GAP_SECONDS = 43200  # ファイル間の時間差の異常値判定閾値(12時間、秒)
PLAYLIST_WAIT_MAX_ATTEMPTS = 10  # 生成中プレイリスト待機の最大試行回数
PLAYLIST_WAIT_POLL_INTERVAL_SEC = 0.5  # 生成中プレイリスト待機のポーリング間隔(秒)

# Issue #1088: 過去日の録画再生で3台分のVOD生成(-c:v copy で丸1日分)が同時に走り、
# SQLiteと同じSDカードへ約47分で約70GBを書き込んで、センサー保存失敗・database is locked・
# API無応答を誘発した。以下で「全カメラで同時1本」「空き容量の事前確認」「I/O優先度の
# 引き下げ」「終了のログ出力」を行う。
VOD_OUTPUT_SIZE_FACTOR = 1.1  # 想定出力量 = 元MP4の合計サイズ × この係数(HLS(.ts)化のオーバーヘッド分)
VOD_QUEUE_MAX = 6  # VOD生成の順番待ちの最大件数(カメラ3台×2日分程度。超えた要求は断る)
VOD_QUEUED_RETRY_AFTER_SEC = 60  # 順番待ち・混雑で断ったときに返す Retry-After(秒)
VOD_STDERR_TAIL_LINES = 20  # ffmpeg終了時にログへ出すstderr末尾の行数

# 不具合修正: ffmpegのRTSP入力にはioタイムアウトが既定で設定されておらず、
# TCP接続は張れるが応答が返らない種類のネットワーク不調(実機で駐車場カメラの
# ネットワーク瞬断後に確認)に遭遇すると、ffmpegプロセスはエラーも出さず
# 無期限にハングする。`_start_hls_stream_locked`の`existing.poll() is None`
# チェックはOSプロセスの生死しか見ないため、ハングしたプロセスを「配信中」と
# 誤認して再起動されず、ライブのはずの映像が固まった時点の映像(録画のように
# 見える)を配信し続けてしまう。RTSP/tcpプロトコル自身のソケットI/Oタイムアウト
# である`-timeout`(マイクロ秒)を設定し、応答が無ければffmpeg自身が終了する
# ようにすることで、次のリクエストで`existing.poll() is not None`となり
# 新しいプロセスが起動されるようにする。
# 注意: 汎用オプションの`-rw_timeout`は`async:`プロトコル専用であり、
# RTSP/tcpの読み取りには効かないことを実機相当の検証で確認済み(疎通不能な
# ホストに対して待ち続け、この`-timeout`だけが指定通りに機能する)。
FFMPEG_RTSP_TIMEOUT_USEC = 15_000_000  # ライブRTSP入力のI/Oタイムアウト(15秒)

# #882: `-timeout`でも検知できないハング(プロセスは生きているのにセグメントが
# 書かれない)に備え、プレイリストの更新が止まって一定時間を超えたら、生きて
# いるffmpegでも強制終了して再起動する。セグメント長2秒・RTSPタイムアウト15秒
# より十分長く取り、起動直後や一時的な遅延での誤再起動を避ける。
HLS_LIVE_STALL_SECONDS = 30
HLS_LIVE_KILL_WAIT_SECONDS = 5  # 停滞したffmpegをkillした後の終了待ち(秒)

_active_processes: Dict[str, subprocess.Popen] = {}
_live_started_at: dict[str, float] = {}  # cam_id -> ライブffmpegの起動時刻(monotonicではなくtime.time())
_active_vod_processes: Dict[str, subprocess.Popen] = {} # VOD排他制御用の辞書を追加
# Issue #1088: VOD生成の順番待ち(process_key -> (cam_conf, target_date))。挿入順=実行順。
_vod_queue: "collections.OrderedDict[str, Tuple[Dict[str, Any], str]]" = collections.OrderedDict()
_rtsp_cache: Dict[str, str] = {}

# #439: 上記3辞書はFastAPIの同期エンドポイント(スレッドプール実行)から複数スレッドで
# 同時に読み書きされうる。個々のdict操作自体はGILにより原子的だが、
# 「チェックしてから更新する」までの一連の操作は原子的ではなく、既に終了した
# プロセスをactiveと誤認したり、プロセス管理情報が不整合になったりし得るため、
# 読み書きの一連の操作をこのLockで保護する。
_state_lock = threading.Lock()

# VOD生成のcheck-then-act競合(同一cam_id・日付への同時リクエストで
# ffmpegが二重起動し同一ファイルへ書き込む)を防ぐための、process_key単位ロック。
#
# #247: _active_vod_processesには対応する_prune_finished_vod_processes()が
# あるが、以前はこの辞書には剪定処理が存在せず、cam_id×target_dateの組み合わせが
# 増えるたびに(long-running環境で日々)無限に蓄積していた。threading.Lockオブジェクト
# 自体は軽量なため実運用上のメモリ影響は小さいが、参照カウント方式を導入し、
# そのエントリを誰も使用していない(参照カウント0)場合にのみ辞書から削除する。
# 単純に「lock.locked()がFalseなら削除」する方式だと、取得元(_vod_generation_lock)が
# 辞書からロックオブジェクトを取り出した直後・実際にwith文で獲得する直前の隙間で
# 別スレッドが剪定してしまい、同一process_keyに対して2つの別々のLockオブジェクトが
# 生成されて同時に「取得成功」してしまう(このロック機構が本来防ぐべき二重起動と
# 全く同じ問題を再発させる)ため、参照カウントで安全性を担保している。
# Issue #661: このファイルにあった _RefCountedLock と、それを使う2つの
# コンテキストマネージャは core.utils.RefCountedLockRegistry と同一の実装だった
# (同レジストリの docstring 自身がこの実装を参照元として挙げている)。
# 重複を解消し、レジストリ側に一本化する。挙動は変わらない。
_vod_generation_locks = RefCountedLockRegistry()

# Issue #1088: 全カメラ・全日付を通じてVOD生成のffmpegを同時1本に制限するためのロック。
# 「実行中のVOD生成があるか」の判定→起動・登録/順番待ちへの追加、および生成終了時の
# 「次の順番待ちを起動」を、このロックの下で不可分に行う(判定と起動の隙間に2本目が
# 起動されないように)。生成そのもの(数十分)の間は保持しない。
# ロック順序は _vod_generation_lock(process_key) → _vod_scheduler_lock → _state_lock。
_vod_scheduler_lock = threading.RLock()


class VodGenerationUnavailable(Exception):
    """録画VODの生成を今は始められない(順番待ち・混雑・空き容量不足)ことを表す(Issue #1088)。
    ルーターはこれを 503 に変換する。retry_after は再試行までの目安秒数(不明なら None)。"""

    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


@contextlib.contextmanager
def _vod_generation_lock(process_key: str):
    """process_key単位で排他制御を行うコンテキストマネージャ。
    使用中(参照カウント>0)のエントリは剪定されず、使用を終えた
    (参照カウントが0に戻った)エントリのみ_vod_generation_locksから削除される。"""
    with _vod_generation_locks.acquire(process_key):
        yield


# #439: ライブHLS配信の起動も、同一cam_idへの同時リクエストで「実行中でない」の
# チェックとffmpeg起動・登録までを不可分にする必要がある(_vod_generation_lockと同じ
# check-then-act競合)。cam_id単位の参照カウント付きロックとして同じ仕組みを流用する。
_live_stream_locks = RefCountedLockRegistry()


@contextlib.contextmanager
def _live_stream_lock(cam_id: str):
    """cam_id単位でライブHLS配信の起動を排他制御するコンテキストマネージャ。"""
    with _live_stream_locks.acquire(cam_id):
        yield


def stop_all_processes(timeout: float = 5.0) -> int:
    """ライブ配信・VOD生成の全 ffmpeg 子プロセスを停止する(サーバー終了時に呼ぶ)。

    #360: 以前は lifespan の終了処理が scheduler/camera_monitor しか止めておらず、
    ffmpeg が孤児化して再起動後の新 ffmpeg と同じ HLS パスへ二重書き込みし、
    再生が破損していた。停止できたプロセス数を返す。
    """
    stopped = 0
    # Issue #1088: 先に順番待ちを空にし、停止したVOD生成の終了を契機に次の生成が
    # 起動されないようにする。
    with _vod_scheduler_lock:
        _vod_queue.clear()
    for registry in (_active_processes, _active_vod_processes):
        with _state_lock:
            items = list(registry.items())
        for key, proc in items:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    stopped += 1
            except Exception as e:
                logger.warning(f"ffmpeg プロセス停止に失敗 ({key}): {e}")
            with _state_lock:
                registry.pop(key, None)
                if registry is _active_processes:
                    _live_started_at.pop(key, None)
    if stopped:
        logger.info(f"🛑 ffmpeg プロセスを {stopped} 件停止しました")
    return stopped


def _prune_finished_vod_processes() -> None:
    """完了済み(poll()がNoneでない)プロセスを_active_vod_processesから除去する。
    キーがcam_id×target_dateの組み合わせのため、剪定しないと日々増え続けて
    無限に蓄積してしまう。"""
    with _state_lock:
        finished_keys = [key for key, proc in _active_vod_processes.items() if proc.poll() is not None]
        for key in finished_keys:
            _active_vod_processes.pop(key, None)


def _mask_rtsp_url_for_log(url: str) -> str:
    """RTSP URLの認証情報(user:pass)をログ出力用にマスクする。
    パスワードが空文字の場合、str.replace('', '***')は文字列の全文字間に
    '***'を挿入して破壊する(Pythonの仕様)ため、urlparseでnetloc部分のみ
    安全に再構築する。"""
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.username and not parsed.password:
            return url
        host_part = parsed.hostname or ""
        if parsed.port:
            host_part = f"{host_part}:{parsed.port}"
        masked_netloc = f"***:***@{host_part}" if parsed.password else f"***@{host_part}"
        return parsed._replace(netloc=masked_netloc).geturl()
    except Exception:
        return "***"

def init_output_dir(base_dir: str, camera_id: str) -> str:
    cam_dir = os.path.join(base_dir, camera_id)
    os.makedirs(cam_dir, exist_ok=True)
    return cam_dir


def get_rtsp_url(cam_conf: Dict[str, Any]) -> str:
    cam_id = cam_conf['id']
    with _state_lock:
        cached = _rtsp_cache.get(cam_id)
    if cached:
        return cached

    if cam_conf.get("rtsp_url"):
        with _state_lock:
            _rtsp_cache[cam_id] = cam_conf["rtsp_url"]
        return cam_conf["rtsp_url"]

    try:
        wsdl_path = find_wsdl_path()
        if not wsdl_path:
            raise FileNotFoundError("WSDL directory not found in sys.path")

        mycam = ONVIFCamera(cam_conf['ip'], cam_conf.get('port', 80), cam_conf['user'], cam_conf.get('pass', ''), wsdl_dir=wsdl_path)
        media_service = mycam.create_media_service()
        profiles = media_service.GetProfiles()
        token = profiles[0].token

        req = media_service.create_type('GetStreamUri')
        req.ProfileToken = token
        req.StreamSetup = {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}}
        
        res = media_service.GetStreamUri(req)
        uri = res.Uri

        parsed = urllib.parse.urlparse(uri)
        # URLセーフな形式にエンコード（safe='' を指定してすべての記号をエンコード）
        safe_user = urllib.parse.quote(cam_conf['user'], safe='')
        safe_pass = urllib.parse.quote(cam_conf.get('pass', ''), safe='')
        
        auth_uri = f"rtsp://{safe_user}:{safe_pass}@{parsed.netloc}{parsed.path}?{parsed.query}"
        
        with _state_lock:
            _rtsp_cache[cam_id] = auth_uri
        return auth_uri
    except Exception as e:
        logger.error(f"❌ [{cam_conf['name']}] ONVIF経由のRTSP URL取得に失敗: {e}")
        raise

def start_hls_stream(cam_conf: Dict[str, Any]) -> str:
    cam_id = cam_conf['id']
    # #439: 同一cam_idへの同時リクエストで「実行中でないチェック→ffmpeg起動・登録」が
    # 競合しないよう、cam_id単位でこの一連の処理全体を排他する。
    with _live_stream_lock(cam_id):
        return _start_hls_stream_locked(cam_conf, cam_id)


def _remove_stale_live_outputs(cam_dir: str) -> None:
    """前回のffmpegが残したライブHLSの出力(stream.m3u8・セグメント.ts)を削除する(#882)。

    ffmpegは`delete_segments`でも終了時に最後のプレイリストと直近のセグメントを
    ディスクに残す(正常終了なら`#EXT-X-ENDLIST`付きのVOD扱いになる)。新しいffmpegを
    起動してもプレイリストが書き換わるのは最初のセグメントを書き終えた後なので、
    それまでの間、ルーターの「プレイリストが存在すれば即返す」待機ループが古い
    プレイリストを返し、前回終了時点(例: 前日22時)の映像が再生されていた。
    起動前に消しておけば、ルーターは新しいffmpegが書いたプレイリストだけを返す。
    """
    try:
        names = os.listdir(cam_dir)
    except OSError:
        return
    for name in names:
        if name == "stream.m3u8" or name.endswith((".ts", ".m3u8.tmp")):
            try:
                os.remove(os.path.join(cam_dir, name))
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning(f"⚠️ 古いライブHLSファイルの削除に失敗: {name}: {e}")


def _is_live_stalled(playlist_path: str, started_at: float | None) -> bool:
    """生存中のライブffmpegが、プレイリストを更新しなくなったか(ハングか)を判定する(#882)。

    プレイリストがあればその更新時刻、まだ無ければ起動時刻を基準にする。
    どちらも分からない場合(起動時刻の記録が無い等)は、誤って再起動しないよう停滞なしとする。
    """
    try:
        reference = os.path.getmtime(playlist_path)
    except OSError:
        reference = started_at
    if reference is None:
        return False
    return time.time() - reference > HLS_LIVE_STALL_SECONDS


def _start_hls_stream_locked(cam_conf: Dict[str, Any], cam_id: str) -> str:
    cam_dir = init_output_dir(HLS_LIVE_DIR, cam_id)
    playlist_path = os.path.join(cam_dir, "stream.m3u8")

    with _state_lock:
        existing = _active_processes.get(cam_id)
        started_at = _live_started_at.get(cam_id)
    if existing is not None and existing.poll() is None:
        if not _is_live_stalled(playlist_path, started_at):
            return playlist_path
        logger.warning(
            f"⚠️ [{cam_conf['name']}] ライブHLSの更新が{HLS_LIVE_STALL_SECONDS}秒以上止まっているため"
            "ffmpegを再起動します(ハング検知)"
        )
        try:
            existing.kill()
            existing.wait(timeout=HLS_LIVE_KILL_WAIT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning(f"⚠️ 停滞したffmpegの停止に失敗: {e}")

    try:
        rtsp_url = get_rtsp_url(cam_conf)
    except Exception:
        return ""

    logger.info(f"🎥 [{cam_conf['name']}] ライブHLS配信を開始 (RTSP: {_mask_rtsp_url_for_log(rtsp_url)})")

    # 前回のffmpegの出力が残っていると、新しいffmpegが最初のセグメントを書くまでの
    # 間に古い映像のプレイリストが返ってしまうため、起動前に消す(#882)。
    _remove_stale_live_outputs(cam_dir)

    cmd = [
        "nice", "-n", str(FFMPEG_NICE_LEVEL),
        "ffmpeg", "-y",
        # -hide_banner/-loglevel error: 認証情報込みのRTSP URLが
        # ffmpeg自身の起動バナー("Input #0, rtsp, from 'rtsp://user:pass@...'")
        # 経由でffmpeg.logに平文出力されるのを防ぐ。
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-timeout", str(FFMPEG_RTSP_TIMEOUT_USEC),
        "-i", rtsp_url,
        "-c:v", "copy",
        "-an",
        "-f", "hls",
        "-hls_time", str(HLS_LIVE_SEGMENT_SECONDS),
        "-hls_list_size", str(HLS_LIVE_LIST_SIZE),
        "-hls_flags", "delete_segments",
        playlist_path
    ]

    # FFmpegのエラーを追えるようにログファイルへ出力。
    # 他ローカルユーザーからの閲覧を防ぐため所有者のみ読み書き可能にする。
    log_path = os.path.join(cam_dir, "ffmpeg.log")
    log_file = open(log_path, "w")
    try:
        os.chmod(log_path, FFMPEG_LOG_FILE_MODE)
    except OSError:
        pass
    try:
        process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
    finally:
        # 子プロセスがdup()で自分のfdを持つため、親側はPopen呼び出し後に
        # 閉じてよい。閉じないと、プロセスがクラッシュして再起動されるたびに
        # ファイルハンドルがプロセス内に蓄積してリークする。
        log_file.close()
    with _state_lock:
        _active_processes[cam_id] = process
        _live_started_at[cam_id] = time.time()
    return playlist_path

def get_record_start_offset(cam_conf: Dict[str, Any], target_date: str) -> int:
        """指定日の最初の録画ファイルの開始時刻を0時からの秒数で返す"""
        nas_folder_name = cam_conf.get("nas_folder", cam_conf["name"])
        nvr_base_dir = config.NVR_RECORD_DIR
        search_dir = os.path.join(nvr_base_dir, nas_folder_name)
        
        search_pattern = os.path.join(search_dir, f"{target_date}_*.mp4")
        mp4_files = sorted(glob.glob(search_pattern))
        
        if not mp4_files:
            return 0
            
        try:
            first_file = os.path.basename(mp4_files[0])
            time_str = first_file.split("_")[1].split(".")[0]
            dt = datetime.strptime(time_str, "%H%M%S")
            return dt.hour * 3600 + dt.minute * 60 + dt.second
        except Exception as e:
            logger.warning(f"Failed to parse start offset for {cam_conf['name']}: {e}")
            return 0


def _playlist_is_complete(path: str) -> bool:
    """m3u8ファイルにffmpegの`-hls_playlist_type vod`が処理完走時のみ末尾へ
    書き込む`#EXT-X-ENDLIST`タグが存在するかを確認する(#562)。
    シャットダウン時のterminate等で生成途中のまま終わったプレイリストは
    このタグを持たないため、過去日付キャッシュとして返してよいかの判定に使う。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return "#EXT-X-ENDLIST" in f.read()
    except OSError:
        return False


def generate_record_playlist(cam_conf: Dict[str, Any], target_date: str) -> Optional[str]:
    """
    指定された日付の録画ファイル群を結合し、シームレス再生用のVODプレイリストを生成する
    target_date 形式: YYYYMMDD (例: 20260716)
    """
    cam_id = cam_conf['id']
    process_key = f"{cam_id}_{target_date}"

    # process_key単位でロックし、以降の「実行中チェック→未実行ならPopen起動・登録」を
    # 単一の原子的な区間にする。ロック無しだと、同一カメラ・日付への同時リクエストが
    # どちらも「実行中でない」と判定してffmpegを二重起動し、同一ファイルへ競合書き込みし得た。
    with _vod_generation_lock(process_key):
        return _generate_record_playlist_locked(cam_conf, target_date, process_key)


def _reusable_record_playlist(cam_conf: Dict[str, Any], target_date: str, playlist_path: str) -> Optional[str]:
    """既存のプレイリストを再エンコードせずに返してよければそのパスを、そうでなければ None を返す。"""
    # 2. キャッシュ: 過去日付（録画が確定済み）かつ既にプレイリストが生成済みであれば、再エンコードせずそれを返す
    #    当日分は録画ファイルが増え続けるため、キャッシュ対象から除外し毎回最新の状態で再生成する
    #    #562: シャットダウン時のffmpeg terminate(Issue #360系)や、下記の生成待機タイムアウトに
    #    より生成途中で終わったプレイリストが存在すると、以前は完全性を確認せずそのまま
    #    「完成品」としてHLS_VOD_RETENTION_DAYS(既定3日)の間ずっと配信し続けてしまっていた。
    #    ffmpegは`-hls_playlist_type vod`指定時、正常に完走した場合にのみ末尾へ
    #    `#EXT-X-ENDLIST`タグを書き込むため、これの有無で完全性を確認してからキャッシュを返す。
    #    不完全なら以降の生成処理へフォールスルーし、再生成する。
    # Issue #592: target_dateはフロントエンド(ブラウザのローカル日付、実質JST)から
    # 渡される「YYYYMMDD」形式の日付で、実世界のJSTカレンダー日を意図している。
    # ホストOSのタイムゾーン設定に依存するnaiveなdatetime.now()で today_str を
    # 求めると、ホストがJST以外の設定の場合、JSTの日付境界(UTCの日付境界と
    # 9時間ずれる)をまたぐ時間帯で「当日」の判定を誤りうるため、明示的にJSTを使う。
    today_str = get_now_jst().strftime("%Y%m%d")
    if target_date < today_str and os.path.exists(playlist_path) and _playlist_is_complete(playlist_path):
        logger.debug(f"✅ [{cam_conf['name']}] {target_date} のプレイリストは生成済みのためキャッシュを返します。")
        return playlist_path

    # #359: 当日分は録画が増え続けるためキャッシュ対象外だが、以前はプレイリスト要求の
    # たびに当日の全録画を再多重化していた(連続再生・再読込のたびに数GBを再生成)。
    # 生成完了から VOD_TODAY_REUSE_SECONDS 以内なら再利用し、それを過ぎたら再生成する。
    if target_date == today_str and os.path.exists(playlist_path):
        try:
            age = time.time() - os.path.getmtime(playlist_path)
        except OSError:
            age = None
        if age is not None and age < VOD_TODAY_REUSE_SECONDS:
            logger.debug(f"✅ [{cam_conf['name']}] 当日プレイリストは {age:.0f}s 前に生成済みのため再利用します。")
            return playlist_path
    return None


def _segment_duration(mp4: str, next_mp4: Optional[str]) -> float:
    """ffconcat に書く mp4 1本分の duration(次のファイルとの開始時刻の差)を返す。

    最後のファイル(next_mp4 が None)や時刻を読めない場合は既定値を返す。
    """
    duration = DEFAULT_SEGMENT_DURATION_SECONDS
    if next_mp4 is None:
        return duration
    try:
        # 20260720_210200.mp4 のようなファイル名から時刻部分(210200)を抽出
        curr_time_str = os.path.basename(mp4).split("_")[1].split(".")[0]
        next_time_str = os.path.basename(next_mp4).split("_")[1].split(".")[0]
        
        dt_curr = datetime.strptime(curr_time_str, "%H%M%S")
        dt_next = datetime.strptime(next_time_str, "%H%M%S")
        
        diff_seconds = (dt_next - dt_curr).total_seconds()
        
        # 0秒以上かつ異常値(12時間以上等)でなければ、実時間差をdurationに設定
        # これにより、ファイルが欠落している隙間は最終フレームで停止したまま時間を稼ぐ
        if 0 < diff_seconds <= MAX_PLAUSIBLE_SEGMENT_GAP_SECONDS:
            duration = diff_seconds
    except Exception as e:
        logger.warning(f"Failed to calculate duration for {mp4}: {e}")
    return duration


def _write_ffconcat_file(concat_file_path: str, mp4_files: List[str]) -> None:
    """ffmpegのconcatファイルリスト作成 (ffconcat version 1.0 を使用しタイムラインを補正)"""
    with open(concat_file_path, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for i, mp4 in enumerate(mp4_files):
            next_mp4 = mp4_files[i + 1] if i < len(mp4_files) - 1 else None
            f.write(f"file '{mp4}'\n")
            # 次のファイルとの時間差を計算し、動画の再生時間(duration)を明示する
            f.write(f"duration {_segment_duration(mp4, next_mp4)}\n")


def _build_vod_ffmpeg_cmd(concat_file_path: str, playlist_path: str) -> List[str]:
    """ffconcat のリストを再エンコードせずに HLS(VOD)へ多重化する ffmpeg コマンド。

    Issue #1088: SQLite と同じSDカードへ大量に書き込むため、I/O優先度を idle クラス
    (`ionice -c3`)に下げる(ionice が無い環境では付けない。smart_timelapse_generator と
    同じ判定)。また `-readrate`(config.HLS_VOD_READRATE、0以下で無効)で入力の読み込み
    速度=書き込み帯域に上限をかける。stderr は終了時にログへ末尾を出すため、量を
    warning 以上に絞る。プレイリストのパスは最後の引数に置く(呼び出し側のテストが前提)。
    """
    cmd: List[str] = []
    if shutil.which("ionice"):
        cmd += ["ionice", "-c3"]
    cmd += [
        "nice", "-n", str(FFMPEG_NICE_LEVEL),
        "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning", "-y",
    ]
    readrate = config.HLS_VOD_READRATE
    if readrate > 0:
        cmd += ["-readrate", f"{readrate:g}"]
    cmd += [
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file_path,
        "-c:v", "copy",
        "-an",
        "-f", "hls",
        "-hls_time", str(HLS_VOD_SEGMENT_SECONDS),
        "-hls_playlist_type", "vod",
        playlist_path
    ]
    return cmd


def _find_record_mp4_files(cam_conf: Dict[str, Any], target_date: str) -> Optional[List[str]]:
    """指定日の10分分割mp4ファイル一覧(昇順)を返す。保存先や録画が無ければ警告して None。"""
    nas_folder_name = cam_conf.get("nas_folder", cam_conf["name"])

    # NVRの保存ディレクトリ。#405: 以前は getattr(config, ..., os.getenv("NVR_RECORD_DIR", ...)) で
    # config を経由しない環境変数読み取りのフォールバックを持っていたが、config.NVR_RECORD_DIR は
    # 常に定義されるためフォールバックは到達不能で、.env.example 整合テストの死角になっていた。
    nvr_base_dir = config.NVR_RECORD_DIR
    search_dir = os.path.join(nvr_base_dir, nas_folder_name)

    if not os.path.exists(search_dir):
        logger.warning(f"⚠️ [{cam_conf['name']}] 録画保存先が存在しません: {search_dir}")
        return None

    search_pattern = os.path.join(search_dir, f"{target_date}_*.mp4")
    mp4_files = sorted(glob.glob(search_pattern))

    if not mp4_files:
        logger.warning(f"⚠️ [{cam_conf['name']}] {target_date} の録画ファイルが存在しません")
        return None
    return mp4_files


_BYTES_PER_GB = 1024 ** 3


def _total_size_bytes(paths: List[str]) -> int:
    total = 0
    for path in paths:
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return total


def _estimated_vod_output_bytes(mp4_files: List[str]) -> int:
    """VOD生成の想定出力量。`-c:v copy` の再多重化なので元MP4の合計 × 係数とする。"""
    return int(_total_size_bytes(mp4_files) * VOD_OUTPUT_SIZE_FACTOR)


def _check_vod_disk_space(cam_dir: str, mp4_files: List[str]) -> Tuple[int, int]:
    """VOD生成の前に出力先の空き容量を確認する(Issue #1088)。

    `-c:v copy` の再多重化なので出力量は元MP4の合計とほぼ同じになる。
    「空き容量 − 想定出力量」が config.HLS_VOD_MIN_FREE_GB を下回るなら
    VodGenerationUnavailable を送出する。空き容量を確認できないときも安全側に倒して断る。
    問題なければ (空き容量, 想定出力量) をバイトで返す。
    """
    estimated = _estimated_vod_output_bytes(mp4_files)
    try:
        free = shutil.disk_usage(cam_dir).free
    except OSError as e:
        raise VodGenerationUnavailable(f"録画プレイリストの出力先の空き容量を確認できません: {e}")
    min_free = config.HLS_VOD_MIN_FREE_GB * _BYTES_PER_GB
    if free - estimated < min_free:
        raise VodGenerationUnavailable(
            "空き容量が不足しているため録画プレイリストを生成しません "
            f"(空き {free / _BYTES_PER_GB:.1f}GB − 想定出力 {estimated / _BYTES_PER_GB:.1f}GB"
            f" < 下限 {config.HLS_VOD_MIN_FREE_GB:g}GB)"
        )
    return free, estimated


# Issue #1088: VODキャッシュの1エントリ(カメラ×日付)を構成するファイル名。
# record_<日付>.m3u8 / record_<日付>N.ts(ffmpegのHLS出力)と concat_<日付>.txt。
_VOD_CACHE_FILE_RE = re.compile(r"^(?:record_|concat_)(\d{8})")


def _vod_cache_entries() -> Dict[str, Tuple[int, float, List[str]]]:
    """VODキャッシュを process_key(`<cam_id>_<日付>`)単位に集計する。
    値は (合計バイト, 最も新しい更新時刻, ファイルパス一覧)。読めないファイルは数えない。"""
    entries: Dict[str, Tuple[int, float, List[str]]] = {}
    try:
        cam_ids = os.listdir(HLS_VOD_DIR)
    except OSError:
        return entries
    for cam_id in cam_ids:
        cam_dir = os.path.join(HLS_VOD_DIR, cam_id)
        if not os.path.isdir(cam_dir):
            continue
        try:
            names = os.listdir(cam_dir)
        except OSError:
            continue
        for name in names:
            m = _VOD_CACHE_FILE_RE.match(name)
            if not m:
                continue
            path = os.path.join(cam_dir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = f"{cam_id}_{m.group(1)}"
            size, mtime, paths = entries.get(key, (0, 0.0, []))
            paths.append(path)
            entries[key] = (size + st.st_size, max(mtime, st.st_mtime), paths)
    return entries


def _enforce_vod_cache_limit(process_key: str, estimated: int) -> None:
    """VODキャッシュ全体を config.HLS_VOD_CACHE_MAX_GB 以下に保つ(Issue #1088)。

    「既存キャッシュの合計 + 今回の想定出力」が上限を超えるなら、更新時刻の古い
    カメラ×日付のキャッシュから削除する。実行中の生成と、今回生成する process_key
    自身(同名ファイルで上書きされる)は数えず、消さない。今回の想定出力だけで上限を
    超える、または消せるものを全部消しても収まらないなら、何も消さずに
    VodGenerationUnavailable を送出する。上限が0以下なら何もしない。
    _vod_scheduler_lock を保持した状態で呼ぶこと。
    """
    max_gb = config.HLS_VOD_CACHE_MAX_GB
    if max_gb <= 0:
        return
    limit = round(max_gb * _BYTES_PER_GB)
    if estimated > limit:
        raise VodGenerationUnavailable(
            "録画プレイリストの想定出力がVODキャッシュの上限を超えるため生成しません "
            f"(想定出力 {estimated / _BYTES_PER_GB:.1f}GB > 上限 {max_gb:g}GB)"
        )
    protected = set(_running_vod_keys()) | {process_key}
    entries = {k: v for k, v in _vod_cache_entries().items() if k != process_key}
    used = sum(size for size, _, _ in entries.values())
    if used + estimated <= limit:
        return

    evictable = sorted(
        ((k, v) for k, v in entries.items() if k not in protected),
        key=lambda kv: kv[1][1],
    )
    victims: List[Tuple[str, int, List[str]]] = []
    remaining = used
    for key, (size, _mtime, paths) in evictable:
        if remaining + estimated <= limit:
            break
        victims.append((key, size, paths))
        remaining -= size
    if remaining + estimated > limit:
        raise VodGenerationUnavailable(
            "VODキャッシュの上限に収まらないため録画プレイリストを生成しません "
            f"(使用中 {used / _BYTES_PER_GB:.1f}GB(うち生成中で消せない分 "
            f"{remaining / _BYTES_PER_GB:.1f}GB) + 想定出力 {estimated / _BYTES_PER_GB:.1f}GB"
            f" > 上限 {max_gb:g}GB)"
        )

    for key, size, paths in victims:
        for path in paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning(f"⚠️ VODキャッシュの削除に失敗しました ({path}): {e}")
        logger.info(
            f"🧹 VODキャッシュの上限 {max_gb:g}GB に収めるため {key} のキャッシュ"
            f"({size / _BYTES_PER_GB:.2f}GB)を削除しました"
        )


def _running_vod_keys() -> List[str]:
    """ffmpegが実行中(poll()がNone)のVOD生成の process_key 一覧。"""
    with _state_lock:
        return [key for key, proc in _active_vod_processes.items() if proc.poll() is None]


def _vod_output_bytes(cam_dir: str, target_date: str) -> int:
    """生成済みのVOD出力(プレイリストと .ts セグメント)の合計サイズ。"""
    return _total_size_bytes(glob.glob(os.path.join(cam_dir, f"record_{target_date}*")))


def _log_vod_process_exit(cam_name: str, target_date: str, returncode: Any, elapsed: float,
                          output_bytes: int, stderr_tail: List[str]) -> None:
    """VOD生成ffmpegの終了(終了コード・所要時間・出力サイズ・stderr末尾)をログに出す(Issue #1088)。
    以前は stdout/stderr を DEVNULL に捨てていて、生成が終わったことすらログから分からなかった。"""
    summary = (
        f"(終了コード {returncode}、所要 {elapsed:.0f}秒、"
        f"出力 {output_bytes / _BYTES_PER_GB:.2f}GB)"
    )
    tail = ("\nffmpeg stderr 末尾:\n" + "\n".join(stderr_tail)) if stderr_tail else ""
    if returncode == 0:
        logger.info(f"✅ [{cam_name}] {target_date} の録画プレイリスト生成が完了しました {summary}{tail}")
    else:
        logger.warning(f"❌ [{cam_name}] {target_date} の録画プレイリスト生成が異常終了しました {summary}{tail}")


def _watch_vod_process(process: subprocess.Popen, cam_name: str, target_date: str, process_key: str,
                       cam_dir: str, started_at: float) -> None:
    """VOD生成ffmpegの終了を待ってログに出し、順番待ちの次の生成を起動する(監視スレッド本体)。

    stderr はパイプが詰まってffmpegが止まらないよう終了まで読み続け、末尾だけを残す。
    """
    tail: "collections.deque[str]" = collections.deque(maxlen=VOD_STDERR_TAIL_LINES)
    returncode: Any = None
    try:
        stream = getattr(process, "stderr", None)
        if stream is not None:
            for raw in stream:
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                line = line.rstrip()
                if line:
                    tail.append(line)
            try:
                stream.close()
            except Exception:
                pass
        returncode = process.wait()
    except Exception as e:
        logger.warning(f"⚠️ 録画プレイリスト生成の終了待ちに失敗しました ({process_key}): {e}")
        returncode = getattr(process, "returncode", None)
    finally:
        try:
            _log_vod_process_exit(cam_name, target_date, returncode, time.monotonic() - started_at,
                                  _vod_output_bytes(cam_dir, target_date), list(tail))
        finally:
            _on_vod_process_exit(process_key, process)


def _start_vod_watcher(process: subprocess.Popen, cam_conf: Dict[str, Any], target_date: str,
                       process_key: str, cam_dir: str, started_at: float) -> None:
    threading.Thread(
        target=_watch_vod_process,
        args=(process, cam_conf["name"], target_date, process_key, cam_dir, started_at),
        name=f"vod-watch-{process_key}",
        daemon=True,
    ).start()


def _launch_vod_generation_locked(cam_conf: Dict[str, Any], target_date: str, process_key: str,
                                  mp4_files: List[str]) -> None:
    """VODキャッシュの上限・空き容量を確認してVOD生成のffmpegを起動・登録し、監視スレッドを付ける。
    _vod_scheduler_lock を保持した状態で呼ぶこと。上限超過・容量不足なら VodGenerationUnavailable。"""
    cam_dir = init_output_dir(HLS_VOD_DIR, cam_conf["id"])
    # キャッシュの上限を先に確認する(古いキャッシュを消せば空き容量チェックにも効く)
    _enforce_vod_cache_limit(process_key, _estimated_vod_output_bytes(mp4_files))
    free, estimated = _check_vod_disk_space(cam_dir, mp4_files)
    _vod_queue.pop(process_key, None)

    concat_file_path = os.path.join(cam_dir, f"concat_{target_date}.txt")
    playlist_path = os.path.join(cam_dir, f"record_{target_date}.m3u8")
    _write_ffconcat_file(concat_file_path, mp4_files)

    logger.info(
        f"🎞️ [{cam_conf['name']}] {target_date} の録画プレイリスト生成中... "
        f"(元動画 {len(mp4_files)}本、想定出力 {estimated / _BYTES_PER_GB:.1f}GB、"
        f"空き {free / _BYTES_PER_GB:.1f}GB)"
    )

    cmd = _build_vod_ffmpeg_cmd(concat_file_path, playlist_path)

    # subprocess.run (ブロック) ではなく Popen (非同期) で起動し、プロセスを登録する。
    # 終了の検知・ログ出力・次の順番待ちの起動は監視スレッドが行う(Issue #1088)。
    started_at = time.monotonic()
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    with _state_lock:
        _active_vod_processes[process_key] = process
    _start_vod_watcher(process, cam_conf, target_date, process_key, cam_dir, started_at)


def _enqueue_vod_job_locked(cam_conf: Dict[str, Any], target_date: str, process_key: str,
                            running_keys: List[str]) -> None:
    """VOD生成を順番待ちに入れ、VodGenerationUnavailable を送出する(戻らない)。
    _vod_scheduler_lock を保持した状態で呼ぶこと。"""
    if process_key not in _vod_queue:
        if len(_vod_queue) >= VOD_QUEUE_MAX:
            raise VodGenerationUnavailable(
                f"録画プレイリストの生成待ちが混雑しています(待ち {len(_vod_queue)}件)。しばらくしてから再試行してください",
                retry_after=VOD_QUEUED_RETRY_AFTER_SEC,
            )
        _vod_queue[process_key] = (cam_conf, target_date)
        logger.info(
            f"⏳ [{cam_conf['name']}] {target_date} の録画プレイリスト生成を順番待ちに入れました "
            f"(実行中: {', '.join(running_keys)}、待ち {len(_vod_queue)}件)"
        )
    position = list(_vod_queue).index(process_key) + 1
    raise VodGenerationUnavailable(
        f"他の録画プレイリストを生成中のため順番待ちです({position}番目)。しばらくしてから再試行してください",
        retry_after=VOD_QUEUED_RETRY_AFTER_SEC,
    )


def _dispatch_next_vod_job_locked() -> None:
    """実行中のVOD生成が無ければ、順番待ちの先頭から起動できるものを1件起動する。
    _vod_scheduler_lock を保持した状態で呼ぶこと。"""
    while _vod_queue:
        if _running_vod_keys():
            return
        process_key, (cam_conf, target_date) = _vod_queue.popitem(last=False)
        try:
            mp4_files = _find_record_mp4_files(cam_conf, target_date)
            if not mp4_files:
                continue
            _launch_vod_generation_locked(cam_conf, target_date, process_key, mp4_files)
            return
        except VodGenerationUnavailable as e:
            logger.warning(f"⚠️ 順番待ちの録画プレイリスト生成を取りやめました ({process_key}): {e.message}")
        except Exception as e:
            logger.error(f"❌ 順番待ちの録画プレイリスト生成の起動に失敗しました ({process_key}): {e}")


def _on_vod_process_exit(process_key: str, process: subprocess.Popen) -> None:
    """VOD生成ffmpegの終了時に登録を外し、順番待ちの次の生成を起動する。"""
    with _vod_scheduler_lock:
        with _state_lock:
            if _active_vod_processes.get(process_key) is process:
                _active_vod_processes.pop(process_key, None)
        _dispatch_next_vod_job_locked()


def _wait_for_playlist(playlist_path: str) -> Optional[str]:
    """フロントエンドが404にならないよう、プレイリストファイルが生成されるまで少し待機する。"""
    for _ in range(PLAYLIST_WAIT_MAX_ATTEMPTS):
        if os.path.exists(playlist_path):
            return playlist_path
        time.sleep(PLAYLIST_WAIT_POLL_INTERVAL_SEC)
    return playlist_path if os.path.exists(playlist_path) else None


def _generate_record_playlist_locked(cam_conf: Dict[str, Any], target_date: str, process_key: str) -> Optional[str]:
    mp4_files = _find_record_mp4_files(cam_conf, target_date)
    if not mp4_files:
        return None

    cam_dir = init_output_dir(HLS_VOD_DIR, cam_conf["id"])
    playlist_path = os.path.join(cam_dir, f"record_{target_date}.m3u8")

    # 完了済みプロセスを剪定してから登録状況を確認する(無限蓄積の防止)
    _prune_finished_vod_processes()

    # Issue #1088: 「実行中か」の判定から起動・順番待ちへの追加までを全カメラ共通の
    # ロックの下で行い、VOD生成のffmpegが同時に2本以上走らないようにする。
    with _vod_scheduler_lock:
        running_keys = _running_vod_keys()
        already_running = process_key in running_keys
        if not already_running:
            cached = _reusable_record_playlist(cam_conf, target_date, playlist_path)
            if cached is not None:
                return cached
            if running_keys:
                # 他のカメラ・日付を生成中: 順番待ちに入れて 503 で返す(戻らない)
                _enqueue_vod_job_locked(cam_conf, target_date, process_key, running_keys)
            try:
                _launch_vod_generation_locked(cam_conf, target_date, process_key, mp4_files)
            except VodGenerationUnavailable as e:
                logger.warning(f"💾 [{cam_conf['name']}] {target_date}: {e.message}")
                raise

    if already_running:
        # 1. 排他制御: 既に同じカメラ・日付の変換プロセスが実行中なら、新たに起動せず
        #    フロントエンドが500エラー（FileResponseのクラッシュ）にならないよう生成を待機する
        logger.info(f"⏳ [{cam_conf['name']}] {target_date} の録画プレイリスト生成は既に実行中です。")
    # ロックの外で待つ(待機中に他カメラの要求を止めない)
    return _wait_for_playlist(playlist_path)


def get_camera_config_or_none(camera_id: str) -> Optional[Dict[str, Any]]:
    """config.CAMERAS(devices.jsonからロードされたカメラ定義一覧)からcamera_idに
    一致する設定を返す。見つからない場合はNoneを返す(#551: camera_router側に
    重複していた同一のlookup+404送出を、ルーターの`_require_camera`ヘルパーへ
    一元化するために切り出した)。"""
    return next((c for c in config.CAMERAS if c["id"] == camera_id), None)


def set_camera_enabled(camera_id: str, enabled: bool) -> bool:
    """devices.json 上の該当カメラの enabled フラグを更新し、config.CAMERAS にも反映する。
    devices.json が存在しない、または該当カメラが見つからない場合は False を返す。"""
    if not os.path.exists(config.DEVICES_JSON_PATH):
        return False

    with open(config.DEVICES_JSON_PATH, "r", encoding="utf-8") as f:
        devices_data = json.load(f)

    cameras = devices_data.get("cameras", [])
    target = next((c for c in cameras if c.get("id") == camera_id), None)
    if target is None:
        return False

    target["enabled"] = enabled
    # temp+renameでアトミックに書き込む。直接上書きだと、書き込み途中の
    # クラッシュ・電源断でdevices.jsonが壊れ、全カメラ設定が失われ得る。
    tmp_path = f"{config.DEVICES_JSON_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(devices_data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, config.DEVICES_JSON_PATH)

    for cam in config.CAMERAS:
        if cam.get("id") == camera_id:
            cam["enabled"] = enabled
            break

    return True