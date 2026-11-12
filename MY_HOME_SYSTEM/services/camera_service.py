import contextlib
import json
import os
import subprocess
import threading
import time
import urllib.parse
import glob
from datetime import datetime
from typing import Optional, Dict, Any
# #661: WSDL 探索は core/onvif_utils.py に一本化(以前は本ファイルと camera_monitor/camera_service に同一実装が重複)
from core.onvif_utils import find_wsdl_path
from core.logger import setup_logging
from core.utils import RefCountedLockRegistry
import config

try:
    from onvif import ONVIFCamera
except ImportError:
    ONVIFCamera = Any

logger = setup_logging("camera_service")

# /tmp (RAM) から物理ストレージ（プロジェクト直下のdataディレクトリ）へ変更
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
HLS_LIVE_DIR = os.path.join(BASE_DIR, "data", "hls_streams", "live")
# Issue #1091: 録画VODは NAS 上の fragmented MP4 をバイト範囲で直接参照するプレイリスト
# (services/record_playlist_service.py)で配信し、ffmpeg による HLS(.ts) 化は廃止した。

# Issue #451: ffmpeg起動コマンドに直書きされていたマジックナンバー群を集約
FFMPEG_NICE_LEVEL = 15  # ffmpegの優先度(nice値)。他プロセスを圧迫しない程度に低優先度化
HLS_LIVE_SEGMENT_SECONDS = 2  # ライブHLS配信のセグメント長(秒)
HLS_LIVE_LIST_SIZE = 5  # ライブHLS配信でプレイリストに保持するセグメント数
FFMPEG_LOG_FILE_MODE = 0o600  # ffmpeg.logのパーミッション(所有者のみ読み書き可)


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
_rtsp_cache: Dict[str, str] = {}

# #439: 上記3辞書はFastAPIの同期エンドポイント(スレッドプール実行)から複数スレッドで
# 同時に読み書きされうる。個々のdict操作自体はGILにより原子的だが、
# 「チェックしてから更新する」までの一連の操作は原子的ではなく、既に終了した
# プロセスをactiveと誤認したり、プロセス管理情報が不整合になったりし得るため、
# 読み書きの一連の操作をこのLockで保護する。
_state_lock = threading.Lock()

# #439: ライブHLS配信の起動も、同一cam_idへの同時リクエストで「実行中でない」の
# チェックとffmpeg起動・登録までを不可分にする必要がある(同一cam_idへの
# 同時リクエストでffmpegが二重起動する check-then-act 競合)。cam_id単位の参照カウント付きロックとして同じ仕組みを流用する。
_live_stream_locks = RefCountedLockRegistry()


@contextlib.contextmanager
def _live_stream_lock(cam_id: str):
    """cam_id単位でライブHLS配信の起動を排他制御するコンテキストマネージャ。"""
    with _live_stream_locks.acquire(cam_id):
        yield


def stop_all_processes(timeout: float = 5.0) -> int:
    """ライブ配信の全 ffmpeg 子プロセスを停止する(サーバー終了時に呼ぶ)。

    #360: 以前は lifespan の終了処理が scheduler/camera_monitor しか止めておらず、
    ffmpeg が孤児化して再起動後の新 ffmpeg と同じ HLS パスへ二重書き込みし、
    再生が破損していた。停止できたプロセス数を返す。
    """
    stopped = 0
    for registry in (_active_processes,):
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