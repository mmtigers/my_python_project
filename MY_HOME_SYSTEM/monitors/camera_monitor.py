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
# logging.getLogger("zeep").setLevel(logging.DEBUG)
# logging.getLogger("urllib3").setLevel(logging.DEBUG)

ASSETS_DIR: str = os.path.join(config.ASSETS_DIR, "snapshots")
os.makedirs(ASSETS_DIR, exist_ok=True)

BINDING_NAME: str = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'
PRIORITY_MAP: Dict[str, int] = {"intrusion": 100, "person": 80, "vehicle": 50, "motion": 10}
# カメラの強制切断(約60秒)より前に再接続するための寿命設定
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
    """
    個別のカメラ監視ロジック (Fix: 予防的再接続版)。
    ONVIFのイベントストリームを購読し、動き検知時に画像保存と通知を行う。
    """
    cam_name: str = cam_conf['name']
    consecutive_errors: int = 0
    
    # ポートの候補: 設定値 -> 2020(ONVIF拡張) -> 80(標準)
    port_candidates: List[int] = list(dict.fromkeys([cam_conf.get('port', 80), 2020, 80]))

    logger.info(f"🚀 [{cam_name}] Monitor thread started.")

    while True:
        mycam = None
        current_pullpoint = None
        
        try:
            # -------------------------------------------------------
            # 1. 接続フェーズ
            # -------------------------------------------------------
            # WSDLパスの特定
            wsdl_path = find_wsdl_path()
            if not wsdl_path:
                raise FileNotFoundError("WSDL path could not be determined.")

            # カメラ接続試行 (ポート候補をローテーション)
            target_port = port_candidates[0] # 先頭のポートを試す
            
            mycam = ONVIFCamera(
                cam_conf['ip'], 
                target_port, 
                cam_conf['user'], 
                cam_conf['pass'],
                wsdl_dir=wsdl_path
            )

            # サービス作成
            await_params = {'timeout': 5} # 接続タイムアウト
            devicemgmt = mycam.create_devicemgmt_service()
            device_info = devicemgmt.GetDeviceInformation()
            
            # イベントサービスの作成と購読
            events_service = mycam.create_events_service()
            pullpoint = events_service.CreatePullPointSubscription()
            
            # 成功したらリストに追加
            active_pullpoints.append(pullpoint)
            current_pullpoint = pullpoint
            
            # ポートの優先順位を更新（成功したポートを次回も優先）
            if port_candidates[0] != target_port:
                port_candidates.remove(target_port)
                port_candidates.insert(0, target_port)

            logger.info(f"✅ [{cam_name}] Subscribed (Port: {target_port}, Model: {device_info.Model})")

            # エラーカウンタリセット
            consecutive_errors = 0
            
            # セッション開始時刻を記録 (予防的再接続用)
            session_start_time = time.time()

            # -------------------------------------------------------
            # 2. 監視ループ (Session Scope)
            # -------------------------------------------------------
            while True:
                # [A] 寿命チェック (Proactive Refresh)
                # カメラに切断される(60s)前に、自分から行儀よく再接続へ移行する
                if time.time() - session_start_time > SESSION_LIFETIME:
                    logger.info(f"🔄 [{cam_name}] Session limit reached ({SESSION_LIFETIME}s). Refreshing...")
                    try:
                        pullpoint.Unsubscribe()
                    except Exception:
                        pass # 失敗しても気にしない
                    break # 内側のループを抜ける -> 外側のループで即座に再接続

                # [B] イベント取得 (PullMessages)
                try:
                    # タイムアウトを短く設定し、制御を細かく戻す
                    # (タイムアウトしてもエラーではなく「イベントなし」として扱う)
                    events = pullpoint.PullMessages({'Timeout': timedelta(seconds=2), 'MessageLimit': 100})
                except Exception as e:
                    # タイムアウトや一時的な通信遅延は無視してループ継続
                    # ただし、致命的な切断エラーはここで検知されることもある
                    events = None

                # [C] 負荷軽減 (重要)
                time.sleep(0.5)

                # [D] イベント解析
                if events and hasattr(events, 'NotificationMessage'):
                    for msg in events.NotificationMessage:
                        if not msg.Topic: continue
                        
                        topic_str = str(msg.Topic)
                        # MotionAlarm (動き検知)
                        if 'RuleEngine/CellMotionDetector/Motion' in topic_str:
                            is_motion = msg.Data.SimpleItem[0].Value
                            if is_motion == 'true':
                                logger.info(f"🏃 [{cam_name}] Motion Detected!")
                                save_log_generic("camera", f"[{cam_name}] Motion detected", "INFO")
                                # 画像保存とLINE通知
                                save_image_from_stream(cam_conf, "motion")
                        
                        # DigitalInput (人感センサー等)
                        elif 'DigitalInput' in topic_str:
                            is_active = msg.Data.SimpleItem[0].Value
                            if is_active == 'true':
                                logger.info(f"DETECT: [{cam_name}] Sensor Active")

        # -------------------------------------------------------
        # 3. エラーハンドリング
        # -------------------------------------------------------
        except (RemoteDisconnected, ProtocolError, BrokenPipeError, ConnectionResetError) as e:
            # ネットワーク切断 (予期せぬタイミングでの切断)
            logger.warning(f"⚠️ [{cam_name}] Connection lost unexpectedly: {e}")
            if current_pullpoint in active_pullpoints: 
                active_pullpoints.remove(current_pullpoint)
            
            # 少し待機してから再接続
            time.sleep(2)
            continue 

        except Exception as e:
            # その他の致命的なエラー (認証失敗、WSDL不在、IP到達不能など)
            logger.error(f"❌ [{cam_name}] Error: {e}")
            if current_pullpoint in active_pullpoints: 
                active_pullpoints.remove(current_pullpoint)
            
            # 診断実行
            perform_emergency_diagnosis(cam_conf['ip'])
            
            # 指数バックオフ (最大300秒)
            wait = min(300, 30 * (2 ** consecutive_errors))
            consecutive_errors += 1
            if consecutive_errors > 5:
                # あまりに失敗する場合はポート候補をローテーションしてみる
                port_candidates.append(port_candidates.pop(0))
                
            logger.info(f"Waiting {wait}s before retry...")
            time.sleep(wait)

async def main() -> None:
    if not WSDL_DIR: return logger.error("WSDL not found")
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=len(config.CAMERAS)) as executor:
        await asyncio.gather(*[loop.run_in_executor(executor, monitor_single_camera, cam) for cam in config.CAMERAS])

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass