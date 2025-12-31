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
import traceback

# === ログ設定 ===
logger = common.setup_logging("camera")
logging.getLogger("zeep").setLevel(logging.WARNING)

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

    # --- 判定ロジック (ここを強化) ---
    
    # 1. 侵入・ライン通過
    # Name属性だけでなく、Rule名に 'Intrusion' や 'LineCross', 'Cross' が含まれる場合も対象にする
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
    cam_port = cam_conf.get('port', 80)
    cam_loc = cam_conf.get('location', '伊丹')
    
    logger.info(f"🚀 [{cam_name}] 監視スレッド起動 (IP:{cam_conf['ip']} Port:{cam_port}) WSDL:{WSDL_DIR}")

    # === 【修正】連続エラーカウントと通知閾値の設定 ===
    consecutive_conn_errors = 0
    NOTIFY_THRESHOLD = 5  # 5回連続失敗で通知
    has_notified_error = False  # エラー通知済みフラグを追加

    while True: 
        try:
            # ONVIFカメラ接続
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
            
            
            # === 接続成功時 ===
            if consecutive_conn_errors > 0:
                logger.info(f"✅ [{cam_name}] 接続復旧しました")
            consecutive_conn_errors = 0
            has_notified_error = False  # フラグをリセット
            
            logger.info(f"✅ [{cam_name}] 接続確立")

            error_count = 0
            # イベント受信ループ
            while True:
                try:
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    error_count = 0  # PullMessages成功で内部エラーカウンタもリセット
                    
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
                                
                                # 車判定ロジック (外出/帰宅記録用)
                                is_car_related = "vehicle" in event_type or "Vehicle" in str(rule_name) or event_type == "intrusion"
                                if is_car_related:
                                    action = "UNKNOWN"
                                    if any(k in rule_name for k in config.CAR_RULE_KEYWORDS["LEAVE"]):
                                        action = "LEAVE"
                                    elif any(k in rule_name for k in config.CAR_RULE_KEYWORDS["RETURN"]):
                                        action = "RETURN"
                                    
                                    if action != "UNKNOWN":
                                        logger.info(f"🚗 車両移動判定: {action} (Rule: {rule_name})")
                                        common.save_log_generic(config.SQLITE_TABLE_CAR,
                                            ["timestamp", "action", "rule_name"],
                                            (common.get_now_iso(), action, rule_name))

                                # 通知送信 (侵入のみ)
                                if event_type == "intrusion":
                                    msg = f"🚨【緊急】[{cam_loc}] {cam_name} に侵入者です！"
                                    
                                    # target="discord" を指定
                                    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=img, target="discord")
                                    
                                    # 通知した場合はクールタイムを入れる
                                    time.sleep(15)
                                    break

                except Exception as e:
                    # 内部ループ（PullMessages）のエラーハンドリング
                    err = str(e)
                    # タイムアウトはよくあるので無視してループ継続
                    if "timed out" in err or "TimeOut" in err: continue
                    
                    error_count += 1
                    # ★修正: 内部エラー時も詳細ログを出す
                    if error_count >= 5:
                        logger.warning(f"⚠️ [{cam_name}] ストリーム不安定のため再接続します... (Error: {err})")
                        logger.debug(traceback.format_exc())
                        break
                    time.sleep(2)

        except Exception as e:
            # === 【修正】外部ループ（接続自体）のエラーハンドリング ===
            consecutive_conn_errors += 1
            err_msg = str(e)
            
            # ★追加: 詳細なスタックトレースを取得
            tb = traceback.format_exc()


            # 待機時間の計算 (基本30秒 * 失敗回数。最大300秒)
            wait_time = min(30 * consecutive_conn_errors, 300)

            # エラー判定: 接続拒否やタイムアウトはネットワーク/機器起因
            is_network_issue = "Connection refused" in err_msg or "timed out" in err_msg or "No route to host" in err_msg or "111" in err_msg

            if is_network_issue:
                if consecutive_conn_errors < NOTIFY_THRESHOLD:
                    # 閾値未満: WARNING (通知なし)
                    logger.warning(f"⚠️ [{cam_name}] 接続試行中({consecutive_conn_errors}/{NOTIFY_THRESHOLD})... : {err_msg}")
                
                elif consecutive_conn_errors == NOTIFY_THRESHOLD and not has_notified_error:
                    # 閾値到達時: ERROR (通知あり・初回のみ)
                    logger.error(f"❌ [{cam_name}] 接続不能: 規定回数失敗しました。以降は復旧まで静観します。(Error: {err_msg})")
                    has_notified_error = True
                
                else:
                    # 閾値超過かつ通知済み: WARNING (通知なし・静観モード)
                    logger.warning(f"💤 [{cam_name}] 接続不可継続中 ({consecutive_conn_errors}回目)... Retry in {wait_time}s")
            else:
                # ネットワーク以外（認証エラーやコードバグなど）は毎回 ERROR
                logger.error(f"❌ [{cam_name}] 予期せぬ接続エラー: {err_msg}\n詳細:\n{tb}")

            time.sleep(wait_time)

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