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
import socket
import zeep.helpers
from lxml import etree
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
import traceback
import signal

# === ログ設定 ===
logger = common.setup_logging("camera")
# 調査のためZeep(通信ライブラリ)のログも少し出す
logging.getLogger("zeep").setLevel(logging.ERROR) 

# プロセス終了時にUnsubscribeするために、アクティブなSubscriptionを保持する
active_subscriptions = []

def cleanup_handler(signum, frame):
    """プロセス終了シグナルを受け取った時のクリーンアップ処理"""
    logger.info(f"🛑 終了シグナル({signum})を受信。カメラ接続のクリーンアップを開始します...")
    
    for sub in active_subscriptions:
        try:
            # ONVIFのUnsubscribeメソッドを呼び出す
            if hasattr(sub, 'Unsubscribe'):
                sub.Unsubscribe()
                logger.info("✅ Unsubscribe送信成功")
            # zeep objectの場合のフォールバック
            elif hasattr(sub, 'service') and hasattr(sub.service, 'Unsubscribe'):
                sub.service.Unsubscribe(_soapheaders=None)
                logger.info("✅ Unsubscribe送信成功 (zeep)")
        except Exception as e:
            logger.warning(f"⚠️ Unsubscribe送信失敗 (無視します): {e}")

    logger.info("👋 監視プロセスを終了します")
    sys.exit(0)

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

def perform_emergency_diagnosis(ip):
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

    # 診断結果の解釈
    if all(results.values()):
        msg += "   👉 結論: ネットワークは正常です。認証エラーか、カメラの接続数上限(Busy)の可能性があります。"
    elif not any(results.values()):
        msg += "   👉 結論: カメラとの通信が完全に途絶しています(電源断/IP変更/ケーブル抜け)。"
    elif results[554] and not results[2020] and not results[80]:
        msg += "   👉 結論: 映像(RTSP)は生きていますが、制御(ONVIF/HTTP)サービスだけダウンしています。"
    
    logger.error(msg)
    return results

def monitor_single_camera(cam_conf):
    cam_name = cam_conf['name']
    cam_base_port = cam_conf.get('port', 80)
    cam_loc = cam_conf.get('location', '伊丹')
    
    logger.info(f"🚀 [{cam_name}] 監視プロセス起動 (Target IP:{cam_conf['ip']})")

    consecutive_conn_errors = 0
    NOTIFY_THRESHOLD = 5
    has_notified_error = False

    # Backoffの上限設定（最大10分待機）
    MAX_WAIT_TIME = 600 

    # ポートフォールバック用のリスト作成
    port_candidates = []
    if cam_base_port not in [80, 2020]: port_candidates.append(cam_base_port)
    port_candidates.extend([2020, 80]) # 優先順位: 指定ポート -> 2020 -> 80
    port_candidates = list(dict.fromkeys(port_candidates)) # 重複排除

    while True: 
        mycam = None
        current_port = None
        
        try:
            # --- 接続フェーズ ---
            for port in port_candidates:
                try:
                    # 【修正1】攻撃とみなされないようポートスキャン時に少し待つ
                    time.sleep(1.0)
                    
                    logger.debug(f"[{cam_name}] ポート {port} で接続試行中...")
                    
                    # 【修正2】ソケットタイムアウトを明示してハング防止
                    socket.setdefaulttimeout(10.0)

                    mycam = ONVIFCamera(cam_conf['ip'], port, cam_conf['user'], cam_conf['pass'], wsdl_dir=WSDL_DIR)
                    
                    # サービス作成テスト（ここが通れば認証OK）
                    mycam.create_events_service()
                    
                    current_port = port
                    logger.info(f"✅ [{cam_name}] 接続成功 (Port: {port})")
                    break
                except Exception as e:
                    # 【修正3】失敗時は即座にセッションを閉じてリソース解放
                    close_camera_connection(mycam)
                    mycam = None # Reset
                    
                    # 認証エラー系は即時記録
                    if "401" in str(e) or "Unauthorized" in str(e):
                        logger.warning(f"⚠️ [{cam_name}] Port {port} 認証失敗: パスワードかユーザー名が違います。")
                    continue
            
            if current_port is None:
                raise Exception(f"全ポート({port_candidates})で接続に失敗しました")

            # --- 監視セットアップ ---
            event_service = mycam.create_events_service()
            subscription = event_service.CreatePullPointSubscription()

            # ==========================================
            # 作成したサブスクリプションをグローバルリストに登録し、終了時にUnsubscribeできるようにする
            active_subscriptions.append(subscription)
            logger.info(f"✅ [{cam_name}] Subscription登録完了 (Cleanup対象)")
            # ==========================================

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
            
            # 【重要】ここではまだエラーカウンタをリセットしない
            # イベント受信が安定して初めてリセットする仕様に変更

            # --- イベント受信ループ ---
            error_count = 0
            success_pull_count = 0 # 成功回数カウンタ

            while True:
                try:
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    
                    # 【修正4】イベント取得成功数をカウントし、安定してからリセット
                    success_pull_count += 1
                    if success_pull_count >= 5 and consecutive_conn_errors > 0:
                        logger.info(f"🎉 [{cam_name}] 接続が完全に安定しました(Count Reset)")
                        consecutive_conn_errors = 0
                        has_notified_error = False
                    
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

                                if img:
                                    try:
                                        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                                        filename = f"snapshot_{cam_conf['id']}_{ts}.jpg"
                                        save_path = os.path.join(ASSETS_DIR, filename)
                                        with open(save_path, "wb") as f: f.write(img)
                                    except Exception: pass
                                
                                common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                    ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                    (common.get_now_iso(), "防犯カメラ", cam_conf['id'], "ONVIF Camera", event_type))
                                
                                is_car_related = "vehicle" in event_type or "Vehicle" in str(rule_name) or event_type == "intrusion"
                                if is_car_related:
                                    action = "UNKNOWN"
                                    if any(k in rule_name for k in config.CAR_RULE_KEYWORDS["LEAVE"]): action = "LEAVE"
                                    elif any(k in rule_name for k in config.CAR_RULE_KEYWORDS["RETURN"]): action = "RETURN"
                                    
                                    if action != "UNKNOWN":
                                        logger.info(f"🚗 車両判定: {action}")
                                        common.save_log_generic(config.SQLITE_TABLE_CAR,
                                            ["timestamp", "action", "rule_name"],
                                            (common.get_now_iso(), action, rule_name))

                                if event_type == "intrusion":
                                    msg = f"🚨【緊急】[{cam_loc}] {cam_name} に侵入者です！"
                                    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=img, target="discord")
                                    time.sleep(15)

                except Exception as e:
                    err = str(e)
                    if "timed out" in err or "TimeOut" in err: continue
                    
                    error_count += 1
                    logger.warning(f"⚠️ [{cam_name}] イベント受信エラー({error_count}回目): {err}")
                    
                    # 短期的なエラーなら少し待つ
                    time.sleep(2)

                    if error_count >= 5:
                        raise Exception("イベント受信エラー過多により再接続します")

        except Exception as e:
            # === エラー発生時の徹底診断フェーズ ===
            consecutive_conn_errors += 1
            err_msg = str(e)
            
            logger.error(f"❌ [{cam_name}] 接続切断/失敗 ({consecutive_conn_errors}回目): {err_msg}")
            
            # 【修正5】緊急診断は頻度を落とす（3回に1回、かつ3回目以降）
            if consecutive_conn_errors >= 3 and consecutive_conn_errors % 3 == 0:
                perform_emergency_diagnosis(cam_conf['ip'])

            # 【修正6】セッションの確実なクリーンアップ
            close_camera_connection(mycam)
            mycam = None

            # 【修正7】待機時間の指数関数的増加（Exponential Backoff）
            # 30, 60, 120... と倍々で増え、最大10分(600秒)で頭打ち
            wait_time = min(30 * (2 ** (min(consecutive_conn_errors, 6) - 1)), MAX_WAIT_TIME)
            
            # 通知ロジック
            if consecutive_conn_errors == NOTIFY_THRESHOLD and not has_notified_error:
                logger.error(f"❌ [{cam_name}] 接続不能が続いています。待機時間を {wait_time}秒 に拡大します。(Error: {err_msg})")
                has_notified_error = True

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