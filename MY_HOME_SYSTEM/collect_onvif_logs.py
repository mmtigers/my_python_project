# HOME_SYSTEM/collect_onvif_logs.py
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
from lxml import etree
import logging
from concurrent.futures import ThreadPoolExecutor
from http.client import RemoteDisconnected
from urllib3.exceptions import ProtocolError

# === ロガー設定 ===
logger = common.setup_logging("onvif_collector")

# === 設定 ===
LOG_DIR = os.path.join(config.BASE_DIR, "logs")
BINDING_NAME = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'

# ★追加: VIGIカメラ等の強制切断対策 (50秒で自発的に再接続)
SESSION_LIFETIME = 50
# ★追加: エラー通知を抑制する閾値
MAX_RETRY_THRESHOLD = 3

def ensure_log_dir():
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
            logger.info(f"📁 保存用フォルダを作成: {LOG_DIR}")
        except OSError as e:
            logger.error(f"フォルダ作成失敗: {e}")
            return False
    return True

def get_log_filepath(camera_id):
    """カメラIDごとにログファイルを分ける"""
    today = common.get_today_date_str()
    # IDに含まれるかもしれないファイル名に使えない文字を除去
    safe_id = "".join([c for c in camera_id if c.isalnum() or c in ('_', '-')])
    return os.path.join(LOG_DIR, f"onvif_raw_{safe_id}_{today}.log")

def write_to_file(camera_id, text):
    filepath = get_log_filepath(camera_id)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {text}\n")
            f.write("-" * 80 + "\n")
    except Exception as e:
        logger.error(f"[{camera_id}] ファイル書き込み失敗: {e}")

def find_wsdl_path():
    for path in sys.path:
        if 'site-packages' in path and os.path.exists(path):
            candidate = os.path.join(path, 'onvif', 'wsdl')
            if os.path.exists(os.path.join(candidate, 'devicemgmt.wsdl')):
                return candidate
            for root, dirs, files in os.walk(path):
                if 'devicemgmt.wsdl' in files: return root
    return None

def collect_single_camera(cam_conf):
    """1台のカメラのデータを収集するプロセス"""
    cam_name = cam_conf['name']
    cam_ip = cam_conf['ip']
    cam_id = cam_conf['id']
    
    logger.info(f"🚀 [{cam_name}] 収集スレッド起動 ({cam_ip})")
    
    wsdl_dir = find_wsdl_path()
    if not wsdl_dir:
        logger.error("WSDLが見つかりません")
        return

    # 開始通知
    # common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"🎥 {cam_name} のデータ記録を始めます✨"}])

    while True: # 再接続ループ
        try:
            logger.info(f"📡 [{cam_name}] 接続中...")
            
            mycam = ONVIFCamera(cam_ip, 80, cam_conf['user'], cam_conf['pass'], wsdl_dir=wsdl_dir)
            event_service = mycam.create_events_service()
            
            # フィルターなしで全イベント収集
            subscription = event_service.CreatePullPointSubscription()
            
            try:
                plp_address = subscription.SubscriptionReference.Address._value_1
            except AttributeError:
                plp_address = subscription.SubscriptionReference.Address
            
            events_wsdl = os.path.join(wsdl_dir, 'events.wsdl')
            pullpoint = ONVIFService(
                xaddr=plp_address,
                user=cam_conf['user'],
                passwd=cam_conf['pass'],
                url=events_wsdl,
                encrypt=True,
                binding_name=BINDING_NAME
            )
            
            pullpoint.zeep_client.transport.session.auth = HTTPDigestAuth(cam_conf['user'], cam_conf['pass'])
            logger.info(f"✅ [{cam_name}] 記録開始")

            # ★追加: セッション開始時刻
            session_start_time = time.time()

            while True:
                try:
                    # ★追加: 50秒経過チェック (Proactive Refresh)
                    if time.time() - session_start_time > SESSION_LIFETIME:
                        # 正常な再接続フローなのでログは最小限に
                        # logger.info(f"🔄 [{cam_name}] 定期セッション更新 (50s)")
                        try:
                            if hasattr(subscription, 'Unsubscribe'):
                                subscription.Unsubscribe()
                        except Exception: pass
                        break # 内側のループを抜けて再接続へ

                    # ポーリング
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    
                    if hasattr(events, 'NotificationMessage'):
                        for event in events.NotificationMessage:
                            topic = str(event.Topic)
                            
                            # XML文字列化
                            xml_str = "None"
                            message_node = getattr(event, 'Message', None)
                            if message_node:
                                raw_element = getattr(message_node, '_value_1', message_node)
                                if hasattr(raw_element, 'tag'):
                                    xml_str = etree.tostring(raw_element, encoding='unicode', pretty_print=True)
                                else:
                                    xml_str = str(raw_element)

                            # 保存
                            log_content = f"Topic: {topic}\nData:\n{xml_str}"
                            write_to_file(cam_id, log_content)
                                
                except KeyboardInterrupt: raise
                except Exception as e:
                    err = str(e)
                    if "timed out" in err or "TimeOut" in err: continue
                    
                    # その他のエラーは外側のexceptでキャッチさせるために投げる
                    raise e

        except (RemoteDisconnected, ProtocolError, BrokenPipeError, ConnectionResetError) as e:
             # ★修正: 切断エラーは想定内なので WARNING ではなく INFO で扱う
             # logger.info(f"🔄 [{cam_name}] 通信切断(想定内): {e}. 再接続します...")
             time.sleep(1)
             continue

        except KeyboardInterrupt:
            logger.info(f"[{cam_name}] 停止しました。")
            break
        except Exception as e:
            # ★修正: 全てのエラーをここで一元管理し、通知制御を行う
            err_str = str(e)
            
            # 想定内のエラーキーワードリスト
            transient_keywords = [
                'RemoteDisconnected', 
                'Connection aborted', 
                'BrokenPipeError', 
                'ConnectionResetError',
                'Unknown error' # onvifライブラリがラップしたものも含む
            ]
            
            is_transient = any(k in err_str for k in transient_keywords)
            
            consecutive_errors += 1
            
            if is_transient:
                if consecutive_errors >= MAX_RETRY_THRESHOLD:
                    # 3回以上連続したら初めてERRORログを出す
                    logger.error(f"❌ [{cam_name}] 接続エラー(頻発): {e} ({consecutive_errors}/{MAX_RETRY_THRESHOLD})")
                else:
                    # それまではINFO/WARNING程度に留める
                    logger.info(f"🔄 [{cam_name}] 通信切断(再接続中): {consecutive_errors}回目. Error: {e}")
                
                time.sleep(2) # 短い待機で再試行
            
            else:
                # 明らかな予期せぬエラー
                logger.error(f"❌ [{cam_name}] 予期せぬエラー: {e}")
                logger.info(f"[{cam_name}] 10秒後に再試行...")
                time.sleep(10)

async def main():
    if not ensure_log_dir(): return
    
    loop = asyncio.get_running_loop()
    tasks = []
    
    # カメラごとにスレッドを立ち上げる
    with ThreadPoolExecutor(max_workers=len(config.CAMERAS)) as executor:
        for cam in config.CAMERAS:
            tasks.append(loop.run_in_executor(executor, collect_single_camera, cam))
        
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass