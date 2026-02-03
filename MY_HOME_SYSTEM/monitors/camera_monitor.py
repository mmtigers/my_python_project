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
import glob
import requests
import datetime
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

ASSETS_DIR: str = os.path.join(config.ASSETS_DIR, "snapshots")
os.makedirs(ASSETS_DIR, exist_ok=True)

BINDING_NAME: str = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'
PRIORITY_MAP: Dict[str, int] = {"intrusion": 100, "person": 80, "vehicle": 50, "motion": 10}
SESSION_LIFETIME: int = 50  
RENEW_DURATION: str = "PT600S"

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
        logger.warning(f"⚠️ [{cam_name}] Failed to check camera time: {e}")
        return True

def capture_snapshot_from_nvr(cam_conf: Dict[str, Any], target_time: Optional[datetime.datetime] = None) -> Optional[bytes]:
    """NASの録画データから指定時刻の画像を切り出す。"""
    if target_time is None: target_time = dt_class.now()
    sub_dir = "parking" if "Parking" in cam_conf['id'] else "garden" if "Garden" in cam_conf['id'] else None
    if not sub_dir: return None

    record_dir: str = os.path.join(config.NVR_RECORD_DIR, sub_dir)
    try:
        files = sorted(glob.glob(os.path.join(record_dir, "*.mp4")))
        if not files: return None

        target_file = files[-1]
        for f_path in reversed(files):
            try:
                f_dt = dt_class.strptime(os.path.basename(f_path).split('.')[0], "%Y%m%d_%H%M%S")
                if f_dt <= target_time:
                    target_file = f_path
                    break
            except ValueError: continue
        
        f_start_dt = dt_class.strptime(os.path.basename(target_file).split('.')[0], "%Y%m%d_%H%M%S")
        seek_sec = max(0.0, (target_time - f_start_dt).total_seconds())
        
        tmp_path = f"/tmp/snapshot_{cam_conf['id']}.jpg"
        cmd = ["ffmpeg", "-y", "-ss", str(seek_sec), "-i", target_file, "-frames:v", "1", "-q:v", "2", tmp_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
        
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            with open(tmp_path, "rb") as f: return f.read()
    except Exception:
        pass
    return None

def save_image_from_stream(cam_conf: Dict[str, Any], trigger_type: str) -> None:
    image_data = capture_snapshot_from_nvr(cam_conf)
    if not image_data: return

    filename = f"{cam_conf['id']}_{trigger_type}_{dt_class.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    filepath = os.path.join(ASSETS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(image_data)
    
    img_url = f"{config.FRONTEND_URL}/assets/snapshots/{filename}"
    send_push(config.LINE_USER_ID, [{"type":"image", "originalContentUrl": img_url, "previewImageUrl": img_url}], target="line")

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

def monitor_single_camera(cam_conf: Dict[str, Any]) -> None:
    cam_name: str = cam_conf['name']
    consecutive_errors: int = 0
    port_candidates: List[int] = [2020, 80]

    is_first_connect: bool = True
    
    if cam_conf.get('port'):
        if cam_conf['port'] in port_candidates:
            port_candidates.remove(cam_conf['port'])
        port_candidates.insert(0, cam_conf['port'])

    logger.info(f"🚀 [{cam_name}] Monitor thread started.")

    while True:
        mycam = None
        current_pullpoint = None
        events_service = None # 初期化漏れ防止
        
        try:
            wsdl_path = find_wsdl_path()
            if not wsdl_path: raise FileNotFoundError("WSDL path could not be determined.")

            target_port = port_candidates[0]
            
            # 1. カメラ接続 (ONVIFCamera)
            # ★Fix: encrypt=True (デフォルト) を使用してWSSEヘッダーを有効化
            # collect_onvif_logs.py と同じ設定にする
            mycam = ONVIFCamera(
                cam_conf['ip'], 
                target_port, 
                cam_conf['user'], 
                cam_conf['pass'],
                wsdl_dir=wsdl_path,
                encrypt=True # 明示的にTrue (WSSE有効)
            )

            # 2. devicemgmtサービス作成 & 認証設定
            devicemgmt = mycam.create_devicemgmt_service()
            # ★Fix: Digest認証も追加 (WSSE + Digest の最強構成)
            devicemgmt.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
            
            check_camera_time(devicemgmt, cam_name)
            
            device_info = devicemgmt.GetDeviceInformation()
            if is_first_connect:
                logger.info(f"📡 [{cam_name}] Connected. Model: {device_info.Model}")
            else:
                logger.debug(f"📡 [{cam_name}] Connected. Model: {device_info.Model} (Reconnected)")
            # 3. イベント購読
            events_service = mycam.create_events_service()
            events_service.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
            subscription = events_service.CreatePullPointSubscription()
            
            try:
                plp_address = subscription.SubscriptionReference.Address._value_1
            except AttributeError:
                plp_address = subscription.SubscriptionReference.Address

            events_wsdl = os.path.join(wsdl_path, 'events.wsdl')
            pullpoint = ONVIFService(
                xaddr=plp_address,
                user=cam_conf['user'],
                passwd=cam_conf['pass'],
                url=events_wsdl,
                encrypt=True, # ★Fix: PullPointもWSSE有効化
                binding_name=BINDING_NAME
            )
            
            # ★Fix: PullPointにDigest認証追加
            pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])

            active_pullpoints.append(pullpoint)
            current_pullpoint = pullpoint
            
            if is_first_connect:
                logger.info(f"✅ [{cam_name}] Subscribed successfully.")
                is_first_connect = False # フラグを折る
            else:
                logger.debug(f"✅ [{cam_name}] Subscribed successfully (Refresh).")
            
            consecutive_errors = 0
            session_start_time = time.time()

            # 4. 監視ループ
            while True:
                if time.time() - session_start_time > SESSION_LIFETIME:
                    logger.debug(f"🔄 [{cam_name}] Refreshing session...")
                    try:
                        if hasattr(subscription, 'Unsubscribe'):
                            subscription.Unsubscribe()
                    except Exception: pass
                    break

                try:
                    events = pullpoint.PullMessages({'Timeout': timedelta(seconds=2), 'MessageLimit': 100})
                except Exception:
                    events = None

                time.sleep(0.5)

                if events and hasattr(events, 'NotificationMessage'):
                    for msg in events.NotificationMessage:
                        if not msg.Topic: continue
                        topic_str = str(msg.Topic)
                        
                        if 'RuleEngine/CellMotionDetector/Motion' in topic_str:
                            try:
                                is_motion = msg.Data.SimpleItem[0].Value
                                if is_motion == 'true':
                                    logger.info(f"🏃 [{cam_name}] Motion Detected!")
                                    save_log_generic("camera", f"[{cam_name}] Motion detected", "INFO")
                                    save_image_from_stream(cam_conf, "motion")
                            except Exception: pass

                        elif 'DigitalInput' in topic_str:
                            try:
                                is_active = msg.Data.SimpleItem[0].Value
                                if is_active == 'true':
                                    logger.info(f"DETECT: [{cam_name}] Sensor Active")
                            except Exception: pass

        except (RemoteDisconnected, ProtocolError, BrokenPipeError, ConnectionResetError) as e:
            # 既知の切断エラーは即座にリトライ（警告ログのみ）
            logger.warning(f"⚠️ [{cam_name}] Connection lost (Transient): {e}")
            time.sleep(2)
            continue 

        except Exception as e:
            # カウンタをインクリメント
            consecutive_errors += 1
            err_msg = str(e)
            logger.error(f"❌ [{cam_name}] Error: {err_msg}")

            # エラー発生後は、復帰したことがわかるように次回接続時にINFOを出すようにする
            is_first_connect = True
            
            # 設計書 9.8 準拠: 3回未満は WARNING、3回以上で ERROR
            if consecutive_errors < 3:
                logger.warning(f"⚠️ [{cam_name}] Connect Failed ({consecutive_errors}/3). Retrying... Reason: {err_msg}")
            else:
                logger.error(f"❌ [{cam_name}] Persistent Error: {err_msg}")
                if "Unknown error" in err_msg or "Unauthorized" in err_msg:
                    logger.error(f"💡 Hint: Check PASSWORD and CAMERA TIME settings.")
            
            if current_pullpoint in active_pullpoints: 
                active_pullpoints.remove(current_pullpoint)
            
            # 診断はWARNINGレベルでも実施してログに残す（トラブルシューティング用）
            perform_emergency_diagnosis(cam_conf['ip'])
            
            # 待機時間の計算 (指数バックオフ)
            wait = min(300, 30 * (2 ** (consecutive_errors - 1))) # 初回は30秒
            
            # 3回失敗したらポートを切り替える (ローテーション)
            if consecutive_errors >= 3:
                old_port = port_candidates[0]
                port_candidates.append(port_candidates.pop(0))
                new_port = port_candidates[0]
                logger.warning(f"🔄 [{cam_name}] Switching port from {old_port} to {new_port}")
                # ポート変更後はカウンタをリセットせず、次の試行で即座に判定させるか、
                # あるいは「新しいポートでの試行」としてカウント継続するか。
                # ここでは「継続」させ、ダメならまたERROR通知が出るようにします。
                
            logger.info(f"[{cam_name}] Retry in {wait}s...")
            time.sleep(wait)

        finally:
            # ▼▼▼ 修正2: 徹底的なリソース解放 ▼▼▼
            
            # 1. PullPointのクリーンアップ
            if current_pullpoint:
                if current_pullpoint in active_pullpoints:
                    active_pullpoints.remove(current_pullpoint)
                try:
                    current_pullpoint.Unsubscribe()
                except Exception:
                    pass
                # セッション物理切断
                force_close_session(current_pullpoint)

            # 2. Events Serviceのクリーンアップ
            if events_service:
                force_close_session(events_service)

            # 3. Main Camera (DeviceMgmt) のクリーンアップ
            if mycam:
                force_close_session(mycam)
            
            # ▲▲▲ ▲▲▲
            
            # 再接続までの安全マージン
            time.sleep(1)

async def main() -> None:
    if not WSDL_DIR: return logger.error("WSDL not found")
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=len(config.CAMERAS)) as executor:
        await asyncio.gather(*[loop.run_in_executor(executor, monitor_single_camera, cam) for cam in config.CAMERAS])

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass