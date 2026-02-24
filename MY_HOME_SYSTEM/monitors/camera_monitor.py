# MY_HOME_SYSTEM/monitors/camera_monitor.py
import os
import sys
import asyncio
import time
import socket
import logging
import subprocess
import traceback
import signal
import uuid
import glob
import requests
import datetime
import cv2
import platform
from datetime import datetime as dt_class, timedelta
from typing import Optional, Dict, Any, Tuple, List
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
from core.logger import setup_logging
from core.database import save_log_generic
from services.notification_service import send_push

# === ログ・定数設定 ===
logger = setup_logging("camera")

try:
    ASSETS_DIR: str = os.path.join(config.ASSETS_DIR, "snapshots")
    os.makedirs(ASSETS_DIR, exist_ok=True)
except (PermissionError, OSError) as e:
    # NAS等が書き込み不可の場合、ローカルの一時ディレクトリにフォールバック
    fallback_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp_assets", "snapshots")
    logger.warning(f"⚠️ Failed to create NAS directory '{ASSETS_DIR}': {e}")
    logger.warning(f"   -> 📂 Switching to local fallback: '{fallback_path}'")
    ASSETS_DIR = fallback_path
    os.makedirs(ASSETS_DIR, exist_ok=True)

BINDING_NAME: str = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'
PRIORITY_MAP: Dict[str, int] = {"intrusion": 100, "person": 80, "vehicle": 50, "motion": 10}
SESSION_LIFETIME: int = 50  
RENEW_DURATION: str = "PT600S"

# クールダウンの秒数を設定 (config.py から読み込み。未定義時は60秒)
MOTION_COOLDOWN_SEC: int = getattr(config, 'MOTION_COOLDOWN_SEC', 60)

# 各カメラの最終検知時刻を保持する辞書
last_motion_detected: Dict[str, float] = {}

active_pullpoints: List[Any] = []

def cleanup_handler(signum: int, frame: Any) -> None:
    """プロセス終了時のクリーンアップ。"""
    logger.info(f"🛑 Shutdown signal ({signum}) received. Cleaning up subscriptions...")
    for svc in active_pullpoints:
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
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.debug(f"Ping execution failed for {ip}: {e}")
        return False

def find_wsdl_path() -> Optional[str]:
    """WSDLファイルのディレクトリを動的に探索する。"""
    for path in sys.path:
        if not os.path.exists(path):
            continue
        candidate_standard = os.path.join(path, 'onvif', 'wsdl')
        candidate_direct = os.path.join(path, 'wsdl')
        for candidate in [candidate_standard, candidate_direct]:
            if os.path.exists(os.path.join(candidate, 'devicemgmt.wsdl')):
                return candidate
    return None

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
        cam_time = dt_class(utc.Date.Year, utc.Date.Month, utc.Date.Day,
                           utc.Time.Hour, utc.Time.Minute, utc.Time.Second)
        
        # 簡易的なUTC->Local変換 (JST前提)
        cam_time_jst = cam_time + timedelta(hours=9)
        now_jst = dt_class.now()
        
        diff = abs((now_jst - cam_time_jst).total_seconds())
        
        if diff > 300: # 5分以上のズレ
            logger.warning(f"⏰ [{cam_name}] Time Drift Detected! Camera: {cam_time_jst}, Server: {now_jst}, Diff: {diff:.0f}s")
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

# def capture_snapshot_from_nvr(cam_conf: Dict[str, Any], target_time: Optional[datetime.datetime] = None) -> Optional[bytes]:
#     """
#     NASの録画データから指定時刻の画像を切り出す（I/O遅延耐性・根本対策済み）。
#     リトライ上限到達時やエラー発生時も、確実に一時ファイル等のリソースを解放する。

#     Args:
#         cam_conf (Dict[str, Any]): カメラ設定辞書
#         target_time (Optional[datetime.datetime]): 取得対象の時刻。Noneの場合は現在時刻を使用。

#     Returns:
#         Optional[bytes]: 取得した画像データのバイト列。失敗時はNone。
#     """
#     if target_time is None:
#         target_time = dt_class.now()
        
#     sub_dir: Optional[str] = "parking" if "Parking" in cam_conf['id'] else "garden" if "Garden" in cam_conf['id'] else None
#     if not sub_dir:
#         return None

#     record_dir: str = os.path.join(config.NVR_RECORD_DIR, sub_dir)
    
#     max_retries: int = 10     
#     retry_delay: float = 1.0    

#     # 並行処理時の競合を防ぐため、一時ファイル名を完全にユニーク化
#     unique_id: str = uuid.uuid4().hex[:8]
#     tmp_path: str = f"/tmp/snapshot_{cam_conf['id']}_{unique_id}.jpg"
    
#     cam_name: str = cam_conf['name']

#     try:
#         for attempt in range(1, max_retries + 1):
#             try:
#                 files: List[str] = sorted(glob.glob(os.path.join(record_dir, "*.mp4")))
#                 if not files:
#                     logger.warning(f"⚠️ [{cam_name}] No .mp4 files found in {record_dir}")
#                     return None

#                 target_file: str = files[-1]
#                 for f_path in reversed(files):
#                     try:
#                         f_dt: datetime.datetime = dt_class.strptime(os.path.basename(f_path).split('.')[0], "%Y%m%d_%H%M%S")
#                         if f_dt <= target_time:
#                             target_file = f_path
#                             break
#                     except ValueError:
#                         continue
                
#                 f_start_dt: datetime.datetime = dt_class.strptime(os.path.basename(target_file).split('.')[0], "%Y%m%d_%H%M%S")
                
#                 exact_seek: float = (target_time - f_start_dt).total_seconds()
#                 seek_sec: float = max(0.0, exact_seek - 1.5)
                
#                 # FFmpeg実行前に、万が一の残留ファイルを削除（State Leak防止）
#                 if os.path.exists(tmp_path):
#                     try:
#                         os.remove(tmp_path)
#                     except OSError as e:
#                         logger.warning(f"⚠️ [{cam_name}] Failed to clear temp file before run: {e}")

#                 cmd: List[str] = ["ffmpeg", "-y", "-ss", str(seek_sec), "-i", target_file, "-frames:v", "1", "-q:v", "2", tmp_path]
#                 res: subprocess.CompletedProcess = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
                
#                 if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
#                     logger.info(f"✅ [{cam_name}] Snapshot created successfully (Attempt {attempt}/{max_retries})")
                    
#                     with open(tmp_path, "rb") as f: 
#                         image_data: bytes = f.read()
                        
#                     return image_data
                
#                 # 失敗時、リトライ状況を記録
#                 logger.warning(f"⏳ [{cam_name}] Frame not yet flushed or EOF. Retrying {attempt}/{max_retries}...")
                
#                 if attempt == max_retries:
#                     logger.error(f"🚨 FFmpeg Stderr Output: {res.stderr.strip()}")
                    
#                 time.sleep(retry_delay)

#             except Exception as e:
#                 logger.error(f"🚨 Exception during capture attempt {attempt}: {e}")
#                 time.sleep(retry_delay)

#         # ループを抜けたということは、リトライ上限到達
#         logger.error(f"❌ [{cam_name}] Failed to capture snapshot after {max_retries} attempts.")
#         return None

#     except Exception as e:
#         logger.error(f"❌ [{cam_name}] Unhandled exception in capture_snapshot_from_nvr: {e}")
#         return None

#     finally:
#         # 必ずリソース（一時ファイル）を解放し、ログを出力する
#         if os.path.exists(tmp_path):
#             try:
#                 os.remove(tmp_path)
#             except OSError as e:
#                 logger.warning(f"⚠️ [{cam_name}] Failed to remove temp file during cleanup: {e}")
                
#         logger.info(f"🔌 [{cam_name}] Connection closed / Resource released.")

def capture_snapshot_from_stream_cv2(cam_conf: Dict[str, Any]) -> Optional[bytes]:
    """
    OpenCVを使用してRTSPストリームから最新のフレームを取得する。
    
    バッファに古いフレームが滞留するのを防ぐため、内部バッファサイズを制限しつつ、
    最新フレームに追いつくまで高速で grab() を回して古いフレームを破棄する。

    Args:
        cam_conf (Dict[str, Any]): カメラ設定辞書 (ip, user, passなどを含む)

    Returns:
        Optional[bytes]: 取得した画像データのバイト列。失敗・EOF到達時はNone。
    """
    cam_name: str = cam_conf.get('name', 'Unknown')
    
    # ストリームURLの構築（設定に rtsp_url があれば優先、なければ標準フォーマットを推測）
    rtsp_url: str = cam_conf.get(
        'rtsp_url', 
        f"rtsp://{cam_conf.get('user')}:{cam_conf.get('pass')}@{cam_conf.get('ip')}:554/stream1"
    )
    
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        logger.error(f"❌ [{cam_name}] Failed to open RTSP stream.")
        return None
    
    try:
        # バックエンドのバッファサイズを最小限(1)に設定（環境依存だが遅延防止に有効）
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        # 溜まっている古いフレームを高速で読み飛ばす（バッファクリア）
        frames_to_clear: int = 5 
        for _ in range(frames_to_clear):
            if not cap.grab():
                logger.warning(f"⚠️ [{cam_name}] Stream disconnected during grab() or EOF reached.")
                return None
                
        # 最新フレームの読み出し
        ret, frame = cap.retrieve()
        if ret and frame is not None:
            logger.debug(f"✅ [{cam_name}] Snapshot captured directly from stream.")
            success, buffer = cv2.imencode('.jpg', frame)
            if success:
                return buffer.tobytes()
        
        logger.error(f"❌ [{cam_name}] Failed to retrieve or decode frame after grab.")
        return None
        
    except Exception as e:
        logger.error(f"🚨 [{cam_name}] Exception during RTSP capture: {e}")
        return None
    finally:
        # 無駄なリソースやファイルディスクリプタを残さないよう確実に解放
        cap.release()
        logger.debug(f"🔌 [{cam_name}] Connection closed / Resource released.")


def save_image_from_stream(cam_conf: Dict[str, Any], trigger_type: str) -> None:
    """画像を保存し、Discordへ直接アップロード通知を行う（根本対策済）"""
    image_data = capture_snapshot_from_stream_cv2(cam_conf)
    if not image_data: 
        logger.warning(f"⚠️ [{cam_conf['name']}] Image data is empty. Skipping save and notification.")
        return

    # NASへ画像を保存
    filename = f"{cam_conf['id']}_{trigger_type}_{dt_class.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    filepath = os.path.join(ASSETS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(image_data)
    logger.info(f"💾 [{cam_conf['name']}] Image successfully saved to NAS: {filepath}")
    
    # 恒久対策: Discordへローカルの画像ファイルを直接アップロード（multipart/form-data）
    webhook_url = config.DISCORD_WEBHOOK_NOTIFY or config.DISCORD_WEBHOOK_URL
    if webhook_url:
        try:
            logger.info(f"📤 [{cam_conf['name']}] Uploading image directly to Discord...")
            with open(filepath, "rb") as img_file:
                files = {"file": (filename, img_file, "image/jpeg")}
                payload = {"content": f"🚨 **{cam_conf['name']}**で動体を検知しました！"}
                res = requests.post(webhook_url, data=payload, files=files, timeout=10)
                
                if res.status_code in [200, 204]:
                    logger.info(f"✅ [{cam_conf['name']}] Discord notification sent successfully.")
                else:
                    logger.error(f"❌ Discord API Error: {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"🚨 Failed to send image to Discord: {e}")
    else:
        logger.warning("⚠️ Discord Webhook URL is not configured.")

def close_camera_session(camera_instance: Any):
    """ONVIFカメラの内部セッションを強制的に閉じる"""
    try:
        if camera_instance:
            # zeepのtransport内にあるsessionを閉じる
            if hasattr(camera_instance, 'devicemgmt'):
                 camera_instance.devicemgmt.transport.session.close()
            elif hasattr(camera_instance, 'transport'):
                 camera_instance.transport.session.close()
    except Exception as e:
        logger.debug(f"Session close warning: {e}")

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
        if not is_motion and not ('RuleEngine/CellMotionDetector/Motion' in topic_str and str(debug_val).lower() in ['true', '1']):
            # 動体検知ではない場合、ここで処理を終了（finallyへ飛ぶ）
            return

        # 4. クールダウン（Debounce）処理の追加
        current_time: float = time.time()
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
        save_image_from_stream(cam_conf, "motion")
        
    except Exception as e:
        logger.warning(f"⚠️ [{cam_name}] Event Parse Error: {e} | Trace: {traceback.format_exc().splitlines()[-1]}")
    finally:
        # ✅ いかなる場合（早期リターン・例外発生）でも確実にリソースを解放する
        del msg
        logger.debug(f"🧹 [{cam_name}] Event processing completed / Local resources released.")


def monitor_single_camera(cam_conf: Dict[str, Any]) -> None:
    """
    単一のカメラに対してONVIF接続を行い、イベントストリームを監視するプロセス。
    接続断時のリトライロジックおよびイベントパースの安全性を含む。
    """
    cam_name: str = cam_conf['name']
    ip_address: str = cam_conf['ip']
    consecutive_errors: int = 0
    port_candidates: List[int] = [2020, 80]
    max_backoff_time: int = 3600  # 最大1時間の待機 (サスペンド)

    transient_error_count: int = 0
    last_transient_error_time: float = 0
    is_first_connect: bool = True

    if cam_conf.get('port'):
        if cam_conf['port'] in port_candidates:
            port_candidates.remove(cam_conf['port'])
        port_candidates.insert(0, cam_conf['port'])

    logger.info(f"🚀 [{cam_name}] Monitor thread started.")

    while True:
        # 1. L3到達性の事前チェック (ホストダウン時の即時サスペンド)
        if not is_host_reachable(ip_address):
            consecutive_errors += 1
            backoff_time: int = min(10 * (2 ** consecutive_errors), max_backoff_time)
            logger.warning(
                f"⚠️ [{cam_name}] 接続失敗 (No route to host). "
                f"{consecutive_errors}回目の失敗。{backoff_time}秒間監視をサスペンドします。"
            )
            time.sleep(backoff_time)
            continue

        mycam: Any = None
        current_pullpoint: Any = None
        events_service: Any = None

        try:
            wsdl_path: Optional[str] = find_wsdl_path()
            if not wsdl_path: raise FileNotFoundError("WSDL path could not be determined.")

            target_port: int = port_candidates[0]
            
            # 2. カメラ接続 (ONVIFCamera)
            mycam = ONVIFCamera(
                ip_address, 
                target_port, 
                cam_conf['user'], 
                cam_conf['pass'],
                wsdl_dir=wsdl_path,
                encrypt=True
            )

            devicemgmt: Any = mycam.create_devicemgmt_service()
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
            events_service = mycam.create_events_service()
            events_service.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
            
            logger.debug(f"[{cam_name}] Creating subscription with TopicFilter...")
            current_pullpoint = events_service.CreatePullPointSubscription()
            
            try:
                plp_address: str = current_pullpoint.SubscriptionReference.Address._value_1
            except AttributeError:
                plp_address: str = current_pullpoint.SubscriptionReference.Address

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

            active_pullpoints.append(pullpoint)
            current_pullpoint = pullpoint
            
            # 接続成功時にエラーカウントをリセット
            consecutive_errors = 0
            session_start_time: float = time.time()

            # 4. 監視ループ
            while True:
                if time.time() - session_start_time > SESSION_LIFETIME:
                    logger.debug(f"🔄 [{cam_name}] Refreshing session...")
                    break

                try:
                    events: Any = pullpoint.PullMessages({'Timeout': timedelta(seconds=2), 'MessageLimit': 100})
                    # ... (ログ出力等は省略せず元の通り) ...
                    if events:
                        logger.debug(f"🔬 [RAW EVENTS] {cam_name}: Type={type(events)}, Attrs={dir(events)}")
                        logger.debug(f"📦 [EVENT PAYLOAD] {cam_name}: {events.NotificationMessage}")
                except Exception as e:
                    logger.debug(f"[{cam_name}] Failed to pull messages: {e}")
                    events = None

                time.sleep(0.5)

                if events and hasattr(events, 'NotificationMessage'):
                    for msg in events.NotificationMessage:
                        process_camera_event(msg, cam_conf)

        except (RemoteDisconnected, ProtocolError, BrokenPipeError, ConnectionResetError) as e:
            # 【修正点】一時的障害に対するExponential Backoffの適用とサスペンドログ
            consecutive_errors += 1
            now: float = time.time()
            if now - last_transient_error_time < 15:
                transient_error_count += 1
            else:
                transient_error_count = 1
            
            last_transient_error_time = now

            wait_time: int = min(10 * (2 ** consecutive_errors), max_backoff_time)

            if transient_error_count >= 3:
                logger.warning(
                    f"⚠️ [{cam_name}] 接続失敗 (Transient Network Error: {e}). "
                    f"{consecutive_errors}回目の失敗。{wait_time}秒間監視をサスペンドします。"
                )
            else:
                logger.debug(f"🔄 [{cam_name}] Connection lost (Intentional/Transient): {e}. Reconnecting in {wait_time}s...")
            
            time.sleep(wait_time)
            continue

        except Exception as e:
            # 【修正点】致命的障害時のバックオフと無意味なポート切り替えの抑止
            consecutive_errors += 1
            err_msg: str = str(e)

            detailed_info: str = ""
            if hasattr(e, 'detail'):
                detailed_info += f" | Detail: {e.detail}"
            if hasattr(e, 'content'):
                detailed_info += f" | Content: {str(e.content)[:200]}"
            
            full_err_msg: str = f"{err_msg}{detailed_info}"

            wait_time_fatal: int = min(10 * (2 ** consecutive_errors), max_backoff_time)
            
            if consecutive_errors >= 5:
                logger.error(f"❌ [{cam_name}] Persistent Error ({consecutive_errors} times): {full_err_msg}")
                if consecutive_errors == 5 or consecutive_errors % 12 == 0:
                    try:
                        alert_msg: str = f"🚨 **カメラ監視アラート**\n[{cam_name}] の接続障害が継続しています（連続{consecutive_errors}回失敗）。\n詳細: {err_msg}"
                        send_push(
                            config.LINE_USER_ID or "", 
                            [{"type": "text", "text": alert_msg}], 
                            target="discord"
                        )
                        logger.info(f"📤 [{cam_name}] 管理者へ障害通知を送信しました。")
                    except Exception as push_err:
                        logger.error(f"🚨 通知送信に失敗しました: {push_err}")
                
                if "Unknown error" in err_msg or "Unauthorized" in err_msg:
                    logger.error(f"💡 Hint: Check PASSWORD and CAMERA TIME settings.")
            
            if current_pullpoint in active_pullpoints: 
                active_pullpoints.remove(current_pullpoint)
            
            # ホストが生きている場合のみ緊急診断とポートローテーションを実行
            if is_host_reachable(ip_address):
                perform_emergency_diagnosis(ip_address)
                
                if consecutive_errors >= 3:
                    old_port: int = port_candidates[0]
                    port_candidates.append(port_candidates.pop(0))
                    new_port: int = port_candidates[0]
                    logger.warning(f"🔄 [{cam_name}] Switching port from {old_port} to {new_port}")
            else:
                logger.warning(f"⚠️ [{cam_name}] Host is unreachable. Skipping port rotation.")
                
            logger.warning(
                f"⚠️ [{cam_name}] 接続失敗 (Connection/ONVIF Error). "
                f"{consecutive_errors}回目の失敗。{wait_time_fatal}秒間監視をサスペンドします。"
            )
            time.sleep(wait_time_fatal)

        finally:
            # 【修正2】リソース解放処理の明示的な記録
            logger.debug(f"🧹 [{cam_name}] Starting resource cleanup...")
            if current_pullpoint:
                if current_pullpoint in active_pullpoints:
                    active_pullpoints.remove(current_pullpoint)
                try:
                    current_pullpoint.Unsubscribe()
                    logger.debug(f"🗑️ [{cam_name}] Unsubscribed from PullPoint successfully.")
                except Exception as e:
                    logger.debug(f"⚠️ [{cam_name}] PullPoint Unsubscribe skipped or failed: {e}")
                
                force_close_session(current_pullpoint)

            if events_service:
                force_close_session(events_service)
                logger.debug(f"🔌 [{cam_name}] Events service session closed.")

            if mycam:
                force_close_session(mycam)
                logger.debug(f"🔌 [{cam_name}] Camera devicemgmt session closed.")
            
            logger.debug(f"✨ [{cam_name}] Resource cleanup completed.")
            time.sleep(1)

async def main() -> None:
    if not WSDL_DIR: return logger.error("WSDL not found")
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=len(config.CAMERAS)) as executor:
        await asyncio.gather(*[loop.run_in_executor(executor, monitor_single_camera, cam) for cam in config.CAMERAS])

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass