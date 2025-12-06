# HOME_SYSTEM/camera_monitor.py
from onvif import ONVIFCamera
from onvif.client import ONVIFService
from requests.auth import HTTPDigestAuth
import requests
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

# === ログ設定 ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logging.getLogger("zeep").setLevel(logging.WARNING)

# バインディング名
BINDING_NAME = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'

# === 検知キーワード設定 ===
# XML内にこれらの単語が含まれていたら、それぞれの種別として判定します
KEYWORDS_PERSON = ["Human", "Person", "People", "Face"]
KEYWORDS_VEHICLE = ["Vehicle", "Car", "Truck", "Bus", "Motorcycle"]
KEYWORDS_MOTION = ["Motion", "Rule"] # Ruleは汎用的な検知

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
    """
    受信したメッセージを解析し、検知種別（人物/車両/動き）を判定する
    """
    try:
        raw_element = getattr(message_node, '_value_1', message_node)
        if raw_element is None: return False, None
        
        if hasattr(raw_element, 'tag'):
            xml_str = etree.tostring(raw_element, encoding='unicode')
        else:
            xml_str = str(raw_element)

        # 1. 検知状態のチェック (Value="true" または State="true" が含まれているか)
        # ※ 検知終了(false)の通知は除外するため
        if 'Value="true"' not in xml_str and 'State="true"' not in xml_str:
            return False, None

        # 2. 種別の判定 (優先順位: 人物 > 車両 > 一般的な動き)
        
        # 人物検知
        if any(k in xml_str for k in KEYWORDS_PERSON):
            return True, "人物"
            
        # 車両検知
        if any(k in xml_str for k in KEYWORDS_VEHICLE):
            return True, "車両"
            
        # その他の動き (Motionという単語が含まれる場合)
        if any(k in xml_str for k in KEYWORDS_MOTION):
            return True, "動き"

        return False, None
    except Exception:
        return False, None

def capture_snapshot_rtsp():
    """FFmpegでRTSPストリームから静止画をキャプチャ"""
    tmp_path = "/tmp/snapshot.jpg"
    # メインストリーム(stream1)を使用
    rtsp_url = f"rtsp://{config.CAMERA_USER}:{config.CAMERA_PASS}@{config.CAMERA_IP}:554/stream1"
    
    cmd = [
        "ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", rtsp_url,
        "-frames:v", "1", "-q:v", "2", tmp_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        if os.path.exists(tmp_path):
            with open(tmp_path, "rb") as f: return f.read()
    except Exception as e:
        logging.error(f"画像キャプチャ失敗: {e}")
    return None

async def run_camera_monitor():
    logging.info(f"=== カメラ監視システム起動 ({config.CAMERA_IP}) ===")
    
    wsdl_dir = find_wsdl_path()
    if not wsdl_dir:
        logging.error("WSDLが見つかりません。")
        return

    while True: # 再接続ループ
        try:
            # logging.info("📡 接続中...") # ログ抑制
            
            mycam = ONVIFCamera(config.CAMERA_IP, 80, config.CAMERA_USER, config.CAMERA_PASS, wsdl_dir=wsdl_dir)
            event_service = mycam.create_events_service()
            
            # フィルターなしで全イベントを受信（カメラ側の設定に依存させる）
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
            logging.info("✅ 監視ループ開始")

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
                                logger.info(f"\n🔥 【検知】 {label} - 写真撮るね！")
                                
                                img = capture_snapshot(media_service, media_profile)
                                
                                common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                    ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                    (common.get_now_iso(), "防犯カメラ", "VIGI_C540_W", "ONVIF Camera", "detected"))
                                
                                msg = f"📷【カメラ通知】\n{label}が通ったかも！"
                                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=img)
                                
                                await asyncio.sleep(10)
                                break

                except KeyboardInterrupt: raise
                except Exception:
                    error_count += 1
                    if error_count >= 5:
                        logging.warning("\n再接続します...")
                        break 
                    await asyncio.sleep(5)

        except KeyboardInterrupt:
            logging.info("\n停止しました。")
            break
        except Exception as e:
            logging.error(f"\n接続エラー: {e}")
            logging.info("30秒後に再接続...")
            time.sleep(30)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_camera_monitor())