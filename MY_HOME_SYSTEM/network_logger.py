import asyncio
import csv
import os
import sys
import time
import datetime
import subprocess
from typing import Dict, Any, List

# プロジェクトのルートパスを特定して config を読み込めるようにする
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

try:
    import config
    from core.logger import setup_logging
except ImportError:
    # 単体動作確認用フォールバック
    print("Warning: Running without full project context. Using dummy config.")
    class Config:
        LOG_DIR = os.path.join(BASE_DIR, "logs")
        CAMERAS = []
    config = Config()
    import logging
    setup_logging = lambda x: logging.getLogger(x)

# ロガー設定
logger = setup_logging("network_monitor")

# CSVファイルのパス
CSV_FILE = os.path.join(config.LOG_DIR, "network_stats.csv")

# 定数
CHECK_INTERVAL = 60  # 秒
RTSP_PORT = 554
HTTP_TIMEOUT = 3.0

async def ping_host(ip: str) -> Dict[str, Any]:
    """ICMP Pingを実行し、レイテンシとパケットロスを確認する"""
    start_time = time.perf_counter()
    try:
        # Linuxシステムのpingコマンドを使用 (-c 1: 1回, -W 1: タイムアウト1秒)
        process = await asyncio.create_subprocess_exec(
            'ping', '-c', '1', '-W', '1', ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        end_time = time.perf_counter()
        
        return_code = process.returncode
        duration_ms = (end_time - start_time) * 1000

        # pingコマンドの出力から正確なtime=XXmsを抽出することも可能だが、
        # ここではSRE的観点から「コマンド実行にかかった総時間」を簡易レイテンシとする
        
        return {
            "status": "OK" if return_code == 0 else "NG",
            "latency": round(duration_ms, 2) if return_code == 0 else 0.0,
            "error": "" if return_code == 0 else "Unreachable"
        }
    except Exception as e:
        return {"status": "ERROR", "latency": 0.0, "error": str(e)}

async def check_tcp_port(ip: str, port: int) -> Dict[str, Any]:
    """指定されたポートへのTCP接続を試行する"""
    start_time = time.perf_counter()
    try:
        # open_connectionを使ってTCPハンドシェイクにかかる時間を計測
        future = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(future, timeout=3.0)
        
        end_time = time.perf_counter()
        duration_ms = (end_time - start_time) * 1000
        
        writer.close()
        await writer.wait_closed()
        
        return {
            "status": "OPEN",
            "latency": round(duration_ms, 2)
        }
    except asyncio.TimeoutError:
        return {"status": "TIMEOUT", "latency": 0.0}
    except ConnectionRefusedError:
        return {"status": "REFUSED", "latency": 0.0}
    except OSError as e:
        return {"status": "ERROR", "latency": 0.0}
    except Exception as e:
        return {"status": "ERROR", "latency": 0.0}

async def check_http_layer(ip: str, port: int) -> Dict[str, Any]:
    """アプリケーション層（HTTP）の簡易チェック"""
    # 外部ライブラリ(aiohttp)に依存せず、標準ライブラリのみで軽量なチェックを行うため
    # TCP接続後にHEADリクエスト相当のデータを送信してみる簡易実装
    try:
        start_time = time.perf_counter()
        reader, writer = await asyncio.open_connection(ip, port)
        
        # 簡易的なHTTPリクエスト
        request = f"GET / HTTP/1.0\r\nHost: {ip}\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()
        
        # レスポンスの最初の数バイトだけ読んで接続確立を確認
        # 401 Unauthorized等が返ってくればWebサーバーは生きている
        data = await asyncio.wait_for(reader.read(1024), timeout=HTTP_TIMEOUT)
        end_time = time.perf_counter()
        
        writer.close()
        await writer.wait_closed()

        if len(data) > 0:
            return {"status": "ALIVE", "latency": round((end_time - start_time) * 1000, 2)}
        else:
            return {"status": "NO_DATA", "latency": 0.0}
            
    except Exception:
        return {"status": "FAIL", "latency": 0.0}

async def monitor_camera(camera: Dict[str, Any]) -> Dict[str, Any]:
    """1台のカメラに対して全てのチェックを並列実行する"""
    ip = camera.get("ip")
    name = camera.get("name", "Unknown")
    # config.pyで指定されているポート（VIGIの場合は管理用ポートやONVIFポート）
    config_port = camera.get("port", 80)
    
    if not ip:
        return None

    # 並列でチェック実行
    ping_res, rtsp_res, app_res = await asyncio.gather(
        ping_host(ip),
        check_tcp_port(ip, RTSP_PORT),      # Port 554 (RTSP)
        check_http_layer(ip, config_port)   # Application/HTTP check
    )
    
    # ログ詳細の整形
    error_details = []
    if ping_res["error"]: error_details.append(f"Ping:{ping_res['error']}")
    if rtsp_res["status"] != "OPEN": error_details.append(f"RTSP:{rtsp_res['status']}")
    
    return {
        "Timestamp": datetime.datetime.now().isoformat(),
        "Camera_Name": name,
        "IP_Address": ip,
        "Ping_Status": ping_res["status"],
        "Ping_Latency_ms": ping_res["latency"],
        "Port_RTSP_Status": rtsp_res["status"],
        "Port_RTSP_Latency_ms": rtsp_res["latency"],
        "App_Layer_Status": app_res["status"],
        "App_Layer_Latency_ms": app_res["latency"],
        "Error_Detail": "; ".join(error_details)
    }

def init_csv():
    """CSVファイルがなければヘッダーを作成する"""
    if not os.path.exists(CSV_FILE):
        try:
            with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Timestamp", "Camera_Name", "IP_Address", 
                    "Ping_Status", "Ping_Latency_ms", 
                    "Port_RTSP_Status", "Port_RTSP_Latency_ms", 
                    "App_Layer_Status", "App_Layer_Latency_ms",
                    "Error_Detail"
                ])
            logger.info(f"Created new log file: {CSV_FILE}")
        except Exception as e:
            logger.error(f"Failed to create CSV file: {e}")

async def main():
    logger.info("Starting Network Logger for ONVIF Cameras...")
    init_csv()
    
    while True:
        try:
            tasks = []
            for cam in config.CAMERAS:
                tasks.append(monitor_camera(cam))
            
            if not tasks:
                logger.warning("No cameras defined in config.CAMERAS")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            results = await asyncio.gather(*tasks)
            
            # 結果をCSVに書き込み
            with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "Timestamp", "Camera_Name", "IP_Address", 
                    "Ping_Status", "Ping_Latency_ms", 
                    "Port_RTSP_Status", "Port_RTSP_Latency_ms", 
                    "App_Layer_Status", "App_Layer_Latency_ms",
                    "Error_Detail"
                ])
                for res in results:
                    if res:
                        writer.writerow(res)
            
            # 簡易ログ出力 (コンソール/ファイル用)
            for res in results:
                if res and res["Error_Detail"]:
                    logger.warning(f"Instability detected for {res['Camera_Name']}: {res['Error_Detail']}")
                    
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        
        # 次のサイクルまで待機
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Network Logger stopped by user.")