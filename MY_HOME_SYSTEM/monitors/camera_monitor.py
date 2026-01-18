# MY_HOME_SYSTEM/monitors/camera_monitor.py
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import glob
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso
from services.notification_service import send_push

import asyncio
from datetime import datetime, timedelta

import time
import socket
import zeep.helpers
from lxml import etree
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
import traceback
import signal
import requests
from onvif import ONVIFCamera
from onvif.client import ONVIFService
from requests.auth import HTTPDigestAuth


# === ログ設定 ===
logger = setup_logging("camera")
# 調査のためZeep(通信ライブラリ)のログも少し出す
logging.getLogger("zeep").setLevel(logging.ERROR) 

# プロセス終了時にUnsubscribeするために、アクティブなPullPointServiceを保持する
# (修正: subscriptionデータではなく、通信可能なserviceオブジェクトを保持します)
active_pullpoints = []

def cleanup_handler(signum, frame):
    """プロセス終了シグナルを受け取った時のクリーンアップ処理"""
    logger.info(f"🛑 終了シグナル({signum})を受信。カメラ接続のクリーンアップを開始します...")
    
    for svc in active_pullpoints:
        try:
            # ONVIFのUnsubscribeメソッドを呼び出す
            if hasattr(svc, 'Unsubscribe'):
                svc.Unsubscribe()
                logger.info("✅ Unsubscribe送信成功")
            # zeep objectの場合のフォールバック
            elif hasattr(svc, 'service') and hasattr(svc.service, 'Unsubscribe'):
                svc.service.Unsubscribe(_soapheaders=None)
                logger.info("✅ Unsubscribe送信成功 (zeep)")
        except Exception as e:
            # 既に切れている場合は無視
            pass

    logger.info("👋 監視プロセスを終了します")
    os._exit(0)

# シグナルハンドラの登録 (Ctrl+C や systemctl stop を捕捉)
signal.signal(signal.SIGINT, cleanup_handler)
signal.signal(signal.SIGTERM, cleanup_handler)

# === 画像保存設定 ===
ASSETS_DIR = os.path.join(config.ASSETS_DIR, "snapshots")
if not os.path.exists(ASSETS_DIR):
    os.makedirs(ASSETS_DIR, exist_ok=True)

# === 定数定義 ===
BINDING_NAME = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'

# 優先度定義
PRIORITY_MAP = {
    "intrusion": 100, "person": 80, "vehicle": 50, "motion": 10
}

# Renew設定
RENEW_INTERVAL = 60      # 60秒ごとに更新リクエストを送る
RENEW_DURATION = "PT600S" # 更新時に要求する有効期間 (10分)

def find_wsdl_path():
    for path in sys.path:
        if 'site-packages' in path and os.path.exists(path):
            candidate = os.path.join(path, 'onvif', 'wsdl')
            if os.path.exists(os.path.join(candidate, 'devicemgmt.wsdl')):
                return candidate
            for root, dirs, files in os.walk(path):
                if 'devicemgmt.wsdl' in files: return root
    return None

WSDL_DIR = find_wsdl_path()

def close_camera_connection(mycam):
    """Zeep/Requestsのセッションを明示的に閉じてカメラの接続枠を解放する"""
    if not mycam:
        return
    try:
        # 内部で保持しているサービス(devicemgmt, events, mediaなど)のセッションを閉じる
        services = [
            getattr(mycam, 'devicemgmt', None),
            getattr(mycam, 'events', None),
            getattr(mycam, 'media', None),
            getattr(mycam, 'ptz', None),
            getattr(mycam, 'imaging', None)
        ]
        
        for svc in services:
            if svc and hasattr(svc, 'zeep_client'):
                try:
                    svc.zeep_client.transport.session.close()
                except: pass
        
        # メインのtransportも閉じる
        if hasattr(mycam, 'transport') and hasattr(mycam.transport, 'session'):
             mycam.transport.session.close()

    except Exception as e:
        logger.warning(f"Session close error: {e}")

