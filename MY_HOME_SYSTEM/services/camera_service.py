import contextlib
import json
import os
import sys
import subprocess
import threading
import time
import urllib.parse
import glob
from datetime import datetime
from typing import Optional, Dict, Any
from core.logger import setup_logging
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

_active_processes: Dict[str, subprocess.Popen] = {}
_active_vod_processes: Dict[str, subprocess.Popen] = {} # VOD排他制御用の辞書を追加
_rtsp_cache: Dict[str, str] = {}

# VOD生成のcheck-then-act競合(同一cam_id・日付への同時リクエストで
# ffmpegが二重起動し同一ファイルへ書き込む)を防ぐための、process_key単位ロック。
#
# #247: _active_vod_processesには対応する_prune_finished_vod_processes()が
# あるが、以前はこの辞書には剪定処理が存在せず、cam_id×target_dateの組み合わせが
# 増えるたびに(long-running環境で日々)無限に蓄積していた。threading.Lockオブジェクト
# 自体は軽量なため実運用上のメモリ影響は小さいが、参照カウント(_RefCountedLock)を
# 導入し、そのエントリを誰も使用していない(参照カウント0)場合にのみ辞書から削除する。
# 単純に「lock.locked()がFalseなら削除」する方式だと、取得元(_vod_generation_lock)が
# 辞書からロックオブジェクトを取り出した直後・実際にwith文で獲得する直前の隙間で
# 別スレッドが剪定してしまい、同一process_keyに対して2つの別々のLockオブジェクトが
# 生成されて同時に「取得成功」してしまう(このロック機構が本来防ぐべき二重起動と
# 全く同じ問題を再発させる)ため、参照カウントで安全性を担保している。
class _RefCountedLock:
    __slots__ = ("lock", "ref_count")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ref_count = 0


_vod_generation_locks: Dict[str, _RefCountedLock] = {}
_vod_generation_locks_guard = threading.Lock()


@contextlib.contextmanager
def _vod_generation_lock(process_key: str):
    """process_key単位で排他制御を行うコンテキストマネージャ。
    使用中(参照カウント>0)のエントリは剪定されず、使用を終えた
    (参照カウントが0に戻った)エントリのみ_vod_generation_locksから削除される。"""
    with _vod_generation_locks_guard:
        entry = _vod_generation_locks.get(process_key)
        if entry is None:
            entry = _RefCountedLock()
            _vod_generation_locks[process_key] = entry
        entry.ref_count += 1
    try:
        with entry.lock:
            yield
    finally:
        with _vod_generation_locks_guard:
            entry.ref_count -= 1
            if entry.ref_count == 0 and _vod_generation_locks.get(process_key) is entry:
                del _vod_generation_locks[process_key]


def _prune_finished_vod_processes() -> None:
    """完了済み(poll()がNoneでない)プロセスを_active_vod_processesから除去する。
    キーがcam_id×target_dateの組み合わせのため、剪定しないと日々増え続けて
    無限に蓄積してしまう。"""
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

def find_wsdl_path() -> Optional[str]:
    """camera_monitor.pyと同等のWSDL動的探索ロジック"""
    for path in sys.path:
        if not os.path.exists(path):
            continue
        candidate_standard = os.path.join(path, 'onvif', 'wsdl')
        candidate_direct = os.path.join(path, 'wsdl')
        for candidate in [candidate_standard, candidate_direct]:
            if os.path.exists(os.path.join(candidate, 'devicemgmt.wsdl')):
                return candidate
    return None

def get_rtsp_url(cam_conf: Dict[str, Any]) -> str:
    cam_id = cam_conf['id']
    if cam_id in _rtsp_cache:
        return _rtsp_cache[cam_id]

    if cam_conf.get("rtsp_url"):
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
        
        _rtsp_cache[cam_id] = auth_uri
        return auth_uri
    except Exception as e:
        logger.error(f"❌ [{cam_conf['name']}] ONVIF経由のRTSP URL取得に失敗: {e}")
        raise

def start_hls_stream(cam_conf: Dict[str, Any]) -> str:
    cam_id = cam_conf['id']
    cam_dir = init_output_dir(HLS_LIVE_DIR, cam_id)
    playlist_path = os.path.join(cam_dir, "stream.m3u8")

    if cam_id in _active_processes and _active_processes[cam_id].poll() is None:
        return playlist_path

    try:
        rtsp_url = get_rtsp_url(cam_conf)
    except Exception:
        return ""

    logger.info(f"🎥 [{cam_conf['name']}] ライブHLS配信を開始 (RTSP: {_mask_rtsp_url_for_log(rtsp_url)})")

    cmd = [
        "nice", "-n", "15",
        "ffmpeg", "-y",
        # -hide_banner/-loglevel error: 認証情報込みのRTSP URLが
        # ffmpeg自身の起動バナー("Input #0, rtsp, from 'rtsp://user:pass@...'")
        # 経由でffmpeg.logに平文出力されるのを防ぐ。
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-c:v", "copy",
        "-an",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments",
        playlist_path
    ]

    # FFmpegのエラーを追えるようにログファイルへ出力。
    # 他ローカルユーザーからの閲覧を防ぐため所有者のみ読み書き可能にする。
    log_path = os.path.join(cam_dir, "ffmpeg.log")
    log_file = open(log_path, "w")
    try:
        os.chmod(log_path, 0o600)
    except OSError:
        pass
    try:
        process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
    finally:
        # 子プロセスがdup()で自分のfdを持つため、親側はPopen呼び出し後に
        # 閉じてよい。閉じないと、プロセスがクラッシュして再起動されるたびに
        # ファイルハンドルがプロセス内に蓄積してリークする。
        log_file.close()
    _active_processes[cam_id] = process
    return playlist_path

def get_record_start_offset(cam_conf: Dict[str, Any], target_date: str) -> int:
        """指定日の最初の録画ファイルの開始時刻を0時からの秒数で返す"""
        nas_folder_name = cam_conf.get("nas_folder", cam_conf["name"])
        nvr_base_dir = getattr(config, 'NVR_RECORD_DIR', os.getenv("NVR_RECORD_DIR", "/mnt/nas/home_system/nvr_recordings"))
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


def _generate_record_playlist_locked(cam_conf: Dict[str, Any], target_date: str, process_key: str) -> Optional[str]:
    cam_id = cam_conf['id']
    nas_folder_name = cam_conf.get("nas_folder", cam_conf["name"])

    # NVRの保存ディレクトリ (config.NVR_RECORD_DIR が未定義の場合は環境変数やフォールバックを使用)
    nvr_base_dir = getattr(config, 'NVR_RECORD_DIR', os.getenv("NVR_RECORD_DIR", "/mnt/nas/home_system/nvr_recordings"))
    search_dir = os.path.join(nvr_base_dir, nas_folder_name)

    if not os.path.exists(search_dir):
        logger.warning(f"⚠️ [{cam_conf['name']}] 録画保存先が存在しません: {search_dir}")
        return None

    # 指定日の10分分割mp4ファイル一覧を取得
    search_pattern = os.path.join(search_dir, f"{target_date}_*.mp4")
    mp4_files = sorted(glob.glob(search_pattern))

    if not mp4_files:
        logger.warning(f"⚠️ [{cam_conf['name']}] {target_date} の録画ファイルが存在しません")
        return None

    cam_dir = init_output_dir(HLS_VOD_DIR, cam_id)
    concat_file_path = os.path.join(cam_dir, f"concat_{target_date}.txt")
    playlist_path = os.path.join(cam_dir, f"record_{target_date}.m3u8")

    # 完了済みプロセスを剪定してから登録状況を確認する(無限蓄積の防止)
    _prune_finished_vod_processes()

    # 1. 排他制御: 既に同じカメラ・日付の変換プロセスが実行中の場合は処理をスキップ
    if process_key in _active_vod_processes and _active_vod_processes[process_key].poll() is None:
        logger.info(f"⏳ [{cam_conf['name']}] {target_date} の録画プレイリスト生成は既に実行中です。")
        # フロントエンドが500エラー（FileResponseのクラッシュ）にならないよう、生成を待機する
        for _ in range(10):
            if os.path.exists(playlist_path):
                return playlist_path
            time.sleep(0.5)
        return None

    # 2. キャッシュ: 過去日付（録画が確定済み）かつ既にプレイリストが生成済みであれば、再エンコードせずそれを返す
    #    当日分は録画ファイルが増え続けるため、キャッシュ対象から除外し毎回最新の状態で再生成する
    today_str = datetime.now().strftime("%Y%m%d")
    if target_date < today_str and os.path.exists(playlist_path):
        logger.debug(f"✅ [{cam_conf['name']}] {target_date} のプレイリストは生成済みのためキャッシュを返します。")
        return playlist_path

    # ffmpegのconcatファイルリスト作成 (ffconcat version 1.0 を使用しタイムラインを補正)
    with open(concat_file_path, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        
        for i in range(len(mp4_files)):
            mp4 = mp4_files[i]
            f.write(f"file '{mp4}'\n")
            
            # 次のファイルとの時間差を計算し、動画の再生時間(duration)を明示する
            duration = 600.0  # 正常な10分ファイル(600秒)をデフォルトとする
            
            if i < len(mp4_files) - 1:
                try:
                    # 20260720_210200.mp4 のようなファイル名から時刻部分(210200)を抽出
                    curr_time_str = os.path.basename(mp4).split("_")[1].split(".")[0]
                    next_time_str = os.path.basename(mp4_files[i+1]).split("_")[1].split(".")[0]
                    
                    dt_curr = datetime.strptime(curr_time_str, "%H%M%S")
                    dt_next = datetime.strptime(next_time_str, "%H%M%S")
                    
                    diff_seconds = (dt_next - dt_curr).total_seconds()
                    
                    # 0秒以上かつ異常値(12時間以上等)でなければ、実時間差をdurationに設定
                    # これにより、ファイルが欠落している隙間は最終フレームで停止したまま時間を稼ぐ
                    if 0 < diff_seconds <= 43200:
                        duration = diff_seconds
                except Exception as e:
                    logger.warning(f"Failed to calculate duration for {mp4}: {e}")
            
            f.write(f"duration {duration}\n")

    logger.info(f"🎞️ [{cam_conf['name']}] {target_date} の録画プレイリスト生成中...")

    cmd = [
        "nice", "-n", "15",
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file_path,
        "-c:v", "copy",
        "-an",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_playlist_type", "vod",
        playlist_path
    ]

    

    # 3. subprocess.run (ブロック) から Popen (非同期) に変更し、プロセスを登録する
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _active_vod_processes[process_key] = process

    # 4. フロントエンドが404にならないよう、プレイリストファイルが生成されるまで少し待機する
    for _ in range(10):
        if os.path.exists(playlist_path):
            break
        time.sleep(0.5)
    
    return playlist_path if os.path.exists(playlist_path) else None


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