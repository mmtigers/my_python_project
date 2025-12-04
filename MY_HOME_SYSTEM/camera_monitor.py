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

# ロガー設定 (共通設定を使用)
logger = common.setup_logging("camera")
logging.getLogger("zeep").setLevel(logging.WARNING)

BINDING_NAME = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'

def find_wsdl_path():
    for path in sys.path:
        if 'site-packages' in path and os.path.exists(path):
            candidate = os.path.join(path, 'onvif', 'wsdl')
            if os.path.exists(os.path.join(candidate, 'devicemgmt.wsdl')):
                return candidate
            for root, dirs, files in os.walk(path):
                if 'devicemgmt.wsdl' in files: return root
    return None

def check_detection(message_node):
    try:
        raw_element = getattr(message_node, '_value_1', message_node)
        if raw_element is None: return False, None
        
        if hasattr(raw_element, 'tag'):
            xml_str = etree.tostring(raw_element, encoding='unicode')
        else:
            xml_str = str(raw_element)

        # 判定ロジック
        if 'Name="IsMotion"' in xml_str and 'Value="true"' in xml_str:
            if any(k in xml_str for k in ["Human", "Person", "People"]):
                return True, "人物"
            return True, "動き"
        return False, None
    except Exception:
        return False, None

async def run_camera_monitor():
    logger.info(f"=== カメラ監視システム起動 ({config.CAMERA_IP}) ===")
    
    wsdl_dir = find_wsdl_path()
    if not wsdl_dir:
        logger.error("WSDLが見つかりません。")
        return

    while True:
        try:
            mycam = ONVIFCamera(config.CAMERA_IP, 80, config.CAMERA_USER, config.CAMERA_PASS, wsdl_dir=wsdl_dir)
            event_service = mycam.create_events_service()
            subscription = event_service.CreatePullPointSubscription()
            
            try:
                plp_address = subscription.SubscriptionReference.Address._value_1
            except AttributeError:
                plp_address = subscription.SubscriptionReference.Address
            
            events_wsdl = os.path.join(wsdl_dir, 'events.wsdl')
            pullpoint = ONVIFService(
                xaddr=plp_address,
                user=config.CAMERA_USER,
                passwd=config.CAMERA_PASS,
                url=events_wsdl,
                encrypt=True,
                binding_name=BINDING_NAME
            )
            
            pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(config.CAMERA_USER, config.CAMERA_PASS)
            logger.info("✅ 監視ループ開始")

            error_count = 0
            while True:
                try:
                    print(".", end="", flush=True)
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    error_count = 0
                    
                    if hasattr(events, 'NotificationMessage'):
                        for event in events.NotificationMessage:
                            is_detected, label = check_detection(event.Message)
                            if is_detected:
                                logger.info(f"\n🔥 【検知】 {label}")
                                common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                    ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                    (common.get_now_iso(), "防犯カメラ", "VIGI_C540_W", "ONVIF Camera", "detected"))
                                
                                # 修正: send_line_push -> send_push
                                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"📷【カメラ通知】\n{label}を検知しました！"}])
                                
                                await asyncio.sleep(10)
                                break
                except KeyboardInterrupt: raise
                except Exception:
                    error_count += 1
                    if error_count >= 5:
                        logger.warning("\n再接続します...")
                        break 
                    await asyncio.sleep(5)

        except KeyboardInterrupt:
            logger.info("\n停止しました。")
            break
        except Exception as e:
            logger.error(f"\n接続エラー: {e}")
            logger.info("30秒後に再接続...")
            time.sleep(30)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_camera_monitor())