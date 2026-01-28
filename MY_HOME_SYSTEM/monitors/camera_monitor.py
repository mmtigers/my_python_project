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
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List
from concurrent.futures import ThreadPoolExecutor
from http.client import RemoteDisconnected
from urllib3.exceptions import ProtocolError
from requests.auth import HTTPDigestAuth

# ONVIF関連ライブラリ
try:
    from onvif import ONVIFCamera
    from onvif.client import ONVIFService
    from lxml import etree
except ImportError:
    ONVIFCamera = Any
    ONVIFService = Any
    etree = Any

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso
from services.notification_service import send_push

# === ログ・定数設定 ===
logger = setup_logging("camera")
logging.getLogger("zeep").setLevel(logging.ERROR) 

ASSETS_DIR: str = os.path.join(config.ASSETS_DIR, "snapshots")
os.makedirs(ASSETS_DIR, exist_ok=True)

BINDING_NAME: str = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'
PRIORITY_MAP: Dict[str, int] = {"intrusion": 100, "person": 80, "vehicle": 50, "motion": 10}
RENEW_INTERVAL: int = 60      
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
    """WSDLファイルのディレクトリを動的に探索する（パス構造の変化に対応）。"""
    for path in sys.path:
        if not os.path.exists(path):
            continue
            
        # 候補1: 標準的な構造 (onvif/wsdl)
        candidate_standard = os.path.join(path, 'onvif', 'wsdl')
        # 候補2: 今回見つかった構造 (site-packages直下のwsdl)
        candidate_direct = os.path.join(path, 'wsdl')

        for candidate in [candidate_standard, candidate_direct]:
            if os.path.exists(os.path.join(candidate, 'devicemgmt.wsdl')):
                logger.info(f"✅ WSDL found at: {candidate}")
                return candidate
                
    return None

WSDL_DIR: Optional[str] = find_wsdl_path()

def perform_emergency_diagnosis(ip: str) -> Dict[int, bool]:
    """接続障害時にポートの状態を診断する。"""
    results: Dict[int, bool] = {}
    msg = f"🚑 [Diagnosis] Checking {ip}:\n"
    for port in [80, 2020]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        res = sock.connect_ex((ip, port))
        results[port] = (res == 0)
        status = "OPEN" if res == 0 else f"CLOSED({res})"
        msg += f"   - Port {port}: {status}\n"
        sock.close()
    logger.warning(msg)
    return results

def analyze_event_type(xml_str: str) -> Tuple[Optional[str], Optional[str], int, Optional[str]]:
    """XMLメッセージを解析し、検知タイプを分類する。"""
    if 'Value="true"' not in xml_str and 'State="true"' not in xml_str:
        return None, None, 0, None

    rule_name: str = "Unknown"
    if 'Rule="' in xml_str:
        try:
            start = xml_str.find('Rule="') + 6
            end = xml_str.find('"', start)
            rule_name = xml_str[start:end]
        except Exception: pass

    # 判定ロジックの集約
    if any(k in xml_str or k in rule_name for k in ['Intrusion', 'LineCross']):
        return "intrusion", "敷地への侵入", PRIORITY_MAP["intrusion"], rule_name
    if any(k in xml_str or k in rule_name for k in ['People', 'Person']):
        return "person", "人", PRIORITY_MAP["person"], rule_name
    if any(k in xml_str or k in rule_name for k in ['Vehicle', 'Car']):
        return "vehicle", "車", PRIORITY_MAP["vehicle"], rule_name
    if 'Motion' in xml_str or 'Motion' in rule_name:
        return "motion", "動き", PRIORITY_MAP["motion"], rule_name

    return None, None, 0, None

def capture_snapshot_from_nvr(cam_conf: Dict[str, Any], target_time: Optional[datetime] = None) -> Optional[bytes]:
    """NASの録画データから指定時刻の画像を切り出す。"""
    start_ts = time.time()
    if target_time is None: target_time = datetime.now()
    sub_dir = "parking" if "Parking" in cam_conf['id'] else "garden" if "Garden" in cam_conf['id'] else None
    if not sub_dir: return None

    record_dir: str = os.path.join(config.NVR_RECORD_DIR, sub_dir)
    try:
        files = sorted(glob.glob(os.path.join(record_dir, "*.mp4")))
        if not files: return None

        target_file: Optional[str] = None
        for f_path in reversed(files):
            try:
                f_dt = datetime.strptime(os.path.basename(f_path).split('.')[0], "%Y%m%d_%H%M%S")
                if f_dt <= target_time:
                    target_file = f_path
                    break
            except ValueError: continue
        
        if not target_file: target_file = files[-1]
        
        # パフォーマンス・ラグ計測
        f_start_dt = datetime.strptime(os.path.basename(target_file).split('.')[0], "%Y%m%d_%H%M%S")
        seek_sec = max(0.0, (target_time - f_start_dt).total_seconds())
        logger.info(f"🔍 [NVR] File: {os.path.basename(target_file)}, Seek: {seek_sec:.1f}s")

        tmp_path = f"/tmp/snapshot_{cam_conf['id']}.jpg"
        cmd = ["ffmpeg", "-y", "-ss", str(seek_sec), "-i", target_file, "-frames:v", "1", "-q:v", "2", tmp_path]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
        
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            logger.info(f"✅ [Perf] NVR extraction success: {time.time() - start_ts:.2f}s")
            with open(tmp_path, "rb") as f: return f.read()
        else:
            logger.warning(f"⚠️ [NVR] FFmpeg error: {proc.stderr.decode()[-200:]}")
    except Exception as e:
        logger.error(f"❌ [NVR] Exception: {e}")
    return None

def monitor_single_camera(cam_conf: Dict[str, Any]) -> None:
    """個別のカメラ監視ロジック。"""
    cam_name: str = cam_conf['name']
    consecutive_errors: int = 0
    last_disconnect_time: float = 0.0
    disconnect_count_short_term: int = 0
    last_success_port: Optional[int] = None
    port_candidates: List[int] = list(dict.fromkeys([cam_conf.get('port', 80), 2020, 80]))

    while True:
        mycam = None
        current_pullpoint = None
        renew_supported: bool = "Parking" not in cam_conf.get('id', '')
        
        try:
            # ポート試行ループ
            ports = [last_success_port] + [p for p in port_candidates if p != last_success_port] if last_success_port else port_candidates
            for port in ports:
                try:
                    socket.setdefaulttimeout(10.0)
                    mycam = ONVIFCamera(cam_conf['ip'], port, cam_conf['user'], cam_conf['pass'], wsdl_dir=WSDL_DIR)
                    mycam.create_events_service()
                    last_success_port = port
                    break
                except Exception:
                    mycam = None
            
            if not mycam: raise ConnectionError("All ports failed")

            # 監視セットアップ
            svc = mycam.create_events_service()
            subscription = svc.CreatePullPointSubscription()
            plp_addr = getattr(subscription.SubscriptionReference.Address, '_value_1', subscription.SubscriptionReference.Address)
            pullpoint = ONVIFService(plp_addr, cam_conf['user'], cam_conf['pass'], os.path.join(WSDL_DIR, 'events.wsdl'), binding_name=BINDING_NAME)
            pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
            
            active_pullpoints.append(pullpoint)
            current_pullpoint = pullpoint
            logger.info(f"✅ [{cam_name}] Subscribed (Port: {last_success_port})")

            success_pull_count: int = 0
            last_renew_time: float = time.time()
            
            while True:
                # Renew
                if renew_supported and (time.time() - last_renew_time > RENEW_INTERVAL):
                    try:
                        pullpoint.Renew(RENEW_DURATION)
                        last_renew_time = time.time()
                    except Exception: renew_supported = False

                # イベント取得
                events = pullpoint.PullMessages({'Timeout': timedelta(seconds=5), 'MessageLimit': 100})
                success_pull_count += 1
                if success_pull_count >= 5:
                    consecutive_errors = 0
                    disconnect_count_short_term = 0
                
                if hasattr(events, 'NotificationMessage'):
                    for event in events.NotificationMessage:
                        if not hasattr(event, 'Message'): continue
                        xml_str = etree.tostring(event.Message, encoding='unicode') if hasattr(event.Message, 'tag') else str(event.Message)
                        ev_type, label, priority, rule = analyze_event_type(xml_str)
                        
                        if ev_type:
                            logger.info(f"🔥 [{cam_name}] Detect: {label}")
                            img = capture_snapshot_from_nvr(cam_conf)
                            if img:
                                path = os.path.join(ASSETS_DIR, f"snapshot_{cam_conf['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
                                with open(path, "wb") as f: f.write(img)
                            
                            save_log_generic(config.SQLITE_TABLE_SENSOR, ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                             (get_now_iso(), "防犯カメラ", cam_conf['id'], "Camera", ev_type))
                            
                            # 車両判定
                            if ev_type == "vehicle" or "Vehicle" in str(rule):
                                action = "LEAVE" if any(k in rule for k in config.CAR_RULE_KEYWORDS["LEAVE"]) else "RETURN" if any(k in rule for k in config.CAR_RULE_KEYWORDS["RETURN"]) else "UNKNOWN"
                                if action != "UNKNOWN":
                                    save_log_generic(config.SQLITE_TABLE_CAR, ["timestamp", "action", "rule_name"], (get_now_iso(), action, rule))

                            if ev_type == "intrusion":
                                send_push(config.LINE_USER_ID, [{"type": "text", "text": f"🚨【警告】侵入検知: {cam_name}"}], image_data=img, target="discord")
                                time.sleep(15)

        except (RemoteDisconnected, ProtocolError, BrokenPipeError, ConnectionResetError):
            # 瞬断・Flapping対策
            now = time.time()
            disconnect_count_short_term = disconnect_count_short_term + 1 if now - last_disconnect_time < 60 else 1
            last_disconnect_time = now
            if disconnect_count_short_term > 3: 
                logger.warning(f"⚠️ [{cam_name}] Flapping detected. Cooling down...")
                time.sleep(10)
            break 
        except Exception as e:
            if current_pullpoint in active_pullpoints: active_pullpoints.remove(current_pullpoint)
            perform_emergency_diagnosis(cam_conf['ip'])
            consecutive_errors += 1
            wait = min(30 * (2 ** (min(consecutive_errors, 6) - 1)), 600)
            logger.error(f"❌ [{cam_name}] Error: {e}. Retrying in {wait}s...")
            time.sleep(wait)

async def main() -> None:
    if not WSDL_DIR: return logger.error("WSDL not found")
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=len(config.CAMERAS)) as executor:
        await asyncio.gather(*[loop.run_in_executor(executor, monitor_single_camera, cam) for cam in config.CAMERAS])

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass