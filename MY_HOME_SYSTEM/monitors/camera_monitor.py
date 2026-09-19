# MY_HOME_SYSTEM/monitors/camera_monitor.py
import os
import sys
import asyncio
import json
import time
import socket
import subprocess
import tempfile
import threading
import traceback
import signal
import uuid
import datetime
import platform
from datetime import datetime as dt_class, timedelta, timezone
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from http.client import RemoteDisconnected
from urllib3.exceptions import ProtocolError
from requests.auth import HTTPDigestAuth

# ONVIF関連ライブラリ
try:
    from onvif import ONVIFCamera, ONVIFError
    from onvif.client import ONVIFService
    import zeep.exceptions
    from lxml import etree
except ImportError:
    ONVIFCamera = Any
    ONVIFService = Any
    ONVIFError = Exception
    etree = Any
    zeep = Any

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
# #661: WSDL 探索は core/onvif_utils.py に一本化(以前は本ファイルと camera_monitor/camera_service に同一実装が重複)
from core.onvif_utils import find_wsdl_path
from core.logger import setup_logging
from core.database import save_log_generic
# Issue #703: RTSP URL の解決(ONVIF GetStreamUri + 認証情報の埋め込み + キャッシュ)と
# ログ用のURLマスクは services/camera_service.py の既存実装を再利用する。camera_monitor は
# unified_server とは別プロセスで動くため、camera_service のモジュール状態(_rtsp_cache や
# ライブHLS配信の ffmpeg プロセス管理辞書)を共有することはなく、配信側には干渉しない。
from services.camera_service import get_rtsp_url, _mask_rtsp_url_for_log
from services.notification_service import send_push

# === ログ・定数設定 ===
logger = setup_logging("camera")

# Issue #497 (C-4): os.path.join自体が失敗した場合、ASSETS_DIRがtry節内でしか
# 束縛されず、except節でのログ出力(52行目)がNameErrorに化けてしまい、原因が
# 隠れる(到達可能性は低いが、pyrightのreportPossiblyUnboundVariableで検出済み)。
# try節の外側で先に組み立てておく。
_nas_assets_dir = os.path.join(config.ASSETS_DIR, "snapshots")
try:
    ASSETS_DIR: str = _nas_assets_dir
    os.makedirs(ASSETS_DIR, exist_ok=True)
except (PermissionError, OSError) as e:
    # NAS等が書き込み不可の場合、ローカルの一時ディレクトリにフォールバック
    # Issue #537: 以前は BASE_DIR/temp_assets/snapshots という独自パスで、nas_monitor の同期
    # (sync_fallback_data)も保持期間削除の対象にもならず SD カードに無制限に蓄積していた。
    # config.ensure_safe_path_with_backoff と同じ FALLBACK_ROOT/assets 配下に統一する。
    fallback_path = os.path.join(config.FALLBACK_ROOT, "assets", "snapshots")
    logger.warning(f"⚠️ Failed to create NAS directory '{_nas_assets_dir}': {e}")
    logger.warning(f"   -> 📂 Switching to local fallback: '{fallback_path}'")
    ASSETS_DIR = fallback_path
    os.makedirs(ASSETS_DIR, exist_ok=True)

BINDING_NAME: str = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'
PRIORITY_MAP: Dict[str, int] = {"intrusion": 100, "person": 80, "vehicle": 50, "motion": 10}
SESSION_LIFETIME: int = 3600
# 玄関カメラ専用: 10分の購読期限が切れる前に自発的に張り直す間隔(秒)
FORCE_RECONNECT_INTERVAL_SEC: int = 540
# PullMessages がこの回数連続で失敗したら、SESSION_LIFETIME を待たずに再接続する
PULL_FAILURE_RECONNECT_THRESHOLD: int = 3
RENEW_DURATION: str = "PT600S"

# クールダウンの秒数を設定 (config.py から読み込み。未定義時は60秒)
MOTION_COOLDOWN_SEC: int = getattr(config, 'MOTION_COOLDOWN_SEC', 60)

# --- Issue #703: 動体検知スナップショットのパラメータ ---
# RTSP への接続〜1フレーム取得にかける ffmpeg 1回あたりの上限秒数。
# RTSP の接続確立(TCP + DESCRIBE/SETUP/PLAY)とキーフレーム待ちで数秒かかるため、
# NVR ファイル切り出し時の 10 秒より長めに取る。
RTSP_SNAPSHOT_TIMEOUT_SEC: int = 15
# RTSP 取得のリトライ回数。動体検知の同期パス上で待たせることになるため、
# 失敗確定までの最悪時間が従来(NVR切り出し: 10秒×3 + バックオフ6秒 = 約36秒)を
# 超えないよう 2 回に抑える(15秒×2 + バックオフ2秒 = 約32秒)。
RTSP_SNAPSHOT_MAX_RETRIES: int = 2
# NVR 録画セグメントを「まだ書き込み中」とみなす mtime の猶予(秒)。
# NVR が書き込み中のファイルは mtime が更新され続けるため常に最新として選ばれるが、
# moov atom が未完成で `-sseof` のシークができず ffmpeg が失敗する(Issue #703)。
NVR_INPROGRESS_MTIME_MARGIN_SEC: int = 30

# 各カメラの最終検知時刻を保持する辞書
last_motion_detected: Dict[str, float] = {}
# #439: last_motion_detected はカメラごとの監視スレッドから並行して読み書きされる。
# 個々のdict操作自体はGILにより原子的だが、クールダウン判定の「読んでから書く」までを
# 不可分にするためにこのLockで保護する。
_motion_lock = threading.Lock()

# --- #652: devices.json の enabled フラグの再読込 ---
# set_camera_enabled(services/camera_service.py)は devices.json を書き換えて
# サーバープロセス内の config.CAMERAS を更新するが、camera_monitor は別プロセスで
# 起動時の config.CAMERAS を保持し続けるため、以前は UI でカメラを無効化しても
# 再起動まで ONVIF 購読・スナップショット・通知が続いていた。各監視ループの先頭で
# devices.json の mtime を確認し、変わっていれば enabled を読み直す(SIGHUP 方式より
# 単純で、子プロセスの再起動・再接続も要らない)。
ENABLED_RECHECK_INTERVAL_SEC: float = 5.0   # mtime を stat する最短間隔(秒)
DISABLED_POLL_INTERVAL_SEC: int = 30        # 無効化中に再有効化を待つ間隔(秒)

_enabled_cache: Dict[str, Any] = {"mtime_ns": None, "checked_at": 0.0, "flags": None}
_enabled_cache_lock = threading.Lock()


def _read_enabled_flags_from_devices_json() -> Optional[Dict[str, bool]]:
    """devices.json を読み {camera_id: enabled} を返す。読めない・壊れている場合は None。"""
    try:
        with open(config.DEVICES_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            str(c.get("id")): bool(c.get("enabled", True))
            for c in data.get("cameras", [])
            if isinstance(c, dict) and c.get("id") is not None
        }
    except Exception as e:
        logger.debug(f"devices.json の enabled 再読込に失敗(前回値/起動時値を使う): {e}")
        return None


def is_camera_enabled(cam_conf: Dict[str, Any]) -> bool:
    """該当カメラが現在有効かを devices.json の最新値で返す(#652)。

    devices.json の mtime が前回確認時から変わっていなければキャッシュを返し、
    stat 自体も ENABLED_RECHECK_INTERVAL_SEC に1回に抑える。devices.json が無い・
    壊れている・該当 id が無い場合は起動時の cam_conf["enabled"](既定 True)に倒す。
    """
    fallback = bool(cam_conf.get("enabled", True))
    cam_id = cam_conf.get("id")
    if cam_id is None:
        return fallback
    now = time.monotonic()
    with _enabled_cache_lock:
        if now - _enabled_cache["checked_at"] >= ENABLED_RECHECK_INTERVAL_SEC or _enabled_cache["flags"] is None:
            _enabled_cache["checked_at"] = now
            try:
                mtime_ns = os.stat(config.DEVICES_JSON_PATH).st_mtime_ns
            except OSError:
                mtime_ns = None
            if mtime_ns is None:
                _enabled_cache["mtime_ns"] = None
                _enabled_cache["flags"] = None
            elif mtime_ns != _enabled_cache["mtime_ns"] or _enabled_cache["flags"] is None:
                flags = _read_enabled_flags_from_devices_json()
                if flags is not None:
                    _enabled_cache["mtime_ns"] = mtime_ns
                    _enabled_cache["flags"] = flags
        flags = _enabled_cache["flags"]
    if not flags:
        return fallback
    return flags.get(str(cam_id), fallback)


active_pullpoints: List[Any] = []
# #439: active_pullpoints はカメラごとの監視スレッドから並行してappend/removeされる。
# 「in で存在確認してからremove」の間に他スレッドが同じ要素を削除すると
# list.remove()がValueErrorを送出しうる(finally節内で発生すると後始末処理が
# 中断する)ため、追加・削除・読み取りをこのLockで保護する。
_pullpoints_lock = threading.Lock()


def _add_pullpoint(pullpoint: Any) -> None:
    with _pullpoints_lock:
        active_pullpoints.append(pullpoint)


def _discard_pullpoint(pullpoint: Any) -> None:
    """active_pullpointsから安全に削除する(既に削除済みでも例外を出さない)。"""
    with _pullpoints_lock:
        try:
            active_pullpoints.remove(pullpoint)
        except ValueError:
            pass


def cleanup_handler(signum: int, frame: Any) -> None:
    """プロセス終了時のクリーンアップ。"""
    logger.info(f"🛑 Shutdown signal ({signum}) received. Cleaning up subscriptions...")
    # 他スレッド(monitor_single_camera)が同時に active_pullpoints を変更しうるため、
    # イテレーション中の RuntimeError(list changed size)を避けてコピーを走査する
    with _pullpoints_lock:
        pullpoints_snapshot = list(active_pullpoints)
    for svc in pullpoints_snapshot:
        try:
            if hasattr(svc, 'Unsubscribe'):
                svc.Unsubscribe()
            elif hasattr(svc, 'service') and hasattr(svc.service, 'Unsubscribe'):
                svc.service.Unsubscribe(_soapheaders=None)
        except Exception:
            pass
    logger.info("👋 Cleanup completed. Exiting.")
    os._exit(0)

signal.signal(signal.SIGINT, cleanup_handler)
signal.signal(signal.SIGTERM, cleanup_handler)

def is_host_reachable(ip: str) -> bool:
    """
    Pingコマンドを使用してホストへのL3到達性（Route）を確認する。
    """
    param: str = '-n' if platform.system().lower() == 'windows' else '-c'
    cmd: List[str] = ['ping', param, '1', ip]
    try:
        res: subprocess.CompletedProcess = subprocess.run(
            cmd, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL, 
            timeout=3
        )
        return res.returncode == 0
    except Exception as e:
        logger.debug(f"Ping execution failed for {ip}: {e}")
        return False


WSDL_DIR: Optional[str] = find_wsdl_path()

def perform_emergency_diagnosis(ip: str) -> Dict[int, bool]:
    """接続障害時にポートの状態を診断する。"""
    results: Dict[int, bool] = {}
    msg = f"🚑 [Diagnosis] Checking {ip}:\n"
    for port in [80, 2020]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            res = sock.connect_ex((ip, port))
            results[port] = (res == 0)
            status = "OPEN" if res == 0 else f"CLOSED({res})"
            msg += f"   - Port {port}: {status}\n"
            sock.close()
        except Exception as e:
            msg += f"   - Port {port}: Error({e})\n"
    logger.warning(msg)
    return results

