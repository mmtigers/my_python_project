# MY_HOME_SYSTEM/monitors/camera_monitor.py
import os
import sys
import logging
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
import requests.exceptions

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
from services.notification_service import send_push

# === ログ・定数設定 ===
logger = setup_logging("camera")


class _UnexpandedOnvifDateFilter(logging.Filter):
    """カメラが書式の展開されていない日付を送ってきたときの zeep のエラーだけを抑止する。

    2026-09-19 の実機で、ONVIF 応答の日時項目に '2026-%m-07' や '%Y-09-18' のような
    strftime の書式指定子が残ったままの値が届くことを確認した(カメラ側ファームウェアの
    不具合)。zeep はこれを解釈できず `Error during xml -> python translation` を
    トレースバック付きの ERROR で出したうえで、その項目を None にして処理を続ける
    (zeep/xsd/types/simple.py の parse_xmlelement)。監視の動作には影響しないが、
    health_watch が journal の標準エラーも見るようになった(check_journal_errors)ことで
    1日数回の誤報になるため、発生源で落とす。値に '%' を含まない日付の解釈エラーは
    別の原因でありうるので、従来どおり ERROR のまま出す。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, ValueError) and "ISO 8601" in str(exc) and "%" in str(exc):
            logger.debug(f"🔕 ONVIF応答の日付が書式未展開のため無視しました: {exc}")
            return False
        return True


logging.getLogger("zeep.xsd.types.simple").addFilter(_UnexpandedOnvifDateFilter())

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
# 10分の購読期限が切れる前に自発的に張り直す間隔(秒)。
# Issue #766: 以前は玄関カメラ専用の分岐だった。庭・駐車場(VIGI)はこの先回りが無いため、
# 10分ごとに期限切れ → PullMessages が3回連続失敗 → WARNING を出して張り直す、を
# 1台あたり毎時6回繰り返していた(実機ログの失敗間隔は 10:17〜10:24 と、
# CreatePullPointSubscription の既定 TerminationTime(10分)に一致)。
# 期限切れから再購読までの数秒はイベントを取りこぼすため、全カメラに適用する。
FORCE_RECONNECT_INTERVAL_SEC: int = 540
# PullMessages がこの回数連続で失敗したら、SESSION_LIFETIME を待たずに再接続する
PULL_FAILURE_RECONNECT_THRESHOLD: int = 3
RENEW_DURATION: str = "PT600S"

# 不具合修正: ONVIF(zeep/requests)のSOAP呼び出しにはライブラリ既定でタイムアウトが
# 一切無い。TCP接続自体は張れるが応答が返らない種類のネットワーク不調(実機で
# 駐車場カメラがnetwork_logger.pyの"Ping:Unreachable"を記録した直後、4時間以上
# 動体検知ログが完全に途絶える障害を確認)に遭遇すると、GetDeviceInformation()等の
# 呼び出しが例外を送出しないまま無期限にハングし、監視スレッドがバックオフ・
# エラーログ・Discord通知のいずれにも到達できず、完全に沈黙したまま止まり続ける
# (既存のbackoff/通知経路自体は正しく実装されているが、例外が発生しないため
# 一度も呼ばれない)。zeepのTransport.operation_timeoutを明示し、応答が
# この秒数を超えたら例外(requests.exceptions.Timeout)を送出させることで、
# 既存の`except Exception`によるバックオフ・再接続経路に必ず載るようにする。
ONVIF_OPERATION_TIMEOUT_SEC: int = 10

# Issue #703: NVR録画からスナップショットを切り出すときの試行計画。
# (新しい順に並べた当日の録画ファイルの何番目を使うか, ファイル末尾から何秒手前にシークするか)
# 録画は fragmented MP4(moof/mdat の繰り返し)で、CIFS 越しに見えるファイル末尾は
# 常に最後の mdat の途中で切れている(ファイルサイズが 256KiB 単位で伸び、ffmpeg は
# "partial file" / "Invalid NAL unit size" で失敗する)。以前は末尾1秒前(-sseof -1)を
# 読んでいたため、不完全な末尾フラグメントに当たると失敗し、動体検知の約3割で画像が
# 落ちていた。2026-09-19 の実機計測(書き込み中のファイルに連続20回):
#   庭カメラ   -sseof -1: 0/20  -3: 20/20  -5: 20/20
#   駐車場カメラ -sseof -1: 4/20  -3: 20/20  -5: 20/20
# 最終試行は、書き込みが完了している1つ前のセグメントを使う(録画ファイルの切り替え直後で
# 最新ファイルにまだ完成フラグメントが無い場合の保険。直前10分の末尾=検知の直前の映像)。
NVR_SNAPSHOT_ATTEMPTS: tuple = ((0, 3), (0, 6), (1, 3))

# クールダウンの秒数を設定 (config.py から読み込み。未定義時は60秒)
MOTION_COOLDOWN_SEC: int = getattr(config, 'MOTION_COOLDOWN_SEC', 60)

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
# #1161: 終了時の解除結果をカメラ名付きでログに出すため、購読ごとのカメラ名を持つ
# (キーは id(pullpoint)。active_pullpoints と同じ Lock で保護し、同時に追加・削除する)。
_pullpoint_names: Dict[int, str] = {}

# #1161: 終了時(cleanup_handler)の購読解除にかける合計の上限(秒)。親
# (unified_server.py の lifespan 終了処理)は SIGTERM 後 `wait(timeout=5)` で待ち、
# 超えたら SIGKILL する。以前は1台ずつ順番に解除しており(1台あたり最大
# ONVIF_OPERATION_TIMEOUT_SEC=10秒)、1台でも応答しないと残りのカメラが解除されない
# まま kill されていた。全台を並列に解除し、この秒数で打ち切って終了する
# (打ち切った購読もカメラ側の TerminationTime で失効する)。
CLEANUP_UNSUBSCRIBE_BUDGET_SEC: float = 3.0


def _add_pullpoint(pullpoint: Any, cam_name: str = "?") -> None:
    with _pullpoints_lock:
        active_pullpoints.append(pullpoint)
        _pullpoint_names[id(pullpoint)] = cam_name


def _discard_pullpoint(pullpoint: Any) -> None:
    """active_pullpointsから安全に削除する(既に削除済みでも例外を出さない)。"""
    with _pullpoints_lock:
        try:
            active_pullpoints.remove(pullpoint)
        except ValueError:
            pass
        _pullpoint_names.pop(id(pullpoint), None)


def _unsubscribe_for_shutdown(svc: Any, cam_name: str) -> None:
    """終了時に1つの購読を解除し、結果をカメラ名付きで1行ログに出す(例外は外に出さない)。"""
    try:
        if hasattr(svc, 'Unsubscribe'):
            svc.Unsubscribe()
        elif hasattr(svc, 'service') and hasattr(svc.service, 'Unsubscribe'):
            svc.service.Unsubscribe(_soapheaders=None)
        else:
            return
        logger.info(f"🗑️ [{cam_name}] Unsubscribed on shutdown.")
    except Exception as e:  # noqa: BLE001 (解除できなくても終了は続ける。購読は TerminationTime で失効する)
        # #1161: 以前は `pass` で捨てており、解除できなかったことがログに残らなかった
        logger.warning(f"⚠️ [{cam_name}] Unsubscribe on shutdown failed (expires by TerminationTime): {e}")


def cleanup_handler(signum: int, frame: Any) -> None:
    """プロセス終了時のクリーンアップ。

    #1161: 全カメラの購読を並列に解除し、合計 CLEANUP_UNSUBSCRIBE_BUDGET_SEC 秒で
    打ち切って終了する(親の kill 猶予5秒に収めるため)。各カメラの成否はログに出す。
    """
    logger.info(f"🛑 Shutdown signal ({signum}) received. Cleaning up subscriptions...")
    # 他スレッド(monitor_single_camera)が同時に active_pullpoints を変更しうるため、
    # イテレーション中の RuntimeError(list changed size)を避けてコピーを走査する
    with _pullpoints_lock:
        targets = [(svc, _pullpoint_names.get(id(svc), "?")) for svc in active_pullpoints]
    workers = [
        (threading.Thread(target=_unsubscribe_for_shutdown, args=(svc, name), daemon=True), name)
        for svc, name in targets
    ]
    for worker, _ in workers:
        worker.start()
    deadline = time.monotonic() + CLEANUP_UNSUBSCRIBE_BUDGET_SEC
    for worker, name in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            logger.warning(
                f"⚠️ [{name}] Unsubscribe on shutdown did not finish within "
                f"{CLEANUP_UNSUBSCRIBE_BUDGET_SEC}s (expires by TerminationTime)."
            )
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

# RTSP の既定ポート。常時録画(nvr-*.service)が使う経路なので、ONVIF と並べて診断する
RTSP_PORT: int = 554


def _diagnosis_ports(cam_conf: Dict[str, Any]) -> List[int]:
    """緊急診断で調べるポート(#1151)。設定上の ONVIF ポートと RTSP(554)だけを見る。

    以前は全カメラ共通で [80, 2020] を調べており、ONVIF が80番の玄関カメラでは
    使っていない2020番が毎回 CLOSED と出て誤判定のノイズになっていた。
    """
    onvif_port: int = cam_conf.get('port', 80)
    return [onvif_port] if onvif_port == RTSP_PORT else [onvif_port, RTSP_PORT]


def perform_emergency_diagnosis(ip: str, ports: Optional[List[int]] = None) -> Dict[int, bool]:
    """接続障害時にポートの状態を診断する。ports 省略時は ONVIF 既定(80)と RTSP(554)。"""
    results: Dict[int, bool] = {}
    msg = f"🚑 [Diagnosis] Checking {ip}:\n"
    for port in (ports if ports is not None else [80, RTSP_PORT]):
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


def _resolve_nvr_folder(cam_conf: dict) -> Optional[str]:
    """カメラの NVR 録画フォルダの絶対パスを返す。存在しなければ ERROR を記録して None。"""
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
    return nas_folder


def _list_nvr_mp4_files(nas_folder: str) -> list[str]:
    """NVR 録画フォルダの当日分の mp4 を新しい順に返す。

    #411 S-L10: 以前は "**/*.mp4" で全期間(NVRの保存期間分、数十日)を毎回CIFS越しに
    再帰globしていたため動体検知のたびに高コストなI/Oが発生していた。録画ファイル名は
    camera_service.py と同じ "{YYYYMMDD}_*.mp4" 形式なので、当日分だけに絞って検索する。
    """
    import glob

    # 新しい順。Issue #703: リトライの間に録画ファイルが切り替わりうるため試行ごとに取り直す。
    return sorted(
        (f for pattern in _nvr_search_patterns(nas_folder, dt_class.now()) for f in glob.glob(pattern)),
        key=os.path.getmtime, reverse=True,
    )


def _build_nvr_snapshot_cmd(src: str, seconds_before_eof: int, output_tmp: str) -> list[str]:
    """録画ファイル末尾の数秒手前のフレームを1枚抽出する ffmpeg コマンド。"""
    return [
        "ffmpeg", "-y",
        "-sseof", f"-{seconds_before_eof}",
        "-i", src,
        "-vframes", "1",
        "-q:v", "2",    # 高画質
        output_tmp
    ]


def _ffmpeg_stderr_last_line(e: "subprocess.CalledProcessError") -> str:
    """ffmpeg の stderr の最終行(原因が書かれる行)を返す。stderr が空なら "no stderr"。"""
    stderr_lines = (e.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    detail = stderr_lines[-1] if stderr_lines else "no stderr"
    return detail


def _remove_quietly(path: str) -> None:
    """一時ファイルを削除する。既に無い・削除できない場合は何もしない。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def capture_snapshot_from_nvr(cam_conf: dict, target_time: dt_class = None) -> Optional[bytes]:
    """
    NAS(NVR)に常時録画されている最新の動画ファイル(.mp4)から、
    FFmpegを使用して該当時刻のフレームを切り出す（カメラ本体のRTSP負荷ゼロ）
    """
    import subprocess
    import time

    if target_time is None:
        target_time = dt_class.now()

    nas_folder = _resolve_nvr_folder(cam_conf)
    if nas_folder is None:
        return None

    # 録画ファイルが無い場合の判定は各試行の中で行う(#707 レビュー: 以前はここでも一覧を
    # 取得しており、正常系でも初回に CIFS 越しの glob が2回走っていた。#411 S-L10 が
    # 問題にした「動体検知のたびの高コストなI/O」を増やさないため1回にまとめる)。

    # C-L7: 実行環境のTMPDIR等に追従させるため /tmp 直書きではなく tempfile.gettempdir() 経由で解決する
    output_tmp = os.path.join(tempfile.gettempdir(), f"snapshot_{cam_conf['name']}_{uuid.uuid4().hex}.jpg")
    
    # 設計書「エラーハンドリングと自動復旧」準拠のリトライ。試行ごとの対象ファイルと
    # シーク位置は NVR_SNAPSHOT_ATTEMPTS を参照(Issue #703)。
    max_retries = len(NVR_SNAPSHOT_ATTEMPTS)
    try:
        for attempt, (file_index, seconds_before_eof) in enumerate(NVR_SNAPSHOT_ATTEMPTS, start=1):
            # Issue #703: リトライの間に録画ファイルが切り替わりうるため試行ごとに取り直す。
            mp4_files = _list_nvr_mp4_files(nas_folder)
            if not mp4_files:
                logger.warning(f"⚠️ [{cam_conf['name']}] No NVR video files found in {nas_folder}.")
                return None
            # 1つ前のセグメントが無い(当日最初の録画ファイル等)ときは最新ファイルで代用する
            src = mp4_files[file_index] if file_index < len(mp4_files) else mp4_files[0]
            try:
                # 録画ファイル末尾の数秒手前のフレームを抽出（動体検知直後の映像）。
                # 末尾の不完全なフラグメントを避けるため末尾ちょうどは読まない(Issue #703)。
                cmd = _build_nvr_snapshot_cmd(src, seconds_before_eof, output_tmp)

                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10, check=True)

                if os.path.exists(output_tmp):
                    with open(output_tmp, "rb") as f:
                        image_data = f.read()
                    return image_data

            except subprocess.TimeoutExpired:
                logger.warning(f"⏳ [{cam_conf['name']}] FFmpeg timeout on NVR file (Attempt {attempt}/{max_retries})")
            except subprocess.CalledProcessError as e:
                # Issue #703: 以前は stderr を捨てていたため "exit status 69" しか分からず、
                # 原因(末尾フラグメントが不完全)の特定に実機での再現が必要だった。
                detail = _ffmpeg_stderr_last_line(e)
                logger.warning(
                    f"⚠️ [{cam_conf['name']}] FFmpeg extraction failed "
                    f"(rc={e.returncode}, src={os.path.basename(src)}, sseof=-{seconds_before_eof}): "
                    f"{detail} (Attempt {attempt}/{max_retries})"
                )
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
        _remove_quietly(output_tmp)


def save_image_from_stream(cam_name: str, event_type: str = "motion") -> Optional[str]:
    cam_conf = next((c for c in config.CAMERAS if c["name"] == cam_name), None)
    if not cam_conf:
        return None

    logger.debug(f"📸 [{cam_name}] 映像フレームの取得を開始します (方式: NVR切り出し)")
    
    # ここを cv2 から nvr に変更
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

    # #1160: 以前は3600秒(最大1時間)だった。待機中(time.sleep)は到達性を見ないため、
    # 85分を超える障害ではカメラが復旧してから監視が再開するまで最大60分かかっていた
    # (NVRの録画は先に戻るため「動体通知・スナップショットだけが来ない」状態が見えない)。
    # 再試行は ping + ONVIF 接続1回で軽いため、5分にする。
    MAX_BACKOFF_SEC: int = 300
    TRANSIENT_WINDOW_SEC: int = 15       # これ以内に再発したら「連続」とみなす
    TRANSIENT_WARN_THRESHOLD: int = 3    # 連続何回でWARNINGへ格上げするか
    PERSISTENT_ERROR_THRESHOLD: int = 5  # 連続何回目から「Persistent Error」とみなすか
    # #1160: 継続障害の通知間隔は、再試行の間隔(MAX_BACKOFF_SEC)ではなく時間で決める。
    # 以前は「12の倍数回目に send_push」「5回目以降は毎回 ERROR(=catch-all で Discord)」
    # だったため、上限を下げると通知まで比例して増える。上限3600秒のときの定常の間隔
    # (send_push は 12×3600秒ごと、ERROR は3600秒ごと)を時間で保つ。
    PERSISTENT_RENOTIFY_SEC: int = 12 * 3600
    PERSISTENT_ERROR_LOG_INTERVAL_SEC: int = 3600

    def __init__(self) -> None:
        self.consecutive_errors: int = 0
        self.transient_error_count: int = 0
        self.last_transient_error_time: float = 0
        # この連続障害で最後に send_push / ERROR を出した時刻(time.monotonic)。None は未通知
        self.last_persistent_push_at: Optional[float] = None
        self.last_persistent_error_at: Optional[float] = None
        # #1146: この連続障害の最初の失敗の時刻(time.monotonic)。復旧ログの停止時間に使う
        self.failure_started_at: Optional[float] = None

    def record_failure(self) -> int:
        """失敗を1件数え、次に待つ秒数を返す。"""
        if self.consecutive_errors == 0:
            self.failure_started_at = time.monotonic()
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

    def persistent_alert_level(self, now: float) -> str:
        """継続障害(PERSISTENT_ERROR_THRESHOLD 回目以降)をどう知らせるかを決め、時刻を記録する。

        戻り値: "push"(ERROR + send_push)/ "error"(ERROR。catch-all で Discord へ)/
        "warning"(ログのみ)/ "none"(まだ継続障害ではない)。この連続障害で最初の
        継続障害は必ず "push"、以降は PERSISTENT_RENOTIFY_SEC ごとに "push"、
        PERSISTENT_ERROR_LOG_INTERVAL_SEC ごとに "error"、それ以外は "warning"。
        """
        if self.consecutive_errors < self.PERSISTENT_ERROR_THRESHOLD:
            return "none"
        if self.last_persistent_push_at is None or now - self.last_persistent_push_at >= self.PERSISTENT_RENOTIFY_SEC:
            self.last_persistent_push_at = now
            self.last_persistent_error_at = now
            return "push"
        if self.last_persistent_error_at is None or now - self.last_persistent_error_at >= self.PERSISTENT_ERROR_LOG_INTERVAL_SEC:
            self.last_persistent_error_at = now
            return "error"
        return "warning"

    def reset(self) -> None:
        """接続成功時に呼ぶ。次の失敗は再び最小の待機から始まり、継続障害の通知も初回から数え直す。"""
        self.consecutive_errors = 0
        self.last_persistent_push_at = None
        self.last_persistent_error_at = None
        self.failure_started_at = None


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

        # #1162: 冪等にする。障害時は例外ハンドラで(待機の前に)解放し、finally でも
        # もう一度呼ばれるため、2回目は何もしない(Unsubscribe を二重に送らない)。
        self.pullpoint = None
        self.events_service = None
        self.mycam = None
        logger.debug(f"✨ [{cam_name}] Resource cleanup completed.")


def _connect_and_subscribe(
    cam_conf: Dict[str, Any], session: _CameraSession, is_first_connect: bool
) -> bool:
    """
    ONVIF接続からイベント購読までを行い、確保したリソースを `session` に詰める。

    戻り値は更新後の `is_first_connect`(購読まで成功した初回だけINFOでモデル名を出すため)。
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
    devicemgmt.zeep_client.transport.operation_timeout = ONVIF_OPERATION_TIMEOUT_SEC

    if not check_camera_time(devicemgmt, cam_name):
        raise ConnectionRefusedError(f"[{cam_name}] Time verification failed. Check camera clock.")

    device_info: Any = devicemgmt.GetDeviceInformation()
    # #1145: この時点ではまだ購読していない。「Connected」(INFO)と is_first_connect の更新は
    # 購読まで成功した後(return の直前)に行う。以前はここで行っていたため、続く
    # CreatePullPointSubscription() が例外を出すと更新した値が呼び出し元へ返らず、
    # 購読に失敗し続ける間リトライのたびに「初回接続」扱いの INFO が出ていた。
    logger.debug(f"[{cam_name}] Device OK. Model: {device_info.Model}")

    # 3. イベント購読
    session.events_service = session.mycam.create_events_service()
    session.events_service.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
    session.events_service.zeep_client.transport.operation_timeout = ONVIF_OPERATION_TIMEOUT_SEC

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
    pullpoint.zeep_client.transport.operation_timeout = ONVIF_OPERATION_TIMEOUT_SEC

    _add_pullpoint(pullpoint, cam_name)
    session.pullpoint = pullpoint

    if is_first_connect:
        logger.info(f"📡 [{cam_name}] Connected. Model: {device_info.Model}")
        is_first_connect = False
    else:
        logger.debug(f"📡 [{cam_name}] Connected. Model: {device_info.Model} (Reconnected)")
    return is_first_connect


def _pull_events_until_reconnect(cam_conf: Dict[str, Any], pullpoint: Any) -> None:
    """
    購読済みのセッションでイベントを受け続ける。再接続すべき状況になったら return する。

    return する条件は4つ: セッション寿命(SESSION_LIFETIME)の到達、devices.json での
    無効化、購読期限切れ前の自発的再接続タイマー、PullMessages の失敗。
    """
    cam_name: str = cam_conf['name']
    session_start_time: float = time.time()

    # --- 再接続（Subscribeし直し）タイマー ---
    # 10分の有効期限が切れる前に、自発的にセッションを切り替える
    last_subscribe_time: float = time.time()

    # PullMessages の連続失敗回数。カメラの再起動等で
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

        # --- 購読期限切れの前に張り直す(全カメラ。Issue #766) ---
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


# #1147: 例外メッセージ別のヒント。以前は "Unknown error" でも常に「パスワードと時刻を
# 確認」を ERROR で出しており、購読残存の疑い("Action Failed")など無関係な原因でも
# 調査を誤誘導し、health_watch の app_logs 判定と自動調査の起動要因にもなっていた。
_AUTH_ERROR_MARKERS = ("Unauthorized", "NotAuthorized")
_ACTION_FAILED_MARKER = "Action Failed"


def _error_hint(err_msg: str) -> Optional[str]:
    """例外メッセージから原因の切り分けに役立つヒントを返す(該当なしは None)。"""
    if any(marker in err_msg for marker in _AUTH_ERROR_MARKERS):
        return "💡 Hint: Check PASSWORD and CAMERA TIME settings."
    if _ACTION_FAILED_MARKER in err_msg:
        return (
            "💡 Hint: カメラ側に以前の購読が残っている可能性があります"
            "(TerminationTime の経過で自然に解消します)。"
        )
    return None


def _describe_error(error: Exception) -> str:
    """ログ用に、例外のメッセージへ SOAP Fault の詳細(Subcodes/Detail/Content)を付ける。

    #1144: "Action Failed" の Fault は Detail・Content が空で原因を追えなかったため、
    zeep.exceptions.Fault が持つ subcodes も出す。
    """
    detailed_info: str = ""
    subcodes = getattr(error, 'subcodes', None)
    if subcodes:
        detailed_info += f" | Subcodes: {', '.join(str(code) for code in subcodes)}"
    if hasattr(error, 'detail'):
        detailed_info += f" | Detail: {error.detail}"
    if hasattr(error, 'content'):
        detailed_info += f" | Content: {str(error.content)[:200]}"
    return f"{error}{detailed_info}"


def _report_persistent_error(
    cam_name: str, backoff: _ReconnectBackoff, err_msg: str, full_err_msg: str
) -> str:
    """継続障害(連続5回目以降)のログと管理者通知。致命的・一時的・到達不可の全経路で共有する。

    #1152: 以前は致命的エラーの経路にしか無く、一時的障害(通信断)の経路へ分類された
    障害は何時間続いても通知されなかった。戻り値は `persistent_alert_level` の判定。
    """
    alert_level: str = backoff.persistent_alert_level(time.monotonic())
    if alert_level == "push":
        # チャンネル再設計(2026-09): 直後に同じ内容をsend_push(channel="report")で
        # 明示送信するため、ハンドラ経由(catch-all)の通知はopt-outして二重通知を避ける。
        logger.error(
            f"❌ [{cam_name}] Persistent Error ({backoff.consecutive_errors} times): {full_err_msg}",
            extra={"skip_discord": True},
        )
        try:
            alert_msg: str = (
                f"🚨 **カメラ監視アラート**\n[{cam_name}] の接続障害が継続しています"
                f"（連続{backoff.consecutive_errors}回失敗）。\n詳細: {err_msg}"
            )
            send_push(
                [{"type": "text", "text": alert_msg}],
                target="discord",
                channel="report"
            )
            logger.info(f"📤 [{cam_name}] 管理者へ障害通知を送信しました。")
        except Exception as push_err:
            logger.error(f"🚨 通知送信に失敗しました: {push_err}")
    elif alert_level == "error":
        # send_pushを送らない回は、skip_discordを付けず
        # catch-all(core.logger.DiscordErrorHandler)経由の通知に任せる。
        logger.error(
            f"❌ [{cam_name}] Persistent Error ({backoff.consecutive_errors} times): {full_err_msg}"
        )
    elif alert_level == "warning":
        # #1160: 通知間隔の内側の回はログだけ(WARNING は Discord へ送られない)
        logger.warning(
            f"❌ [{cam_name}] Persistent Error ({backoff.consecutive_errors} times): {full_err_msg}"
        )

    if alert_level != "none":
        hint: Optional[str] = _error_hint(err_msg)
        if hint:
            # #1147: ヒントは WARNING(ERROR にすると health_watch・自動調査の起動要因になる)
            logger.warning(hint)
    return alert_level


def _report_recovery(cam_name: str, backoff: _ReconnectBackoff) -> None:
    """障害からの復旧を INFO で出し、障害通知を送っていた場合は復旧も通知する(#1146)。

    以前は2回目以降の接続が DEBUG の「(Reconnected)」だけで、障害通知は届くのに
    復旧は届かず、復旧時刻は後の「9 minutes passed」から逆算するしかなかった。
    `backoff.reset()` の前に呼ぶこと(リセットで失敗回数と通知の記録が消える)。
    """
    if backoff.consecutive_errors <= 0:
        return
    downtime_sec: int = 0
    if backoff.failure_started_at is not None:
        downtime_sec = max(0, int(time.monotonic() - backoff.failure_started_at))
    summary: str = f"停止約{downtime_sec}秒・失敗{backoff.consecutive_errors}回"
    logger.info(f"✅ [{cam_name}] 復旧しました({summary})")
    if backoff.last_persistent_push_at is None:
        return
    try:
        send_push(
            [{"type": "text", "text": f"✅ **カメラ監視**\n[{cam_name}] の接続が復旧しました（{summary}）。"}],
            target="discord",
            channel="report"
        )
    except Exception as push_err:
        logger.error(f"🚨 復旧通知の送信に失敗しました: {push_err}")


def _suspend_after_transient_error(cam_name: str, error: Exception, backoff: _ReconnectBackoff) -> None:
    """一時的障害(通信断)向けのExponential Backoff。単発はdebug、連発のみWARNINGにする。

    #1152: 連続5回目以降は致命的エラーと同じ継続障害の通知を送る(接続拒否が続く等)。
    """
    wait_time: int = backoff.record_failure()

    if backoff.note_transient(time.time()):
        logger.warning(
            f"⚠️ [{cam_name}] 接続失敗 (Transient Network Error: {error}). "
            f"{backoff.consecutive_errors}回目の失敗。{wait_time}秒間監視をサスペンドします。"
        )
    else:
        logger.debug(f"🔄 [{cam_name}] Connection lost (Intentional/Transient): {error}. Reconnecting in {wait_time}s...")

    _report_persistent_error(cam_name, backoff, str(error), _describe_error(error))

    time.sleep(wait_time)


def _suspend_after_fatal_error(
    cam_conf: Dict[str, Any], error: Exception, backoff: _ReconnectBackoff
) -> None:
    """致命的障害時のバックオフ・管理者通知・緊急診断。無意味なポート切り替えは行わない。

    #1162: 購読とセッションの解放は、呼び出し元がこの関数(=待機)より前に済ませる。
    """
    cam_name: str = cam_conf['name']
    ip_address: str = cam_conf['ip']
    wait_time_fatal: int = backoff.record_failure()

    _report_persistent_error(cam_name, backoff, str(error), _describe_error(error))

    # ホストが生きている場合のみ緊急診断を実行
    if is_host_reachable(ip_address):
        perform_emergency_diagnosis(ip_address, _diagnosis_ports(cam_conf))
    else:
        logger.warning(f"⚠️ [{cam_name}] Host is unreachable. Skipping diagnosis.")

    logger.warning(
        f"⚠️ [{cam_name}] 接続失敗 (Connection/ONVIF Error). "
        f"{backoff.consecutive_errors}回目の失敗。{wait_time_fatal}秒間監視をサスペンドします。"
    )
    time.sleep(wait_time_fatal)


# #1152: 一時的障害(通信断)として扱う例外。requests の ConnectionError(接続拒否・
# No route to host・ConnectTimeout を含む)も通信断であり、以前のように
# "Connection/ONVIF Error"(致命的扱い)へ入れると、SOAP Fault(認証失敗・Action Failed)
# 等のアプリ側の異常とログ上で区別できなかった。
TRANSIENT_ERRORS = (
    RemoteDisconnected,
    ProtocolError,
    BrokenPipeError,
    ConnectionResetError,
    requests.exceptions.ConnectionError,
)


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
            # #1152 横展開: Ping 不達が続く場合も、他の経路と同じく継続障害として通知する
            _report_persistent_error(cam_name, backoff, "No route to host", "No route to host (Ping unreachable)")
            time.sleep(backoff_time)
            continue

        session = _CameraSession()

        try:
            # 2-3. カメラ接続とイベント購読
            is_first_connect = _connect_and_subscribe(cam_conf, session, is_first_connect)
            # #1146: 障害からの復旧をログ(・障害通知を送っていれば通知)に出してからリセットする
            _report_recovery(cam_name, backoff)
            # 接続成功時にエラーカウントをリセット
            backoff.reset()

            # 4. 監視ループ
            _pull_events_until_reconnect(cam_conf, session.pullpoint)

        except TRANSIENT_ERRORS as e:
            # #1162: 待機(最大 MAX_BACKOFF_SEC)の前に購読と HTTP セッションを解放する。
            # 以前は finally まで解放されず、待機中ずっとカメラ側の購読枠とソケットを占有していた。
            session.release(cam_name)
            _suspend_after_transient_error(cam_name, e, backoff)
            continue

        except Exception as e:
            session.release(cam_name)  # #1162: 同上
            _suspend_after_fatal_error(cam_conf, e, backoff)

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