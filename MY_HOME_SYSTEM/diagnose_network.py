import socket
import config
import logging
from concurrent.futures import ThreadPoolExecutor

# ロガー設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger("network_diag")

# 調査対象のポート一覧 (ONVIF, HTTP, RTSPでよく使われるもの)
TARGET_PORTS = [
    80, 2020, # HTTP / ONVIF (TP-Link VIGI標準)
    554,      # RTSP (映像ストリーム)
    443,      # HTTPS
    8000, 8080, 8888, # 代替HTTPポート
    1935,     # RTMP
    1024,     # その他予備
]

def check_port(ip, port):
    """ 指定されたIPとポートに接続できるか確認する """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0) # 1秒でタイムアウト
    try:
        result = sock.connect_ex((ip, port))
        if result == 0:
            return True
    except Exception:
        pass
    finally:
        sock.close()
    return False

def scan_camera(cam_conf):
    ip = cam_conf['ip']
    name = cam_conf['name']
    logger.info(f"🚀 [{name}] ネットワーク診断を開始します (IP: {ip})")
    
    # 1. Ping確認 (簡易的)
    # PythonでPingは権限が必要な場合が多いため、socketで代用するか、
    # とりあえずポートスキャンに進みます。

    open_ports = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_port, ip, p): p for p in TARGET_PORTS}
        for future in futures:
            port = futures[future]
            if future.result():
                open_ports.append(port)
                logger.info(f"   ✅ ポート {port} : OPEN (接続成功)")
            else:
                pass
                # logger.debug(f"   ❌ ポート {port} : CLOSED")

    if not open_ports:
        logger.error(f"❌ [{name}] 全てのポートが閉じています (またはIPが間違っています)")
        logger.info(f"   👉 対策: ルーター管理画面で '{ip}' が本当にこのカメラか確認してください。")
    else:
        logger.info(f"✨ [{name}] 診断完了。開いているポート: {open_ports}")
        
        # 推奨設定の提示
        if 2020 in open_ports:
            logger.info("   👉 ONVIFポート '2020' が生きています。config.py はそのままでOKなはずですが、認証エラーの可能性があります。")
        elif 80 in open_ports:
            logger.info("   👉 ポート '80' が生きています。config.py のポートを 2020 -> 80 に変更してください。")
        elif 554 in open_ports:
            logger.info("   👉 RTSP(554)だけ生きています。ONVIF機能が無効になっている可能性があります。カメラのWeb管理画面でONVIFをONにしてください。")

def main():
    if not config.CAMERAS:
        logger.error("config.py にカメラ設定がありません")
        return

    logger.info("🕵️‍♀️ カメラ ネットワーク自動診断ツール")
    logger.info("=========================================")
    
    for cam in config.CAMERAS:
        scan_camera(cam)
        print("-" * 40)

if __name__ == "__main__":
    main()