def check_camera_time(devicemgmt: Any, cam_name: str) -> bool:
    """カメラの時刻を確認し、ズレが大きい場合は警告する"""
    try:
        sys_dt = devicemgmt.GetSystemDateAndTime()
        if not sys_dt or not hasattr(sys_dt, 'UTCDateTime'):
            return True

        utc = sys_dt.UTCDateTime
        # #382: 以前はカメラのUTC時刻に+9hした naive 値をホストローカルの dt_class.now() と
        # 比較していた(JST前提)。ホストのTZがUTC等の環境では差が常に9hになり、全カメラが
        # 「時刻ズレ」で永久に接続不能になっていた。両者を aware な UTC で比較する。
        cam_time_utc = dt_class(utc.Date.Year, utc.Date.Month, utc.Date.Day,
                               utc.Time.Hour, utc.Time.Minute, utc.Time.Second,
                               tzinfo=timezone.utc)
        now_utc = dt_class.now(timezone.utc)

        diff = abs((now_utc - cam_time_utc).total_seconds())

        if diff > 300: # 5分以上のズレ
            logger.warning(f"⏰ [{cam_name}] Time Drift Detected! Camera(UTC): {cam_time_utc}, Server(UTC): {now_utc}, Diff: {diff:.0f}s")
            logger.warning(f"   -> ONVIF authentication requires synchronized clocks. Please check camera settings.")
            return False
        return True
    except Exception as e:
        err_str: str = str(e)
        if "ISO8601" in err_str or "Unrecognised" in err_str or "zeep" in str(type(e)):
            logger.error(f"❌ [{cam_name}] XML/Date Parse Error in ONVIF response. Camera returned invalid date: {e}")
        else:
            logger.error(f"⚠️ [{cam_name}] Failed to check camera time unexpectedly: {e}")
        
        # 監視そのものを止めないためのFail-Soft対応
        return True

def _nvr_search_patterns(nas_folder: str, now: dt_class) -> list:
    """動体検知時に最新の NVR 録画チャンクを探す glob パターン(当日分)を返す。

    Issue #537: 0時台は「最新のチャンク」が前日 23:5x 開始のファイル(前日日付のプレフィックス)
    であることが普通で、当日プレフィックスだけだと "No NVR video files found" になり
    スナップショットが保存されなかった。0時台に限り前日分のパターンも加える。
    """
    patterns = [os.path.join(nas_folder, f"{now.strftime('%Y%m%d')}_*.mp4")]
    if now.hour == 0:
        yesterday = now - timedelta(days=1)
        patterns.append(os.path.join(nas_folder, f"{yesterday.strftime('%Y%m%d')}_*.mp4"))
    return patterns


def _select_latest_completed_segment(mp4_files: list, now_ts: "float | None" = None) -> "str | None":
    """mtime 降順に並んだ NVR 録画セグメントから、書き込みが完了しているとみなせる最新のものを返す。

    Issue #703: NVR が現在書き込んでいるセグメントは mtime が更新され続けるため必ず先頭に
    来るが、moov atom が未完成のため `-sseof`(末尾からのシーク)ができず ffmpeg が
    終了コード 69/183 で失敗する。実機では動体検知 316 件中 94 件(約30%)がこれで失敗し、
    その WARNING が health_watch を常時「異常」状態にしていた。
    直近 NVR_INPROGRESS_MTIME_MARGIN_SEC 秒以内に更新されたファイルは書き込み中とみなして除外する。

    完成済みのセグメントが1つも無ければ None(呼び出し元は Fail-Soft で諦める)。
    """
    if now_ts is None:
        now_ts = time.time()
    for path in mp4_files:
        try:
            if now_ts - os.path.getmtime(path) >= NVR_INPROGRESS_MTIME_MARGIN_SEC:
                return path
        except OSError:
            # ローテーション等で glob 直後に消えたファイルはスキップする
            continue
    return None


def capture_snapshot_from_rtsp(cam_conf: dict) -> "bytes | None":
    """カメラの RTSP ストリームへ直接接続し、ffmpeg で1フレームだけ抽出する。

    Issue #703: 従来の `capture_snapshot_from_nvr()` は NAS 上の NVR 録画セグメントのうち
    mtime が最新のものを選んでいたが、それは常に「NVR が書き込み中の未完成セグメント」で
    あり、シークに失敗して約3割のスナップショットが落ちていた。動体検知の瞬間の映像を
    残すという本来の目的にも合うため、検知時点で RTSP から直接1フレームを取得する。

    RTSP URL の解決は services/camera_service.py の `get_rtsp_url()` を再利用する
    (devices.json 由来の `rtsp_url` があればそれを、無ければ ONVIF GetStreamUri から
    組み立て、プロセス内にキャッシュする)。

    Fail-Soft 契約: 失敗しても例外は送出せず、ログを残して None を返す。
    """
    cam_name = cam_conf.get("name") or cam_conf.get("id") or "unknown"

    try:
        rtsp_url = get_rtsp_url(cam_conf)
    except Exception as e:  # noqa: BLE001 - ONVIF/zeep は多様な例外を投げるため Fail-Soft で握る
        # get_rtsp_url は ONVIF 失敗時に例外を送出する。監視ループは止めない。
        logger.warning(f"⚠️ [{cam_name}] RTSP URLの取得に失敗しました: {e}")
        return None

    if not rtsp_url:
        logger.warning(f"⚠️ [{cam_name}] RTSP URLが空のためスナップショットを取得できません。")
        return None

    masked_url = _mask_rtsp_url_for_log(rtsp_url)
    # C-L7 と同様、実行環境の TMPDIR に追従させるため tempfile.gettempdir() 経由で解決する
    output_tmp = os.path.join(tempfile.gettempdir(), f"snapshot_{cam_name}_{uuid.uuid4().hex}.jpg")

    try:
        for attempt in range(1, RTSP_SNAPSHOT_MAX_RETRIES + 1):
            try:
                cmd = [
                    "ffmpeg", "-y",
                    # -hide_banner/-loglevel error: 認証情報込みの RTSP URL が ffmpeg 自身の
                    # 起動バナー("Input #0, rtsp, from 'rtsp://user:pass@...'")経由で
                    # 出力されるのを防ぐ(camera_service.start_hls_stream と同じ理由)。
                    "-hide_banner", "-loglevel", "error",
                    # UDP はラズパイ〜カメラ間のパケットロスで壊れたフレームになりやすいため TCP 固定
                    "-rtsp_transport", "tcp",
                    "-i", rtsp_url,
                    "-frames:v", "1",
                    "-q:v", "2",    # 高画質
                    "-an",          # 音声ストリームを持つカメラで image2 muxer が失敗するのを防ぐ
                    "-f", "image2",
                    output_tmp,
                ]

                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=RTSP_SNAPSHOT_TIMEOUT_SEC,
                    check=True,
                )

                # ffmpeg は 0 終了でも 0 バイトのファイルを残すことがあるためサイズも確認する
                if os.path.exists(output_tmp) and os.path.getsize(output_tmp) > 0:
                    with open(output_tmp, "rb") as f:
                        return f.read()
                logger.warning(
                    f"⚠️ [{cam_name}] RTSPから1フレーム抽出できませんでした "
                    f"(Attempt {attempt}/{RTSP_SNAPSHOT_MAX_RETRIES})"
                )

            except subprocess.TimeoutExpired:
                # 例外の str() には cmd(=認証情報入り RTSP URL)が含まれるため、そのまま出さない
                logger.warning(
                    f"⏳ [{cam_name}] RTSP取得がタイムアウトしました "
                    f"({RTSP_SNAPSHOT_TIMEOUT_SEC}s, {masked_url}) "
                    f"(Attempt {attempt}/{RTSP_SNAPSHOT_MAX_RETRIES})"
                )
            except subprocess.CalledProcessError as e:
                logger.warning(
                    f"⚠️ [{cam_name}] RTSPからのフレーム抽出に失敗しました "
                    f"(exit={e.returncode}, {masked_url}) "
                    f"(Attempt {attempt}/{RTSP_SNAPSHOT_MAX_RETRIES})"
                )
            except Exception as e:  # noqa: BLE001 - Fail-Soft 契約(既存の NVR 経路と同じ作法)
                logger.error(f"❌ [{cam_name}] RTSP取得で予期せぬエラー: {e}")
                break

            if attempt < RTSP_SNAPSHOT_MAX_RETRIES:
                time.sleep(2 ** attempt)  # Exponential Backoff

        return None
    finally:
        # タイムアウト・異常終了で ffmpeg が部分書き込みしたファイルを残さない
        try:
            if os.path.exists(output_tmp):
                os.remove(output_tmp)
        except OSError:
            pass


def capture_snapshot_from_nvr(cam_conf: dict, target_time: dt_class = None) -> Optional[bytes]:
    """
    NAS(NVR)に常時録画されている最新の動画ファイル(.mp4)から、
    FFmpegを使用して該当時刻のフレームを切り出す（カメラ本体のRTSP負荷ゼロ）

    Issue #703 以降、これは `capture_snapshot_from_rtsp()` が失敗したときのフォールバック経路。
    最新 mtime のセグメントは NVR が書き込み中で `-sseof` のシークに失敗するため、
    `_select_latest_completed_segment()` で書き込み完了済みのセグメントだけを対象にする
    (そのぶん動体検知時刻から最大でセグメント長ぶん古いフレームになる)。
    """
    import subprocess
    import glob
    import time
    
    if target_time is None:
        target_time = dt_class.now()

    # nas_folder は NVR録画ベースディレクトリ配下の「フォルダ名」であり、絶対パスではない
    # (camera_service.py の get_rtsp_url等と同じ解決ロジックに合わせる)
    # #405: config.NVR_RECORD_DIR は常に定義されるため、環境変数への直接フォールバックは持たない
    nvr_base_dir = config.NVR_RECORD_DIR
    nas_folder_name = cam_conf.get("nas_folder") or cam_conf["name"]
    nas_folder = os.path.join(nvr_base_dir, nas_folder_name)
    if not os.path.exists(nas_folder):
        # 設計書準拠: 介入が必要なエラー(NASマウント外れ等)は ERROR
        logger.error(f"❌ [{cam_conf['name']}] NAS folder not found or unmounted: {nas_folder}")
        return None

    # 最新のmp4ファイルを取得
    # #411 S-L10: 以前は "**/*.mp4" で全期間(NVRの保存期間分、数十日)を毎回CIFS越しに
    # 再帰globしていたため動体検知のたびに高コストなI/Oが発生していた。録画ファイル名は
    # camera_service.py と同じ "{YYYYMMDD}_*.mp4" 形式なので、当日分だけに絞って検索する。
    mp4_files = sorted(
        (f for pattern in _nvr_search_patterns(nas_folder, dt_class.now()) for f in glob.glob(pattern)),
        key=os.path.getmtime, reverse=True,
    )
    
    if not mp4_files:
        logger.warning(f"⚠️ [{cam_conf['name']}] No NVR video files found in {nas_folder}.")
        return None

    # Issue #703: mp4_files[0](=最新 mtime)は NVR が書き込み中のセグメントであることが
    # ほとんどで、未完成の moov atom に対する -sseof のシークが失敗していた。
    latest_mp4 = _select_latest_completed_segment(mp4_files)
    if latest_mp4 is None:
        logger.warning(
            f"⚠️ [{cam_conf['name']}] 書き込み完了済みのNVRセグメントが見つかりません "
            f"(直近{NVR_INPROGRESS_MTIME_MARGIN_SEC}秒以内に更新されたファイルのみ): {nas_folder}"
        )
        return None
    # C-L7: 実行環境のTMPDIR等に追従させるため /tmp 直書きではなく tempfile.gettempdir() 経由で解決する
    output_tmp = os.path.join(tempfile.gettempdir(), f"snapshot_{cam_conf['name']}_{uuid.uuid4().hex}.jpg")
    
    # 設計書「エラーハンドリングと自動復旧」準拠: NVRのバッファフラッシュ遅延を考慮したリトライ
    max_retries = 3
    try:
        for attempt in range(1, max_retries + 1):
            try:
                # 最新の動画の「最後から1秒前」のフレームを抽出（動体検知直後の映像）
                # 実際には target_time と最新mp4のタイムスタンプを比較して -ss のシーク時間を計算するのが理想的です
                cmd = [
                    "ffmpeg", "-y",
                    "-sseof", "-1", # ファイル末尾から1秒前
                    "-i", latest_mp4,
                    "-vframes", "1",
                    "-q:v", "2",    # 高画質
                    output_tmp
                ]

                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)

                if os.path.exists(output_tmp):
                    with open(output_tmp, "rb") as f:
                        image_data = f.read()
                    return image_data

            except subprocess.TimeoutExpired:
                logger.warning(f"⏳ [{cam_conf['name']}] FFmpeg timeout on NVR file (Attempt {attempt}/{max_retries})")
            except subprocess.CalledProcessError as e:
                logger.warning(f"⚠️ [{cam_conf['name']}] FFmpeg extraction failed: {e} (Attempt {attempt}/{max_retries})")
            except Exception as e:
                logger.error(f"❌ [{cam_conf['name']}] Unexpected error in NVR extraction: {e}")
                break

            # #411 S-L10: 最終試行後もsleepしていたため、失敗確定後に無駄な最大8秒待ちが
            # 発生していた(呼出元は動体検知の同期パスで待たされる)。次のリトライがある
            # ときだけ待つ。
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # Exponential Backoff

        return None
    finally:
        # Low: タイムアウトや異常終了でffmpegが output_tmp に部分書き込みしたファイルを
        # 残したまま関数を抜けると、/tmp に snapshot_*.jpg の残骸が蓄積し続けていた。
        # 成功時に読み取った後の削除も含め、どの終了経路でも確実にクリーンアップする。
        try:
            if os.path.exists(output_tmp):
                os.remove(output_tmp)
        except OSError:
            pass


