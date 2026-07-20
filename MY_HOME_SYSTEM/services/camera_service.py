import os
import sys
import subprocess
import time
import urllib.parse
import glob
from datetime import datetime
from typing import Optional, Dict, Any, List
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

    # ログ出力時のマスク処理もエンコード済みのパスワード文字列を対象とする
    safe_pass_for_log = urllib.parse.quote(cam_conf.get('pass', ''), safe='')
    logger.info(f"🎥 [{cam_conf['name']}] ライブHLS配信を開始 (RTSP: {rtsp_url.replace(safe_pass_for_log, '***')})")

    cmd = [
        "nice", "-n", "15",
        "ffmpeg", "-y",
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

    # FFmpegのエラーを追えるようにログファイルへ出力
    log_file = open(os.path.join(cam_dir, "ffmpeg.log"), "w")
    process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
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

    # キャッシュ：既にその日のプレイリストが存在し、ファイル更新日時が新しければそれを返す
    process_key = f"{cam_id}_{target_date}"

    # 1. 排他制御: 既に同じカメラ・日付の変換プロセスが実行中の場合は処理をスキップ
    if process_key in _active_vod_processes and _active_vod_processes[process_key].poll() is None:
        logger.info(f"⏳ [{cam_conf['name']}] {target_date} の録画プレイリスト生成は既に実行中です。")
        # フロントエンドが500エラー（FileResponseのクラッシュ）にならないよう、生成を待機する
        for _ in range(10):
            if os.path.exists(playlist_path):
                return playlist_path
            time.sleep(0.5)
        return None

    # キャッシュ：既にその日のプレイリストが存在し、過去の日付であればそれを返す

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

    

    # 2. subprocess.run (ブロック) から Popen (非同期) に変更し、プロセスを登録する
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _active_vod_processes[process_key] = process
    
    # 3. フロントエンドが404にならないよう、プレイリストファイルが生成されるまで少し待機する
    for _ in range(10):
        if os.path.exists(playlist_path):
            break
        time.sleep(0.5)
    
    return playlist_path if os.path.exists(playlist_path) else None