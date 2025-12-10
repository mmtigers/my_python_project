# HOME_SYSTEM/camera_monitor.py
from onvif import ONVIFCamera
from onvif.client import ONVIFService
from requests.auth import HTTPDigestAuth
import config
import common
import asyncio
from datetime import datetime, timedelta
import os
import sys
import time
import zeep.helpers
from lxml import etree
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor

# === ログ設定 ===
logger = common.setup_logging("camera")
logging.getLogger("zeep").setLevel(logging.WARNING)

# === 画像保存設定 ===
ASSETS_DIR = os.path.join(config.BASE_DIR, "..", "assets", "snapshots")
if not os.path.exists(ASSETS_DIR):
    os.makedirs(ASSETS_DIR, exist_ok=True)

# === 定数定義 ===
BINDING_NAME = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'

# 優先度定義
PRIORITY_MAP = {
    "intrusion": 100, "person": 80, "vehicle": 50, "motion": 10
}

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
    if 'Name="IsIntrusion"' in xml_str or 'Name="IsLineCross"' in xml_str:
        return "intrusion", "敷地への侵入", PRIORITY_MAP["intrusion"], rule_name

    # 2. 人物検知
    if 'Name="IsPeople"' in xml_str or 'People' in rule_name:
        return "person", "人", PRIORITY_MAP["person"], rule_name

    # 3. 車両検知
    if 'Name="IsVehicle"' in xml_str or 'Vehicle' in rule_name:
        return "vehicle", "車", PRIORITY_MAP["vehicle"], rule_name

    # 4. 一般的な動体検知
    if 'Name="IsMotion"' in xml_str or 'Motion' in rule_name:
        return "motion", "動き", PRIORITY_MAP["motion"], rule_name

    return None, None, 0, None

def capture_snapshot_rtsp(cam_conf):
    tmp_path = f"/tmp/snapshot_{cam_conf['id']}.jpg"
    rtsp_url = f"rtsp://{cam_conf['user']}:{cam_conf['pass']}@{cam_conf['ip']}:554/stream1"
    
    cmd = [
        "ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", rtsp_url,
        "-frames:v", "1", "-q:v", "2", tmp_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        if os.path.exists(tmp_path):
            with open(tmp_path, "rb") as f: return f.read()
    except Exception as e:
        logger.error(f"[{cam_conf['name']}] 画像キャプチャ失敗: {e}")
    return None

def monitor_single_camera(cam_conf):
    cam_name = cam_conf['name']
    cam_port = cam_conf.get('port', 80) # ★修正: 設定がなければ80、あればそれを使う
    cam_loc = cam_conf.get('location', '伊丹')
    
    logger.info(f"🚀 [{cam_name}] 監視スレッド起動 (IP:{cam_conf['ip']} Port:{cam_port})")

    while True: 
        try:
            # ★修正: ポート番号を指定して接続
            mycam = ONVIFCamera(cam_conf['ip'], cam_port, cam_conf['user'], cam_conf['pass'], wsdl_dir=WSDL_DIR)
            event_service = mycam.create_events_service()
            subscription = event_service.CreatePullPointSubscription()
            
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
            logger.info(f"✅ [{cam_name}] 接続確立")

            error_count = 0
            while True:
                try:
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    error_count = 0
                    
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
                                img = capture_snapshot_rtsp(cam_conf)

                                # ギャラリー保存
                                if img:
                                    try:
                                        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                                        filename = f"snapshot_{cam_conf['id']}_{ts}.jpg"
                                        save_path = os.path.join(ASSETS_DIR, filename)
                                        with open(save_path, "wb") as f: f.write(img)
                                        logger.info(f"🖼️ 画像保存: {filename}")
                                    except Exception as e:
                                        logger.error(f"画像保存失敗: {e}")
                                
                                # DB記録
                                common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                    ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                    (common.get_now_iso(), "防犯カメラ", cam_conf['id'], "ONVIF Camera", event_type))
                                
                                # 車の記録判定 (侵入も含めるようロジック強化)
                                is_car_related = "vehicle" in event_type or "Vehicle" in str(rule_name) or event_type == "intrusion"
                                if is_car_related:
                                    action = "UNKNOWN"
                                    # 外出判定
                                    if any(k in rule_name for k in config.CAR_RULE_KEYWORDS["LEAVE"]):
                                        action = "LEAVE"
                                    # 帰宅判定
                                    elif any(k in rule_name for k in config.CAR_RULE_KEYWORDS["RETURN"]):
                                        action = "RETURN"
                                    
                                    if action != "UNKNOWN":
                                        common.save_log_generic(config.SQLITE_TABLE_CAR,
                                            ["timestamp", "action", "rule_name"],
                                            (common.get_now_iso(), action, rule_name))

                                # 通知送信 (Discord対応)
                                if priority >= 50:
                                    msg = f"📷【カメラ通知】\n[{cam_loc}] {cam_name} で{label}を検知しました！"
                                    if event_type == "intrusion":
                                        msg = f"🚨【緊急】[{cam_loc}] {cam_name} に侵入者です！"
                                    
                                    # ★修正: target="discord" を明示
                                    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=img, target="discord")
                                    
                                    time.sleep(15)
                                    break

                except Exception as e:
                    err = str(e)
                    if "timed out" in err or "TimeOut" in err: continue
                    error_count += 1
                    if error_count >= 5:
                        logger.warning(f"⚠️ [{cam_name}] 再接続します...")
                        break
                    time.sleep(2)

        except Exception as e:
            logger.error(f"❌ [{cam_name}] 接続エラー (Port:{cam_port}): {e}")
            time.sleep(30)

async def main():
    if not WSDL_DIR: return
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