def analyze_event_type(xml_str):
    if 'Value="true"' not in xml_str and 'State="true"' not in xml_str:
        return None, None, 0, None

    rule_name = "Unknown"
    if 'Rule="' in xml_str:
        try:
            start = xml_str.find('Rule="') + 6
            end = xml_str.find('"', start)
            rule_name = xml_str[start:end]
        except: pass

    # 1. 侵入・ライン通過
    if ('Name="IsIntrusion"' in xml_str or 'Name="IsLineCross"' in xml_str or 
        "Intrusion" in rule_name or "LineCross" in rule_name or "Cross" in rule_name):
        return "intrusion", "敷地への侵入", PRIORITY_MAP["intrusion"], rule_name

    # 2. 人物検知
    if 'Name="IsPeople"' in xml_str or 'People' in rule_name or 'Person' in rule_name:
        return "person", "人", PRIORITY_MAP["person"], rule_name

    # 3. 車両検知
    if 'Name="IsVehicle"' in xml_str or 'Vehicle' in rule_name or 'Car' in rule_name:
        return "vehicle", "車", PRIORITY_MAP["vehicle"], rule_name

    # 4. 一般的な動体検知
    if 'Name="IsMotion"' in xml_str or 'Motion' in rule_name:
        return "motion", "動き", PRIORITY_MAP["motion"], rule_name

    return None, None, 0, None

def capture_snapshot_from_nvr(cam_conf, target_time=None):
    """
    NAS上の録画データから、指定時刻(デフォルトは現在)の画像を切り出す
    """
    if target_time is None:
        target_time = datetime.now()

    if "Parking" in cam_conf['id']:
        sub_dir = "parking"
    elif "Garden" in cam_conf['id']:
        sub_dir = "garden"
    else:
        logger.warning(f"[{cam_conf['name']}] NVRディレクトリが特定できません。ID: {cam_conf['id']}")
        return None

    record_dir = os.path.join(config.NVR_RECORD_DIR, sub_dir)

    try:
        files = sorted(glob.glob(os.path.join(record_dir, "*.mp4")))
        
        if not files:
            # logger.warning(f"[{cam_conf['name']}] 録画ファイルが見つかりません: {record_dir}")
            return None

        target_file = None
        
        for f_path in reversed(files):
            filename = os.path.basename(f_path)
            try:
                # ファイル名から時刻抽出 (YYYYMMDD_HHMMSS.mp4)
                time_str = filename.split('.')[0]
                file_start_dt = datetime.strptime(time_str, "%Y%m%d_%H%M%S")
                
                if file_start_dt <= target_time:
                    target_file = f_path
                    break
            except ValueError:
                continue
        
        if not target_file:
            target_file = files[-1]
            try:
                time_str = os.path.basename(target_file).split('.')[0]
                file_start_dt = datetime.strptime(time_str, "%Y%m%d_%H%M%S")
            except:
                file_start_dt = target_time

        seek_seconds = (target_time - file_start_dt).total_seconds()
        if seek_seconds < 0: seek_seconds = 0
        
        tmp_path = f"/tmp/snapshot_{cam_conf['id']}.jpg"
        
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(seek_seconds),
            "-i", target_file,
            "-frames:v", "1",
            "-q:v", "2", # 画質設定
            tmp_path
        ]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        
        if os.path.exists(tmp_path):
            with open(tmp_path, "rb") as f:
                return f.read()
        else:
            # logger.error(f"[{cam_conf['name']}] ffmpeg画像生成失敗")
            return None

    except Exception as e:
        logger.error(f"[{cam_conf['name']}] NVR画像取得エラー: {e}")
        return None

def perform_emergency_diagnosis(ip, cam_conf=None):
    """エラー発生直後にポートの状態を診断する"""
    results = {}
    target_ports = [80, 2020, 554]
    
    msg = f"🚑 [緊急診断] {ip} の接続状態チェック:\n"
    
    for port in target_ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            res = sock.connect_ex((ip, port))
            status = "OPEN (OK)" if res == 0 else f"CLOSED/FILTERED (Err: {res})"
            results[port] = (res == 0)
        except Exception as e:
            status = f"ERROR ({e})"
            results[port] = False
        finally:
            sock.close()
        msg += f"   - Port {port}: {status}\n"
    
    if results.get(80) and not results.get(2020):
        msg += "   👉 結論: Web(Port 80)は生存していますが、ONVIFサービスがダウンしています。"
        if cam_conf:
             try_soft_reboot(cam_conf['ip'], cam_conf['user'], cam_conf['pass'])
    elif not any(results.values()):
        msg += "   👉 結論: カメラとの通信が完全に途絶しています(電源断/IP変更/ケーブル抜け)。"
    
    logger.warning(msg)
    return results

def try_soft_reboot(ip, user, password):
    """Port 80が生きていれば、ONVIFまたはHTTPで再起動を試みる"""
    logger.info(f"🔄 [{ip}] Port 80経由でのソフトリブートを試行します...")
    try:
        mycam = ONVIFCamera(ip, 80, user, password, wsdl_dir=WSDL_DIR)
        mycam.devicemgmt.SystemReboot()
        logger.info(f"✅ [{ip}] ONVIF SystemReboot コマンド送信成功")
        return True
    except Exception as e:
        logger.warning(f"⚠️ ONVIF Reboot失敗: {e}")
        try:
            url = f"http://{ip}/cgi-bin/reboot.sh" 
            requests.get(url, auth=HTTPDigestAuth(user, password), timeout=5)
            logger.info(f"✅ [{ip}] HTTP CGI Reboot コマンド送信成功")
            return True
        except Exception:
            pass
    return False

def monitor_single_camera(cam_conf):
    cam_name = cam_conf['name']
    cam_base_port = cam_conf.get('port', 80)
    cam_loc = cam_conf.get('location', '伊丹')
    
    logger.info(f"🚀 [{cam_name}] 監視プロセス起動 (Target IP:{cam_conf['ip']})")

    consecutive_conn_errors = 0
    NOTIFY_THRESHOLD = 5
    has_notified_error = False
    MAX_WAIT_TIME = 600 

    port_candidates = []
    if cam_base_port not in [80, 2020]: port_candidates.append(cam_base_port)
    port_candidates.extend([2020, 80]) 
    port_candidates = list(dict.fromkeys(port_candidates)) 

    # 最後に使用して成功したポートを優先する
    last_success_port = None

    while True: 
        mycam = None
        current_pullpoint = None
        
        try:
            # --- 接続フェーズ ---
            current_port = None
            
            # 前回成功したポートがあればそれを先頭に持ってくる
            if last_success_port and last_success_port in port_candidates:
                ports_to_try = [last_success_port] + [p for p in port_candidates if p != last_success_port]
            else:
                ports_to_try = port_candidates

            for port in ports_to_try:
                try:
                    time.sleep(1.0) 
                    socket.setdefaulttimeout(10.0)

                    mycam = ONVIFCamera(cam_conf['ip'], port, cam_conf['user'], cam_conf['pass'], wsdl_dir=WSDL_DIR)
                    # 接続確認のためにサービスを作成してみる
                    mycam.create_events_service() 
                    
                    current_port = port
                    last_success_port = port # 成功ポートを記憶
                    logger.info(f"✅ [{cam_name}] 接続成功 (Port: {port})")
                    break
                except Exception as e:
                    close_camera_connection(mycam)
                    mycam = None 
                    if "401" in str(e) or "Unauthorized" in str(e):
                        logger.warning(f"⚠️ [{cam_name}] Port {port} 認証失敗")
                    continue
            
            if current_port is None:
                raise Exception(f"全ポート({port_candidates})で接続に失敗しました")

            # --- 監視セットアップ ---
            event_service = mycam.create_events_service()
            subscription = event_service.CreatePullPointSubscription()

            # 接続情報を解析
            try:
                plp_address = subscription.SubscriptionReference.Address._value_1
            except AttributeError:
                plp_address = subscription.SubscriptionReference.Address
            
            events_wsdl = os.path.join(WSDL_DIR, 'events.wsdl')
            pullpoint = ONVIFService(
                xaddr=plp_address, user=cam_conf['user'], passwd=cam_conf['pass'],
                url=events_wsdl, encrypt=True, binding_name=BINDING_NAME
            )
            pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
            
            # クリーンアップ用に保持
            active_pullpoints.append(pullpoint)
            current_pullpoint = pullpoint
            
            logger.info(f"✅ [{cam_name}] Subscription登録完了")

            # --- イベント受信ループ ---
            success_pull_count = 0 
            last_renew_time = time.time()
            
            while True:
                try:
                    # ==========================================
                    # 🔹 Renew処理 (ここが修正ポイント)
                    # ==========================================
                    now = time.time()
                    if now - last_renew_time > RENEW_INTERVAL:
                        try:
                            # 期間を延長する
                            pullpoint.Renew(RENEW_DURATION)
                            last_renew_time = now
                            logger.debug(f"🔄 [{cam_name}] Subscription Renew成功")
                        except Exception as e:
                            logger.warning(f"⚠️ [{cam_name}] Renew失敗 -> 再接続へ: {e}")
                            raise Exception("Renew Failed")

                    # ==========================================
                    # 🔹 PullMessages (イベント取得)
                    # ==========================================
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    
                    success_pull_count += 1
                    if success_pull_count >= 5 and consecutive_conn_errors > 0:
                        logger.info(f"🎉 [{cam_name}] 接続が完全に安定しました(Count Reset)")
                        consecutive_conn_errors = 0
                        has_notified_error = False
                    
                    if hasattr(events, 'NotificationMessage'):
                        for event in events.NotificationMessage:
                            message_node = getattr(event, 'Message', None)
                            if not message_node: continue

                            raw_element = getattr(message_node, '_value_1', message_node)
                            if hasattr(raw_element, 'tag'):
                                xml_str = etree.tostring(raw_element, encoding='unicode')
                            else:
                                xml_str = str(raw_element)

                            event_type, label, priority, rule_name = analyze_event_type(xml_str)
                            
                            if event_type:
                                logger.info(f"🔥 [{cam_name}] 検知: {label} (Rule: {rule_name})")
                                img = capture_snapshot_from_nvr(cam_conf)

                                if img:
                                    try:
                                        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                                        filename = f"snapshot_{cam_conf['id']}_{ts}.jpg"
                                        save_path = os.path.join(ASSETS_DIR, filename)
                                        with open(save_path, "wb") as f: f.write(img)
                                    except Exception: pass
                                
                                save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                    ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                    (get_now_iso(), "防犯カメラ", cam_conf['id'], "ONVIF Camera", event_type))
                                
                                is_car_related = "vehicle" in event_type or "Vehicle" in str(rule_name) or event_type == "intrusion"
                                if is_car_related:
                                    action = "UNKNOWN"
                                    if any(k in rule_name for k in config.CAR_RULE_KEYWORDS["LEAVE"]): action = "LEAVE"
                                    elif any(k in rule_name for k in config.CAR_RULE_KEYWORDS["RETURN"]): action = "RETURN"
                                    
                                    if action != "UNKNOWN":
                                        logger.info(f"🚗 車両判定: {action}")
                                        save_log_generic(config.SQLITE_TABLE_CAR,
                                            ["timestamp", "action", "rule_name"],
                                            (get_now_iso(), action, rule_name))

                                if event_type == "intrusion":
                                    msg = f"🚨【緊急】[{cam_loc}] {cam_name} に侵入者です！"
                                    send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=img, target="discord")
                                    time.sleep(15) 

                except Exception as e:
                    err = str(e)
                    # タイムアウトは正常動作なので無視してループ継続
                    if "timed out" in err or "TimeOut" in err: continue
                    
                    fatal_errors = ["Connection refused", "Errno 111", "RemoteDisconnected", "No route to host", "Broken pipe", "Renew Failed"]
                    if any(f in err for f in fatal_errors):
                        logger.warning(f"⚠️ [{cam_name}] 致命的エラー検知: {err} -> 即時再接続")
                        # 接続が完全に切れている場合のみ診断
                        if "Renew Failed" not in err:
                            perform_emergency_diagnosis(cam_conf['ip'], cam_conf)
                        raise Exception("Fatal Connection Error") 

                    logger.warning(f"⚠️ [{cam_name}] イベント受信エラー: {err}")
                    time.sleep(2)

        except Exception as e:
            err_msg = str(e)
            
            # クリーンアップ
            if current_pullpoint:
                try:
                    if current_pullpoint in active_pullpoints:
                        active_pullpoints.remove(current_pullpoint)
                    if hasattr(current_pullpoint, 'Unsubscribe'):
                        current_pullpoint.Unsubscribe()
                        logger.debug(f"🧹 [{cam_name}] Unsubscribe完了")
                except Exception: pass
                finally: current_pullpoint = None

            close_camera_connection(mycam)
            mycam = None
            
            consecutive_conn_errors += 1

            wait_time = min(30 * (2 ** (min(consecutive_conn_errors, 6) - 1)), MAX_WAIT_TIME)
            
            if consecutive_conn_errors >= NOTIFY_THRESHOLD and not has_notified_error:
                logger.error(f"❌ [{cam_name}] 接続不能({consecutive_conn_errors}回目)。待機時間を {wait_time}秒 に拡大します。(Error: {err_msg})")
                has_notified_error = True
            else:
                 logger.warning(f"❌ [{cam_name}] 接続失敗: {err_msg}")

            logger.info(f"💤 [{cam_name}] {wait_time}秒 待機します...")
            time.sleep(wait_time)

async def main():
    if not WSDL_DIR: 
        logger.error("❌ WSDLディレクトリが見つかりません。")
        return
    loop = asyncio.get_running_loop()
    tasks = []
    with ThreadPoolExecutor(max_workers=len(config.CAMERAS)) as executor:
        for cam in config.CAMERAS:
            tasks.append(loop.run_in_executor(executor, monitor_single_camera, cam))
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass