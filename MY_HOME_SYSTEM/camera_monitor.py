# HOME_SYSTEM/camera_monitor.py
from onvif import ONVIFCamera
from onvif.client import ONVIFService
from requests.auth import HTTPDigestAuth
import requests
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
import pytz

# ==========================================
# 🔧 設定エリア
# ==========================================
# デバッグモード (Trueにすると通信の生ログ(XML)や全イベントを表示します)
# ★うまく動かないときはここを True にしてログを見てください
DEBUG_MODE = True 

# 検知対象のキーワード
TARGET_KEYWORDS = ["Human", "Person", "People"]

# バインディング名 (ONVIF標準)
BINDING_NAME = '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding'

# ==========================================
# 📝 ログ設定
# ==========================================
# 基本ログ設定
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("camera")

# 通信ライブラリ(Zeep)のログ制御
# DEBUG_MODEがTrueなら、カメラとの通信内容(XML)を全て表示する
if DEBUG_MODE:
    logging.getLogger("zeep.transports").setLevel(logging.DEBUG)
else:
    logging.getLogger("zeep").setLevel(logging.ERROR)


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

def check_time_sync(mycam):
    """カメラとラズパイの時刻ズレをチェックする"""
    try:
        dt = mycam.devicemgmt.GetSystemDateAndTime()
        # 簡易的なUTC変換
        cam_time = datetime(
            dt.UTCDateTime.Date.Year, dt.UTCDateTime.Date.Month, dt.UTCDateTime.Date.Day,
            dt.UTCDateTime.Time.Hour, dt.UTCDateTime.Time.Minute, dt.UTCDateTime.Time.Second,
            tzinfo=pytz.utc
        )
        pi_time = datetime.now(pytz.utc)
        diff = abs((cam_time - pi_time).total_seconds())
        
        logger.info(f"🕒 時刻チェック - カメラ: {cam_time}, ラズパイ: {pi_time}, ズレ: {diff:.1f}秒")
        
        if diff > 300: # 5分以上
            logger.error("❌ 致命的: 時刻が大幅にズレています！認証に失敗する可能性があります。")
            logger.error("👉 カメラの設定画面でNTPサーバーを設定するか、手動で時刻を合わせてください。")
    except Exception as e:
        logger.warning(f"時刻チェック失敗(無視して続行): {e}")

def check_detection(message_node):
    """
    受信したメッセージを解析し、検知かどうかを判定する
    戻り値: (is_detected, label, debug_info)
    """
    try:
        # メッセージの実体を取り出す
        raw_element = getattr(message_node, '_value_1', message_node)
        if raw_element is None: return False, None, "Empty Message"
        
        # XMLを文字列化 (デバッグ用・検索用)
        if hasattr(raw_element, 'tag'):
            xml_str = etree.tostring(raw_element, encoding='unicode')
        else:
            xml_str = str(raw_element)

        # --- 判定ロジック ---
        
        # パターン1: TP-Link VIGI 特有の動体検知 (IsMotion)
        # <tt:SimpleItem Name="IsMotion" Value="true"/>
        if 'Name="IsMotion"' in xml_str and 'Value="true"' in xml_str:
            if any(k in xml_str for k in TARGET_KEYWORDS):
                return True, "人物", xml_str
            return True, "動き", xml_str

        # パターン2: 一般的なONVIF動体検知 (MotionAlarm)
        # <tt:SimpleItem Name="State" Value="true"/> ... Name="MotionAlarm"
        if 'Name="MotionAlarm"' in xml_str and ('Value="true"' in xml_str or 'State="true"' in xml_str):
             return True, "動き(Alarm)", xml_str

        # 検知対象外だがデータはある場合
        return False, None, xml_str

    except Exception as e:
        return False, None, f"Parse Error: {e}"

def capture_snapshot(media_service, profile_token):
    """スナップショットを取得 (ONVIF -> RTSPフォールバック)"""
    # 1. ONVIFでURL取得を試みる
    try:
        res = media_service.GetSnapshotUri({'ProfileToken': profile_token})
        uri = res.Uri
        # ダイジェスト認証でダウンロード
        response = requests.get(uri, auth=HTTPDigestAuth(config.CAMERA_USER, config.CAMERA_PASS), timeout=5)
        if response.status_code == 200:
            return response.content
    except Exception:
        # 失敗したらログは出さずにRTSPへ移行
        pass

    # 2. RTSP (FFmpeg) でキャプチャ
    return capture_snapshot_rtsp()

def capture_snapshot_rtsp():
    """FFmpegでRTSPストリームから画像を切り出す"""
    import subprocess
    tmp_path = "/tmp/snapshot.jpg"
    # rtsp://user:pass@ip:554/stream1 (高画質)
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

    while True: # 再接続ループ
        try:
            logger.info("------------------------------------------------")
            logger.info("📡 カメラに接続を開始します...")
            
            # 1. メイン接続
            mycam = ONVIFCamera(config.CAMERA_IP, 80, config.CAMERA_USER, config.CAMERA_PASS, wsdl_dir=wsdl_dir)
            
            # 時刻チェック (トラブル防止の要)
            check_time_sync(mycam)

            # サービスの準備
            event_service = mycam.create_events_service()
            media_service = mycam.create_media_service()
            try:
                media_profile = media_service.GetProfiles()[0].token
            except:
                media_profile = "Profile_1" # フォールバック

            # 2. 購読作成
            # フィルターなしで広く受け取る (TP-Link対策)
            subscription = event_service.CreatePullPointSubscription()
            
            try:
                plp_address = subscription.SubscriptionReference.Address._value_1
            except AttributeError:
                plp_address = subscription.SubscriptionReference.Address
            
            logger.info(f"購読URL取得: {plp_address}")

            # 3. PullPointサービス作成
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
                    # 生存確認 (DEBUGモードでない時だけ . を出す)
                    if not DEBUG_MODE: print(".", end="", flush=True)
                    
                    params = {'Timeout': timedelta(seconds=5), 'MessageLimit': 100}
                    events = pullpoint.PullMessages(params)
                    error_count = 0
                    
                    if hasattr(events, 'NotificationMessage'):
                        for event in events.NotificationMessage:
                            is_detected, label, raw_xml = check_detection(event.Message)
                            
                            if is_detected:
                                logger.info(f"\n🔥 【検知】 {label} - 画像を取得します...")
                                
                                # 画像取得
                                img = capture_snapshot(media_service, media_profile)
                                
                                # DB記録
                                common.save_log_generic(config.SQLITE_TABLE_SENSOR, 
                                    ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                                    (common.get_now_iso(), "防犯カメラ", "VIGI_C540_W", "ONVIF Camera", "detected"))
                                
                                # 通知
                                msg = f"📷【カメラ通知】\n{label}を検知しました！"
                                if common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], image_data=img):
                                    logger.info("通知送信成功")
                                
                                await asyncio.sleep(10)
                                break
                            
                            # DEBUGモードなら、検知しなかったイベントの中身も表示する（原因調査用）
                            elif DEBUG_MODE:
                                logger.debug(f"ℹ️ 無視したイベント: {raw_xml[:200]}...")

                except KeyboardInterrupt: raise
                except Exception as e:
                    # 接続エラー処理
                    err = str(e)
                    if "timed out" in err or "TimeOut" in err: continue
                    
                    if DEBUG_MODE: logger.warning(f"\n通信瞬断: {err}")
                    else: print("!", end="", flush=True)
                    
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