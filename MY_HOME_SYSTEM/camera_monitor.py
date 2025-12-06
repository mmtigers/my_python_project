# HOME_SYSTEM/camera_monitor.py
from onvif import ONVIFCamera
from onvif.client import ONVIFService
from requests.auth import HTTPDigestAuth
import config
import common
import asyncio
from datetime import timedelta
import os
import sys
import time
import zeep.helpers
from lxml import etree
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logging.getLogger("zeep").setLevel(logging.WARNING)

BINDING_NAME = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'
KEYWORDS_PERSON = ["Human", "Person", "People", "Face"]
KEYWORDS_VEHICLE = ["Vehicle", "Car", "Truck", "Bus", "Motorcycle"]
KEYWORDS_MOTION = ["Motion", "Rule"]

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

def analyze_event(message_node):
    try:
        raw_element = getattr(message_node, '_value_1', message_node)
        if raw_element is None: return False, None, None
        
        if hasattr(raw_element, 'tag'):
            xml_str = etree.tostring(raw_element, encoding='unicode')
        else:
            xml_str = str(raw_element)

        rule_name = "Unknown"
        if 'Rule="' in xml_str:
            start = xml_str.find('Rule="') + 6
            end = xml_str.find('"', start)
            rule_name = xml_str[start:end]

        if 'Value="true"' in xml_str or 'State="true"' in xml_str:
            if any(k in xml_str for k in KEYWORDS_VEHICLE): return True, "車両", rule_name
            if any(k in xml_str for k in KEYWORDS_PERSON): return True, "人物", rule_name
            if "Motion" in xml_str: return True, "動き", rule_name

        return False, None, None
    except Exception:
        return False, None, None

def capture_snapshot_rtsp(cam_conf):
    """カメラごとの設定でスナップショットを取得"""
    tmp_path = f"/tmp/snapshot_{cam_conf['id']}.jpg"
    rtsp_url = f"rtsp://{cam_conf['user']}:{cam_conf['pass']}@{cam_conf['ip']}:554/stream1"
    
    cmd = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", rtsp_url, "-frames:v", "1", "-q:v", "2", tmp_path]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        if os.path.exists(tmp_path):
            with open(tmp_path, "rb") as f: return f.read()
    except Exception as e:
        logging.error(f"[{cam_conf['name']}] キャプチャ失敗: {e}")
    return None

def monitor_single_camera(cam_conf):
    """1台のカメラを監視するプロセス（ブロッキング処理）"""
    cam_name = cam_conf['name']
    cam_ip = cam_conf['ip']
    logging.info(f"🚀 [{cam_name}] 監視スレッド起動 ({cam_ip})")

    while True:
        try:
            mycam = ONVIFCamera(cam_ip, 80, cam_conf['user'], cam_conf['pass'], wsdl_dir=WSDL_DIR)
            event_service = mycam.create_events_service()
            subscription = event_service.CreatePullPointSubscription()
            
            try:
                plp_address = subscription.SubscriptionReference.Address._value_1
            except AttributeError:
                plp_address = subscription.SubscriptionReference.Address
            
            events_wsdl = os.path.join(WSDL_DIR, 'events.wsdl')
            pullpoint = ONVIFService(
                xaddr=plp_address,
                user=cam_conf['user'],
                passwd=cam_conf['pass'],
                url=events_wsdl,
                encrypt=True,
                binding_name=BINDING_NAME
            )
            pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
            logging.info(f"✅ [{cam_name}] 接続成功")

            error_count = 0
            while True:
                try:
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    error_count = 0
                    
                    if hasattr(events, 'NotificationMessage'):
                        for event in events.NotificationMessage:
                            is_detected, label, rule_name = analyze_event(event.Message)
                            
                            if is_detected:
                                logging.info(f"🔥 [{cam_name}] 検知: {label} (Rule: {rule_name})")
                                
                                img = capture_snapshot_rtsp(cam_conf)
                                
                                # DB記録
                                common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                    ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                    (common.get_now_iso(), "防犯カメラ", cam_conf['id'], "ONVIF Camera", "detected"))
                                
                                # 車両記録
                                if label == "車両":
                                    action = "UNKNOWN"
                                    if any(k in rule_name for k in config.CAR_RULE_KEYWORDS["LEAVE"]): action = "LEAVE"
                                    elif any(k in rule_name for k in config.CAR_RULE_KEYWORDS["RETURN"]): action = "RETURN"
                                    if action != "UNKNOWN":
                                        common.save_log_generic(config.SQLITE_TABLE_CAR, ["timestamp", "action", "rule_name"], (common.get_now_iso(), action, rule_name))

                                # 通知
                                msg = f"📷【{cam_name}】\n{label}を検知しました！"
                                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=img)
                                
                                time.sleep(10)
                                break
                except Exception as e:
                    err = str(e)
                    if "timed out" in err or "TimeOut" in err: continue
                    error_count += 1
                    if error_count >= 5:
                        logging.warning(f"⚠️ [{cam_name}] 再接続します...")
                        break
                    time.sleep(2)

        except Exception as e:
            logging.error(f"❌ [{cam_name}] 接続エラー: {e}")
            time.sleep(30)

async def main():
    if not WSDL_DIR:
        logging.error("WSDLが見つかりません。")
        return

    loop = asyncio.get_running_loop()
    
    # カメラごとの監視タスクを並列実行
    tasks = []
    with ThreadPoolExecutor(max_workers=len(config.CAMERAS)) as executor:
        for cam in config.CAMERAS:
            # ブロッキング関数を別スレッドで実行
            tasks.append(loop.run_in_executor(executor, monitor_single_camera, cam))
        
        # 全タスクの終了を待つ（無限ループなので実質終わらない）
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass