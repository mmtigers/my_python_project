# HOME_SYSTEM/verify_camera_notifications.py
import sys
import os
import time
from datetime import timedelta
from onvif import ONVIFCamera
from onvif.client import ONVIFService
from requests.auth import HTTPDigestAuth
from lxml import etree
import logging

# パス設定
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config
import common

# ログ設定 (コンソール出力のみ)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("verify_cam")

# 定数
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

WSDL_DIR = find_wsdl_path()

# ★本番と同じ判定ロジック
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

    # 1. 侵入・ライン通過 (ここが通知対象)
    if ('Name="IsIntrusion"' in xml_str or 'Name="IsLineCross"' in xml_str or 
        "Intrusion" in rule_name or "LineCross" in rule_name or "Cross" in rule_name):
        return "intrusion", "敷地への侵入", 100, rule_name

    # 2. 人物検知
    if 'Name="IsPeople"' in xml_str or 'People' in rule_name or 'Person' in rule_name:
        return "person", "人", 80, rule_name

    # 3. 車両検知
    if 'Name="IsVehicle"' in xml_str or 'Vehicle' in rule_name or 'Car' in rule_name:
        return "vehicle", "車", 50, rule_name

    # 4. 一般的な動体検知
    if 'Name="IsMotion"' in xml_str or 'Motion' in rule_name:
        return "motion", "動き", 10, rule_name

    return None, None, 0, None

def monitor_test(cam_conf):
    print(f"\n📡 カメラ接続テスト: {cam_conf['name']} (IP: {cam_conf['ip']})")
    
    try:
        mycam = ONVIFCamera(cam_conf['ip'], cam_conf.get('port', 80), 
                           cam_conf['user'], cam_conf['pass'], wsdl_dir=WSDL_DIR)
        event_service = mycam.create_events_service()
        subscription = event_service.CreatePullPointSubscription()
        
        plp_address = subscription.SubscriptionReference.Address
        if hasattr(plp_address, '_value_1'): plp_address = plp_address._value_1

        pullpoint = ONVIFService(
            xaddr=plp_address, user=cam_conf['user'], passwd=cam_conf['pass'],
            url=os.path.join(WSDL_DIR, 'events.wsdl'), encrypt=True, binding_name=BINDING_NAME
        )
        pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
        
        print("✅ 接続成功！ 監視を開始します (Ctrl+C で終了)")
        print("="*60)

        while True:
            try:
                events = pullpoint.PullMessages({'Timeout': timedelta(seconds=2), 'MessageLimit': 100})
                if hasattr(events, 'NotificationMessage'):
                    for event in events.NotificationMessage:
                        msg = getattr(event, 'Message', None)
                        if not msg: continue
                        
                        raw_element = getattr(msg, '_value_1', msg)
                        if hasattr(raw_element, 'tag'):
                            xml_str = etree.tostring(raw_element, encoding='unicode')
                        else:
                            xml_str = str(raw_element)

                        # 解析実行
                        event_type, label, priority, rule_name = analyze_event_type(xml_str)
                        
                        if event_type:
                            print(f"\n🔎 検知: {label} (Type: {event_type})")
                            print(f"   Rule名: {rule_name}")
                            
                            # 判定結果の表示
                            if event_type == "intrusion":
                                print("   🚨 判定: [通知対象] (Discordに通知されます)")
                            else:
                                print("   📝 判定: [記録のみ] (通知はされません)")
                                
                            print("-" * 30)
                            
            except Exception as e:
                # タイムアウトは無視
                if "time" not in str(e).lower():
                    print(f"⚠️ エラー: {e}")

    except Exception as e:
        print(f"❌ 接続失敗: {e}")

if __name__ == "__main__":
    if not config.CAMERAS:
        print("❌ config.py にカメラ設定がありません")
    else:
        # 1台目のカメラ（駐車場）をテスト
        monitor_test(config.CAMERAS[0])