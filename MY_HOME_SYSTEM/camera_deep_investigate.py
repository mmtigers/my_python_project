# HOME_SYSTEM/camera_deep_investigate.py
from onvif import ONVIFCamera
from onvif.client import ONVIFService
from requests.auth import HTTPDigestAuth
import config
import common
import os
import sys
import time
from datetime import datetime, timedelta
from lxml import etree
import logging

# === 徹底調査用ロガー設定 ===
LOG_FILE = os.path.join(config.BASE_DIR, "..", "logs", "camera_investigation.log")
log_dir = os.path.dirname(LOG_FILE)
if not os.path.exists(log_dir): os.makedirs(log_dir)

# ロガーセットアップ
logger = logging.getLogger("deep_investigator")
logger.setLevel(logging.DEBUG)
# ファイル出力（詳細なXMLを含む全て）
fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
# コンソール出力（判定結果のみ）
sh = logging.StreamHandler()
sh.setLevel(logging.INFO)
sh.setFormatter(logging.Formatter('%(message)s'))

if logger.hasHandlers(): logger.handlers.clear()
logger.addHandler(fh)
logger.addHandler(sh)

print(f"📁 詳細ログの保存先: {LOG_FILE}")
print("   (画面には要約が表示されます。XML全文はログファイルを確認してください)")

# WSDLパス探索
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

def analyze_deeply(xml_str, cam_name):
    """
    受信したXMLを徹底的に解析し、判定ロジックの穴を探す
    """
    # 1. 生データをログに保存 (証拠確保)
    logger.debug(f"\n[{cam_name}] Raw XML Data:\n{xml_str}\n{'-'*50}")

    # 2. 基本情報の抽出
    rule_name = "Unknown"
    if 'Rule="' in xml_str:
        try:
            start = xml_str.find('Rule="') + 6
            end = xml_str.find('"', start)
            rule_name = xml_str[start:end]
        except: pass

    # イベントタイプの判定
    event_type = "Unknown"
    if 'Name="IsIntrusion"' in xml_str or 'Name="IsLineCross"' in xml_str: event_type = "intrusion"
    elif 'Name="IsPeople"' in xml_str or 'People' in rule_name: event_type = "person"
    elif 'Name="IsVehicle"' in xml_str or 'Vehicle' in rule_name: event_type = "vehicle"
    elif 'Name="IsMotion"' in xml_str or 'Motion' in rule_name: event_type = "motion"

    # 画面表示
    msg = f"\n----- 📩 受信データ ({cam_name}) -----\n"
    msg += f"📦 Rule Name  : 【 {rule_name} 】\n"
    msg += f"🔍 Event Type : 【 {event_type} 】"
    logger.info(msg)

    # 3. 隠れ情報の探索 (これが重要！)
    # XMLのどこかに 'Vehicle' や 'Car' が含まれていないかチェック
    hidden_vehicle_info = False
    if "Vehicle" in xml_str or "vehicle" in xml_str or "Car" in xml_str:
        if event_type != "vehicle" and "Vehicle" not in str(rule_name):
            hidden_vehicle_info = True
            logger.info("⚠️ 【発見！】XMLデータ内に 'Vehicle' という文字が含まれていますが、現在のロジックでは検知できていません！")
            logger.info("   👉 ログファイルのRaw XMLを確認し、ObjectTypeなどの項目を確認してください。")

    # 4. 判定ロジックシミュレーション
    # 現在の camera_monitor.py のロジック
    is_car_related = "vehicle" in event_type or "Vehicle" in str(rule_name) or event_type == "intrusion"
    
    logger.info("--- 判定ロジック検証 ---")
    if not is_car_related:
        logger.info("❌ [判定NG] 車関連イベントとして認識されません。")
        if hidden_vehicle_info:
            logger.info("   👉 原因: 情報は来ているのに、ロジックがそれを拾えていません。コード修正が必要です。")
        else:
            logger.info("   👉 原因: カメラから車に関する情報が送られてきていません。")
        return

    logger.info("✅ [判定OK] 車関連イベント(車両/侵入)として認識されました。")
    
    # 外出・帰宅キーワードチェック
    action = "UNKNOWN"
    leave_kw = config.CAR_RULE_KEYWORDS["LEAVE"]
    return_kw = config.CAR_RULE_KEYWORDS["RETURN"]
    
    matched_leave = [k for k in leave_kw if k in rule_name]
    matched_return = [k for k in return_kw if k in rule_name]

    if matched_leave:
        logger.info(f"🎉 [完全成功] 「外出 (LEAVE)」と判定されます。(Keyword: {matched_leave})")
    elif matched_return:
        logger.info(f"🎉 [完全成功] 「帰宅 (RETURN)」と判定されます。(Keyword: {matched_return})")
    else:
        logger.info(f"⚠️ [要設定] 車判定まではOKですが、「外出/帰宅」が区別できません！")
        logger.info(f"   受信したルール名: '{rule_name}'")
        logger.info(f"   設定中の外出KW: {leave_kw}")
        logger.info(f"   設定中の帰宅KW: {return_kws}")
        logger.info(f"   👉 対策: config.py のキーワードに '{rule_name}' の一部を追加してください。")

def monitor(cam_conf):
    try:
        # ポート番号対応 (前回判明した2020を使用)
        port = cam_conf.get('port', 80)
        logger.info(f"📡 {cam_conf['name']} に接続中... (IP: {cam_conf['ip']}, Port: {port})")

        mycam = ONVIFCamera(cam_conf['ip'], port, cam_conf['user'], cam_conf['pass'], wsdl_dir=WSDL_DIR)
        event_service = mycam.create_events_service()
        subscription = event_service.CreatePullPointSubscription()
        
        plp_address = subscription.SubscriptionReference.Address
        if hasattr(plp_address, '_value_1'): plp_address = plp_address._value_1

        pullpoint = ONVIFService(
            xaddr=plp_address, user=cam_conf['user'], passwd=cam_conf['pass'],
            url=os.path.join(WSDL_DIR, 'events.wsdl'), encrypt=True,
            binding_name='{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'
        )
        pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
        
        logger.info(f"✅ 監視開始！ 車を動かすか、カメラの前を通ってください...")

        while True:
            try:
                events = pullpoint.PullMessages({'Timeout': timedelta(seconds=5), 'MessageLimit': 100})
                if hasattr(events, 'NotificationMessage'):
                    for event in events.NotificationMessage:
                        msg = getattr(event, 'Message', None)
                        if msg:
                            raw = getattr(msg, '_value_1', msg)
                            xml = etree.tostring(raw, encoding='unicode') if hasattr(raw, 'tag') else str(raw)
                            
                            # 検知開始(True)のみ対象
                            if 'Value="true"' in xml or 'State="true"' in xml:
                                analyze_deeply(xml, cam_conf['name'])
                                
            except Exception as e:
                pass
            
    except Exception as e:
        logger.error(f"接続エラー: {e}")

if __name__ == "__main__":
    if config.CAMERAS:
        # 1台目（駐車場カメラ）を対象に徹底調査
        monitor(config.CAMERAS[0])
    else:
        logger.error("カメラ設定が見つかりません")