def save_image_from_stream(cam_name: str, event_type: str = "motion") -> Optional[str]:
    cam_conf = next((c for c in config.CAMERAS if c["name"] == cam_name), None)
    if not cam_conf:
        return None

    logger.debug(f"📸 [{cam_name}] 映像フレームの取得を開始します (方式: RTSP直接取得)")

    # Issue #703: 動体検知の瞬間の映像をカメラの RTSP から直接取る(第一手)。
    # NVR 録画ファイルの書き込み状態に依存しないため、従来の約3割の失敗が無くなる。
    image_data = capture_snapshot_from_rtsp(cam_conf)

    if not image_data:
        # カメラへ一時的に到達できない場合の保険として、従来の NVR 切り出しを試す
        # (NVR の録画プロセスは別経路のため、片方だけ失敗していることがある)。
        logger.info(f"ℹ️ [{cam_name}] RTSP直接取得に失敗したため、NVR録画からの切り出しにフォールバックします。")
        image_data = capture_snapshot_from_nvr(cam_conf)

    if not image_data:
        # 取得に失敗した場合でも、システム自体を落とさず（Fail-Soft）Noneを返してスキップする
        logger.warning(f"⚠️ [{cam_name}] スナップショットの取得に失敗しましたが、監視プロセスは継続します。")
        return None

    # 以降は既存の保存ロジック（ASSETS_DIRへの保存等）をそのまま使用
    timestamp = dt_class.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{cam_name}_{event_type}_{timestamp}.jpg"
    filepath = os.path.join(ASSETS_DIR, filename)

    try:
        with open(filepath, "wb") as f:
            f.write(image_data)
        return filepath
    except Exception as e:
        logger.error(f"❌ [{cam_name}] Failed to save image to {filepath}: {e}")
        return None

def force_close_session(service_obj: Any) -> None:
    """
    ONVIFService, ONVIFCamera, または zeep Client が保持する
    HTTPセッション(requests.Session)を強制的にcloseし、ファイル記述子を解放する。
    """
    if not service_obj:
        return

    try:
        # パターン1: zeep_client 属性を持つ場合 (ONVIFService, devicemgmt等)
        if hasattr(service_obj, 'zeep_client') and hasattr(service_obj.zeep_client, 'transport'):
            if hasattr(service_obj.zeep_client.transport, 'session'):
                service_obj.zeep_client.transport.session.close()
        
        # パターン2: 直接 transport を持つ場合 (ONVIFCamera等)
        elif hasattr(service_obj, 'transport') and hasattr(service_obj.transport, 'session'):
            service_obj.transport.session.close()

        # パターン3: devicemgmt を経由する場合 (ONVIFCameraの別パターン)
        elif hasattr(service_obj, 'devicemgmt'):
            force_close_session(service_obj.devicemgmt)

    except Exception as e:
        logger.debug(f"Session close warning: {e}")

def process_camera_event(msg: Any, cam_conf: Dict[str, Any]) -> None:
    """
    単一のONVIFイベントメッセージをパースし、動体検知イベントを処理します。
    処理結果に関わらず確実にリソースを解放し、連続発火を防ぐためのクールダウン（Debounce）処理を行います。

    Args:
        msg (Any): ONVIFイベントメッセージオブジェクト
        cam_conf (Dict[str, Any]): カメラ設定辞書
    """
    global last_motion_detected
    cam_name: str = cam_conf['name']
    cam_id: str = cam_conf['id']
    topic_str: str = "Unknown"
    debug_val: str = "N/A"
    is_motion: bool = False
    
    try:
        # 1. Topicの抽出
        if hasattr(msg, 'Topic'):
            if hasattr(msg.Topic, '_value_1') and msg.Topic._value_1 is not None:
                topic_str = str(msg.Topic._value_1)
            else:
                topic_str = str(msg.Topic)

        # 2. Message(XML)のパース
        if hasattr(msg, 'Message') and hasattr(msg.Message, '_value_1'):
            element: Any = msg.Message._value_1
            if type(element).__name__ == '_Element':
                xml_str: str = etree.tostring(element, encoding='unicode')
                debug_val = xml_str
                xml_lower: str = xml_str.lower()
                if ('motion' in xml_lower or 'ruleengine' in xml_lower) and ('value="true"' in xml_lower or 'value="1"' in xml_lower):
                    is_motion = True
            else:
                debug_val = str(element)
        
        logger.debug(f"🕵️ [TOPIC AUDIT] {cam_name} | Topic: {topic_str} | Data: {debug_val}")

        # 3. 早期リターン（対象外イベント）
        if not is_motion:
            # 動体検知ではない場合、ここで処理を終了（finallyへ飛ぶ）
            return

        # 4. クールダウン（Debounce）処理の追加
        current_time: float = time.time()
        with _motion_lock:
            last_detected_time: float = last_motion_detected.get(cam_id, 0.0)
            if current_time - last_detected_time < MOTION_COOLDOWN_SEC:
                logger.debug(f"🏃 [{cam_name}] Motion Detected (Skipped due to cooldown)")
                return
            # 状態更新（有効な検知として処理を進めるため、タイムスタンプを更新）
            last_motion_detected[cam_id] = current_time

        # 5. 動体検知時のアクション（DB保存・画像取得）
        logger.info(f"🏃 [{cam_name}] Motion Detected!")
        JST = datetime.timezone(datetime.timedelta(hours=9))
        now_str = dt_class.now(JST).isoformat()             

        columns = ["timestamp", "device_name", "device_id", "device_type", "movement_state"]
        values = (now_str, cam_name, cam_conf['id'], "ONVIF_CAMERA", "ON")

        save_log_generic("device_records", columns, values)
        save_image_from_stream(cam_name, "motion")
        
    except Exception as e:
        logger.warning(f"⚠️ [{cam_name}] Event Parse Error: {e} | Trace: {traceback.format_exc().splitlines()[-1]}")
    finally:
        # ✅ いかなる場合（早期リターン・例外発生）でも確実にリソースを解放する
        del msg
        logger.debug(f"🧹 [{cam_name}] Event processing completed / Local resources released.")


class _ReconnectBackoff:
    """
    再接続のバックオフ状態(#662)。

    以前は `monitor_single_camera` のループ外で初期化した3つのローカル変数を
    `try`/`except` の両方から書き換えていた。カウントが1つずれるだけで再接続間隔が
    指数的に変わる(10 * 2**n)ため、更新規則ごとここへ閉じ込めている。
    """

    MAX_BACKOFF_SEC: int = 3600          # 最大1時間の待機 (サスペンド)
    TRANSIENT_WINDOW_SEC: int = 15       # これ以内に再発したら「連続」とみなす
    TRANSIENT_WARN_THRESHOLD: int = 3    # 連続何回でWARNINGへ格上げするか

    def __init__(self) -> None:
        self.consecutive_errors: int = 0
        self.transient_error_count: int = 0
        self.last_transient_error_time: float = 0

    def record_failure(self) -> int:
        """失敗を1件数え、次に待つ秒数を返す。"""
        self.consecutive_errors += 1
        return min(10 * (2 ** self.consecutive_errors), self.MAX_BACKOFF_SEC)

    def note_transient(self, now: float) -> bool:
        """一時的障害を記録し、WARNINGへ格上げすべきかを返す。"""
        if now - self.last_transient_error_time < self.TRANSIENT_WINDOW_SEC:
            self.transient_error_count += 1
        else:
            self.transient_error_count = 1
        self.last_transient_error_time = now
        return self.transient_error_count >= self.TRANSIENT_WARN_THRESHOLD

    def reset(self) -> None:
        """接続成功時に呼ぶ。次の失敗は再び最小の待機から始まる。"""
        self.consecutive_errors = 0


class _CameraSession:
    """
    1回の接続セッションが確保したリソース(#662)。

    `pullpoint` には `CreatePullPointSubscription()` の**生の戻り値**がいったん入り、
    その後 `ONVIFService` インスタンスで上書きされる。これは「`ONVIFService` の構築に
    失敗しても生のサブスクリプションを Unsubscribe できる」ようにするための意図的な
    2段階代入で、`release()` はどちらの状態でも動く必要がある。
    """

    def __init__(self) -> None:
        self.mycam: Any = None
        self.events_service: Any = None
        self.pullpoint: Any = None

    def release(self, cam_name: str) -> None:
        """確保したリソースを解放する(どの段階で失敗していても呼べる)。"""
        logger.debug(f"🧹 [{cam_name}] Starting resource cleanup...")
        if self.pullpoint:
            _discard_pullpoint(self.pullpoint)
            try:
                self.pullpoint.Unsubscribe()
                logger.debug(f"🗑️ [{cam_name}] Unsubscribed from PullPoint successfully.")
            except Exception as e:
                logger.debug(f"⚠️ [{cam_name}] PullPoint Unsubscribe skipped or failed: {e}")

            force_close_session(self.pullpoint)

        if self.events_service:
            force_close_session(self.events_service)
            logger.debug(f"🔌 [{cam_name}] Events service session closed.")

        if self.mycam:
            force_close_session(self.mycam)
            logger.debug(f"🔌 [{cam_name}] Camera devicemgmt session closed.")

        logger.debug(f"✨ [{cam_name}] Resource cleanup completed.")


def _connect_and_subscribe(
    cam_conf: Dict[str, Any], session: _CameraSession, is_first_connect: bool
) -> bool:
    """
    ONVIF接続からイベント購読までを行い、確保したリソースを `session` に詰める。

    戻り値は更新後の `is_first_connect`(初回接続だけINFOでモデル名を出すため)。
    途中で失敗した場合は例外をそのまま送出し、解放は呼び出し元の `finally` に任せる。
    """
    cam_name: str = cam_conf['name']

    wsdl_path: Optional[str] = find_wsdl_path()
    if not wsdl_path: raise FileNotFoundError("WSDL path could not be determined.")

    # 設定ファイルで指定されたポートのみを使用し、勝手な切り替えを禁止する
    target_port: int = cam_conf.get('port', 80)

    # 2. カメラ接続 (ONVIFCamera)
    session.mycam = ONVIFCamera(
        cam_conf['ip'],
        target_port,
        cam_conf['user'],
        cam_conf['pass'],
        wsdl_dir=wsdl_path,
        encrypt=True
    )

    devicemgmt: Any = session.mycam.create_devicemgmt_service()
    devicemgmt.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])

    if not check_camera_time(devicemgmt, cam_name):
        raise ConnectionRefusedError(f"[{cam_name}] Time verification failed. Check camera clock.")

    device_info: Any = devicemgmt.GetDeviceInformation()
    if is_first_connect:
        logger.info(f"📡 [{cam_name}] Connected. Model: {device_info.Model}")
        is_first_connect = False
    else:
        logger.debug(f"📡 [{cam_name}] Connected. Model: {device_info.Model} (Reconnected)")

    # 3. イベント購読
    session.events_service = session.mycam.create_events_service()
    session.events_service.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])

    logger.debug(f"[{cam_name}] Creating subscription with TopicFilter...")
    # いったん生の戻り値を持たせる。次の ONVIFService 構築で失敗しても release() が
    # これを Unsubscribe できるようにするため(カメラ側に購読を残さない)。
    session.pullpoint = session.events_service.CreatePullPointSubscription()

    try:
        plp_address: str = session.pullpoint.SubscriptionReference.Address._value_1
    except AttributeError:
        plp_address: str = session.pullpoint.SubscriptionReference.Address

    events_wsdl: str = os.path.join(wsdl_path, 'events.wsdl')
    pullpoint: Any = ONVIFService(
        xaddr=plp_address,
        user=cam_conf['user'],
        passwd=cam_conf['pass'],
        url=events_wsdl,
        encrypt=True,
        binding_name=BINDING_NAME
    )

    pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])

    _add_pullpoint(pullpoint)
    session.pullpoint = pullpoint
    return is_first_connect


def _pull_events_until_reconnect(cam_conf: Dict[str, Any], pullpoint: Any) -> None:
    """
    購読済みのセッションでイベントを受け続ける。再接続すべき状況になったら return する。

    return する条件は4つ: セッション寿命(SESSION_LIFETIME)の到達、devices.json での
    無効化、玄関カメラの自発的再接続タイマー、PullMessages の失敗。
    """
    cam_name: str = cam_conf['name']
    session_start_time: float = time.time()

    # --- 玄関カメラ専用の再接続（Subscribeし直し）タイマー ---
    # 10分の有効期限が切れる前に、自発的にセッションを切り替える
    last_subscribe_time: float = time.time()

    # PullMessages の連続失敗回数(玄関以外のカメラ用)。カメラの再起動等で
    # サブスクリプションが消えた場合、以前は SESSION_LIFETIME(3600秒)が経過する
    # まで毎 0.5 秒 debug ログを出しながら events=None で回り続け、最長1時間
    # 動体検知が止まっていた。閾値に達したらループを抜けて再接続する。
    consecutive_pull_failures: int = 0

    while True:
        current_time = time.time()

        # SESSION_LIFETIME (3600秒) 経過時のみ、安全にループを抜けてセッションを作り直す
        if current_time - session_start_time > SESSION_LIFETIME:
            logger.debug(f"🔄 [{cam_name}] Session lifetime reached. Refreshing gracefully...")
            return

        # #652: UI から無効化されたら購読を解除して外側ループの待機へ移る
        if not is_camera_enabled(cam_conf):
            logger.info(f"⏸️ [{cam_name}] enabled=false に変更されたため購読を解除します。")
            return

        # --- 玄関カメラ専用の自発的再接続ロジック ---
        if cam_name == "玄関カメラ":
            if current_time - last_subscribe_time > FORCE_RECONNECT_INTERVAL_SEC:
                logger.info(f"🔄 [{cam_name}] 9 minutes passed. Reconnecting to avoid silent timeout...")
                return  # 安全に再接続（外側のループへ）
        # -----------------------------------------------------------

        try:
            events: Any = pullpoint.PullMessages({'Timeout': timedelta(seconds=2), 'MessageLimit': 100})
            consecutive_pull_failures = 0
            if events:
                # Low: 元々はデバッグ目的で玄関カメラのみ info に変更されていたが、
                # 全イベント属性(dir(events))・全ペイロードを本番ログに残す設計上の
                # 意図はなく、ノイズ・情報量ともに大きいため debug に降格する。
                if cam_name == "玄関カメラ":
                    logger.debug(f"🔬 [RAW EVENTS] {cam_name}: Type={type(events)}, Attrs={dir(events)}")
                    if hasattr(events, 'NotificationMessage'):
                        logger.debug(f"📦 [EVENT PAYLOAD] {cam_name}: 含まれるメッセージ数: {len(events.NotificationMessage)}")
                        logger.debug(f"📝 [PAYLOAD DETAIL] {events.NotificationMessage}")
        except Exception as e:
            if cam_name == "玄関カメラ":
                # Renew非対応カメラのため、通信断エラーが出た場合はWARNINGとし、再接続へ移行
                logger.warning(f"⚠️ [{cam_name}] Failed to pull messages: {e}. Breaking loop to reconnect.")
                return  # 例外を握りつぶさず、外側の Exponential Backoff 再接続へ移行

            # 駐車場カメラ・庭カメラ: 単発の失敗は従来どおり debug に留めるが、
            # 連続して失敗する場合は接続が死んでいる(サブスクリプション消失等)
            # とみなして玄関カメラと同様に再接続へ移行する。
            consecutive_pull_failures += 1
            events = None
            if consecutive_pull_failures >= PULL_FAILURE_RECONNECT_THRESHOLD:
                logger.warning(
                    f"⚠️ [{cam_name}] PullMessages failed {consecutive_pull_failures} times in a row: {e}. "
                    "Breaking loop to reconnect."
                )
                return
            logger.debug(f"[{cam_name}] Failed to pull messages: {e}")

        time.sleep(0.5)

        if events and hasattr(events, 'NotificationMessage'):
            for msg in events.NotificationMessage:
                process_camera_event(msg, cam_conf)


def _suspend_after_transient_error(cam_name: str, error: Exception, backoff: _ReconnectBackoff) -> None:
    """一時的障害(通信断)向けのExponential Backoff。単発はdebug、連発のみWARNINGにする。"""
    wait_time: int = backoff.record_failure()

    if backoff.note_transient(time.time()):
        logger.warning(
            f"⚠️ [{cam_name}] 接続失敗 (Transient Network Error: {error}). "
            f"{backoff.consecutive_errors}回目の失敗。{wait_time}秒間監視をサスペンドします。"
        )
    else:
        logger.debug(f"🔄 [{cam_name}] Connection lost (Intentional/Transient): {error}. Reconnecting in {wait_time}s...")

    time.sleep(wait_time)


def _suspend_after_fatal_error(
    cam_conf: Dict[str, Any], error: Exception, backoff: _ReconnectBackoff, session: _CameraSession
) -> None:
    """致命的障害時のバックオフ・管理者通知・緊急診断。無意味なポート切り替えは行わない。"""
    cam_name: str = cam_conf['name']
    ip_address: str = cam_conf['ip']
    wait_time_fatal: int = backoff.record_failure()

    err_msg: str = str(error)
    detailed_info: str = ""
    if hasattr(error, 'detail'):
        detailed_info += f" | Detail: {error.detail}"
    if hasattr(error, 'content'):
        detailed_info += f" | Content: {str(error.content)[:200]}"

    full_err_msg: str = f"{err_msg}{detailed_info}"

    if backoff.consecutive_errors >= 5:
        logger.error(f"❌ [{cam_name}] Persistent Error ({backoff.consecutive_errors} times): {full_err_msg}")
        if backoff.consecutive_errors == 5 or backoff.consecutive_errors % 12 == 0:
            try:
                alert_msg: str = (
                    f"🚨 **カメラ監視アラート**\n[{cam_name}] の接続障害が継続しています"
                    f"（連続{backoff.consecutive_errors}回失敗）。\n詳細: {err_msg}"
                )
                send_push(
                    [{"type": "text", "text": alert_msg}],
                    target="discord",
                    channel="error"
                )
                logger.info(f"📤 [{cam_name}] 管理者へ障害通知を送信しました。")
            except Exception as push_err:
                logger.error(f"🚨 通知送信に失敗しました: {push_err}")

        if "Unknown error" in err_msg or "Unauthorized" in err_msg:
            logger.error("💡 Hint: Check PASSWORD and CAMERA TIME settings.")

    if session.pullpoint:
        _discard_pullpoint(session.pullpoint)

    # ホストが生きている場合のみ緊急診断を実行
    if is_host_reachable(ip_address):
        perform_emergency_diagnosis(ip_address)
    else:
        logger.warning(f"⚠️ [{cam_name}] Host is unreachable. Skipping diagnosis.")

    logger.warning(
        f"⚠️ [{cam_name}] 接続失敗 (Connection/ONVIF Error). "
        f"{backoff.consecutive_errors}回目の失敗。{wait_time_fatal}秒間監視をサスペンドします。"
    )
    time.sleep(wait_time_fatal)


def monitor_single_camera(cam_conf: Dict[str, Any]) -> None:
    """
    単一のカメラに対してONVIF接続を行い、イベントストリームを監視するプロセス。

    このループが直接持つ状態は「バックオフ(`_ReconnectBackoff`)」「初回接続かどうか」
    「無効化中かどうか」の3つだけで、接続・購読・イベント受信・障害時の待機は
    それぞれ専用の関数に分けてある(#662)。
    """
    cam_name: str = cam_conf['name']
    ip_address: str = cam_conf['ip']

    backoff = _ReconnectBackoff()
    is_first_connect: bool = True
    was_disabled: bool = False

    logger.info(f"🚀 [{cam_name}] Monitor thread started.")

    while True:
        # 0. #652: devices.json 上で無効化されていれば接続せず待機する(再有効化で再開)
        if not is_camera_enabled(cam_conf):
            if not was_disabled:
                logger.info(f"⏸️ [{cam_name}] devices.json で enabled=false のため監視を停止します(再有効化で再開)。")
                was_disabled = True
            time.sleep(DISABLED_POLL_INTERVAL_SEC)
            continue
        if was_disabled:
            logger.info(f"▶️ [{cam_name}] enabled=true に戻ったため監視を再開します。")
            was_disabled = False

        # 1. L3到達性の事前チェック (ホストダウン時の即時サスペンド)
        if not is_host_reachable(ip_address):
            backoff_time: int = backoff.record_failure()
            logger.warning(
                f"⚠️ [{cam_name}] 接続失敗 (No route to host). "
                f"{backoff.consecutive_errors}回目の失敗。{backoff_time}秒間監視をサスペンドします。"
            )
            time.sleep(backoff_time)
            continue

        session = _CameraSession()

        try:
            # 2-3. カメラ接続とイベント購読
            is_first_connect = _connect_and_subscribe(cam_conf, session, is_first_connect)
            # 接続成功時にエラーカウントをリセット
            backoff.reset()

            # 4. 監視ループ
            _pull_events_until_reconnect(cam_conf, session.pullpoint)

        except (RemoteDisconnected, ProtocolError, BrokenPipeError, ConnectionResetError) as e:
            _suspend_after_transient_error(cam_name, e, backoff)
            continue

        except Exception as e:
            _suspend_after_fatal_error(cam_conf, e, backoff, session)

        finally:
            # 【修正2】リソース解放処理の明示的な記録
            session.release(cam_name)
            # カメラ側のリソース解放（Unsubscribe等）が完了するまで待機する（Race condition防止）
            time.sleep(3)

async def main() -> None:
    if not WSDL_DIR: return logger.error("WSDL not found")
    # #411 S-L3: devices.json未配置等でconfig.CAMERASが空だと
    # ThreadPoolExecutor(max_workers=0)がValueErrorを送出しプロセスが即死する。
    # カメラが1台も無ければ何もせず正常終了する。
    if not config.CAMERAS:
        logger.warning("⚠️ config.CAMERAS が空のため camera_monitor は何も監視せず終了します。")
        return
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=max(1, len(config.CAMERAS))) as executor:
        await asyncio.gather(*[loop.run_in_executor(executor, monitor_single_camera, cam) for cam in config.CAMERAS])

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass