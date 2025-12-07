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

# === ログ設定 ===
# 共通ロガーを使用
logger = common.setup_logging("camera")
logging.getLogger("zeep").setLevel(logging.WARNING)

# === 定数定義 ===
BINDING_NAME = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'

# 優先度定義 (数値が大きいほど優先)
PRIORITY_MAP = {
    "intrusion": 100, # 侵入・ライン通過
    "person": 80,     # 人物
    "vehicle": 50,    # 車両
    "motion": 10      # 単なる動き
}

def find_wsdl_path():
    """WSDLファイルの場所を自動探索"""
    for path in sys.path:
        if 'site-packages' in path and os.path.exists(path):
            candidate = os.path.join(path, 'onvif', 'wsdl')
            if os.path.exists(os.path.join(candidate, 'devicemgmt.wsdl')):
                return candidate
            for root, dirs, files in os.walk(path):
                if 'devicemgmt.wsdl' in files: return root
    return None

def analyze_event_type(xml_str):
    """
    XML文字列からイベントの種類と重要度を判定する
    戻り値: (event_type, label, priority, raw_rule_name)
    """
    # 検知終了(False)の通知は無視
    if 'Value="true"' not in xml_str and 'State="true"' not in xml_str:
        return None, None, 0, None

    # ルール名の抽出 (デバッグ用)
    rule_name = "Unknown"
    if 'Rule="' in xml_str:
        try:
            start = xml_str.find('Rule="') + 6
            end = xml_str.find('"', start)
            rule_name = xml_str[start:end]
        except: pass

    # --- 判定ロジック (VIGI固有のログパターンに基づく) ---
    
    # 1. 侵入・ライン通過 (最優先)
    if 'Name="IsIntrusion"' in xml_str or 'Name="IsLineCross"' in xml_str:
        return "intrusion", "敷地への侵入", PRIORITY_MAP["intrusion"], rule_name

    # 2. 人物検知
    # VIGIは "IsPeople" または Rule名に "People" を含む
    if 'Name="IsPeople"' in xml_str or 'People' in rule_name:
        return "person", "人", PRIORITY_MAP["person"], rule_name

    # 3. 車両検知
    if 'Name="IsVehicle"' in xml_str or 'Vehicle' in rule_name:
        return "vehicle", "車", PRIORITY_MAP["vehicle"], rule_name

    # 4. 一般的な動体検知
    if 'Name="IsMotion"' in xml_str or 'Motion' in rule_name:
        return "motion", "動き", PRIORITY_MAP["motion"], rule_name

    return None, None, 0, None

def capture_snapshot_rtsp():
    """FFmpegでRTSPストリームから静止画をキャプチャ"""
    tmp_path = "/tmp/snapshot.jpg"
    # 高画質ストリーム (stream1)
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
        logger.error(f"画像キャプチャ失敗: {e}")
    return None

async def run_camera_monitor():
    logger.info(f"=== カメラ監視システム起動 ({config.CAMERA_IP}) ===")
    
    wsdl_dir = find_wsdl_path()
    if not wsdl_dir:
        logger.error("WSDLが見つかりません。")
        return

    # 再接続ループ
    while True:
        try:
            logger.info("📡 カメラに接続中...")
            
            mycam = ONVIFCamera(config.CAMERA_IP, 80, config.CAMERA_USER, config.CAMERA_PASS, wsdl_dir=wsdl_dir)
            event_service = mycam.create_events_service()
            
            # フィルターなしで購読 (カメラ側の設定に依存)
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
                    # ポーリング
                    print(".", end="", flush=True)
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    error_count = 0
                    
                    if hasattr(events, 'NotificationMessage'):
                        for event in events.NotificationMessage:
                            # メッセージの解析
                            message_node = getattr(event, 'Message', None)
                            if not message_node: continue

                            # XMLを文字列化して解析
                            raw_element = getattr(message_node, '_value_1', message_node)
                            if hasattr(raw_element, 'tag'):
                                xml_str = etree.tostring(raw_element, encoding='unicode')
                            else:
                                xml_str = str(raw_element)

                            # イベント判定
                            event_type, label, priority, rule_name = analyze_event_type(xml_str)
                            
                            if event_type:
                                logger.info(f"\n🔥 検知: {label} (Rule: {rule_name})")
                                
                                # 画像取得
                                img = capture_snapshot_rtsp()
                                
                                # DB記録
                                common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                    ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                    (common.get_now_iso(), "防犯カメラ", "VIGI_C540_W", "ONVIF Camera", event_type))
                                
                                # 通知メッセージの作成 (主婦向けトーン)
                                if event_type == "intrusion":
                                    msg = "🚨【緊急】敷地に入った人がいます！\n画像を確認してください。"
                                elif event_type == "person":
                                    msg = "👤 あ、誰か来たみたいです。\nお客様かな？"
                                elif event_type == "vehicle":
                                    msg = "🚗 車が通りました。\nパパが帰ってきたかも？"
                                elif event_type == "motion":
                                    # 動体検知のみの場合は、通知頻度を下げるか、通知しない設定も検討
                                    # 今回は控えめな通知にする
                                    msg = "👀 何か動いたみたいです。"
                                
                                # 通知送信
                                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=img)
                                
                                # 車両検知の場合は、車の利用記録(car_records)にも追加
                                if event_type == "vehicle":
                                    # 外出か帰宅かはカメラの方向やルール名(RegionEntering/Exiting)で判別可能だが
                                    # ここでは簡易的に「検知」として記録し、詳細はRule名で保存
                                    action = "DETECTED"
                                    if "Exit" in rule_name or "Leave" in rule_name: action = "LEAVE"
                                    elif "Enter" in rule_name or "Arrive" in rule_name: action = "RETURN"
                                    
                                    common.save_log_generic(config.SQLITE_TABLE_CAR,
                                        ["timestamp", "action", "rule_name"],
                                        (common.get_now_iso(), action, rule_name))

                                # クールタイム (連続通知防止)
                                await asyncio.sleep(15)
                                break # 1回のポーリングで1つのイベントを処理したら抜ける

                except KeyboardInterrupt: raise
                except Exception:
                    error_count += 1
                    if error_count >= 5:
                        logger.warning("\n再接続します...")
                        break 
                    await asyncio.sleep(2)

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