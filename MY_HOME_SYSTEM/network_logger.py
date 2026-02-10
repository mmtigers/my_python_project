import asyncio
import csv
import datetime
import os
import sys
import time
from typing import Dict, Any, List, Optional

# プロジェクトルートのパス設定
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

# 正規のコンフィグとロガーの読み込み
import config
from core.logger import setup_logging

# --- Constants ---
CHECK_INTERVAL = 60  # 監視サイクル (秒)
RTSP_PORT = 554      # RTSP標準ポート
HTTP_TIMEOUT = 3.0   # 接続タイムアウト (秒)
STARTUP_DELAY = 30   # 起動後待機時間 (秒)
PING_RETRY_COUNT = 3 # Ping再試行回数

# ログ設定
logger = setup_logging("network_monitor")
CSV_FILE = os.path.join(config.LOG_DIR, "network_stats.csv")

# CSVヘッダー定義
CSV_HEADERS = [
    "Timestamp", "Camera_Name", "IP_Address",
    "Ping_Status", "Ping_Latency_ms",
    "Port_RTSP_Status", "Port_RTSP_Latency_ms",
    "App_Layer_Status", "App_Layer_Latency_ms",
    "Error_Detail"
]


async def ping_host(ip: str) -> Dict[str, Any]:
    """ICMP Pingを実行し、到達確認とレイテンシ計測を行います。

    Args:
        ip (str): 対象のIPアドレス。

    Returns:
        Dict[str, Any]: 結果辞書 (status, latency, error)。
    """
    start_time = time.perf_counter()
    try:
        # Linuxシステムのpingコマンドを使用 (-c 1: 1回, -W 1: タイムアウト1秒)
        process = await asyncio.create_subprocess_exec(
            'ping', '-c', '1', '-W', '1', ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        end_time = time.perf_counter()

        return_code = process.returncode
        duration_ms = (end_time - start_time) * 1000

        if return_code == 0:
            return {
                "status": "OK",
                "latency": round(duration_ms, 2),
                "error": ""
            }
        else:
            return {
                "status": "NG",
                "latency": 0.0,
                "error": "Unreachable"
            }
    except Exception as e:
        logger.error(f"Ping execution failed for {ip}: {e}")
        return {"status": "ERROR", "latency": 0.0, "error": str(e)}


async def check_tcp_port(ip: str, port: int) -> Dict[str, Any]:
    """指定されたポートへのTCP接続（ハンドシェイク）を試行します。

    Args:
        ip (str): 対象IPアドレス。
        port (int): 対象ポート番号。

    Returns:
        Dict[str, Any]: 結果辞書 (status, latency)。
    """
    start_time = time.perf_counter()
    writer = None
    try:
        # 接続試行
        future = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(future, timeout=HTTP_TIMEOUT)
        
        end_time = time.perf_counter()
        duration_ms = (end_time - start_time) * 1000

        return {
            "status": "OPEN",
            "latency": round(duration_ms, 2)
        }
    except asyncio.TimeoutError:
        return {"status": "TIMEOUT", "latency": 0.0}
    except ConnectionRefusedError:
        return {"status": "REFUSED", "latency": 0.0}
    except OSError:
        return {"status": "ERROR", "latency": 0.0}
    except Exception as e:
        logger.error(f"TCP check failed for {ip}:{port} - {e}")
        return {"status": "ERROR", "latency": 0.0}
    finally:
        # 明示的なリソース解放
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def monitor_camera(cam_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """個別のカメラデバイスに対する監視タスクを実行します。

    Args:
        cam_config (Dict[str, Any]): config.CAMERAS から取得した設定辞書。

    Returns:
        Optional[Dict[str, Any]]: ログ保存用の結果データ。設定不備の場合はNone。
    """
    # 堅牢性: キーが存在しない場合のフォールバック
    name = cam_config.get("name", "Unknown_Camera")
    ip = cam_config.get("ip")

    if not ip:
        logger.warning(f"Skipping camera config with missing IP: {cam_config}")
        return None

    error_details: List[str] = []
    
    # 1. Ping Check (with Retry)
    ping_data = {"status": "UNKNOWN", "latency": 0.0, "error": "Init"}
    
    for _ in range(PING_RETRY_COUNT):
        ping_data = await ping_host(ip)
        if ping_data["status"] == "OK":
            break
        await asyncio.sleep(2)  # Retry interval

    if ping_data["status"] != "OK":
        error_details.append(f"Ping:{ping_data.get('error', 'Fail')}")

    # 2. RTSP Port Check
    # Pingが通った場合のみ実行
    rtsp_data = {"status": "-", "latency": 0.0}
    if ping_data["status"] == "OK":
        rtsp_data = await check_tcp_port(ip, RTSP_PORT)
        if rtsp_data["status"] != "OPEN":
            error_details.append(f"RTSP:{rtsp_data['status']}")
    else:
        error_details.append("RTSP:Skipped")

    # 結果の集約
    has_error = len(error_details) > 0
    
    return {
        "Timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Camera_Name": name,
        "IP_Address": ip,
        "Ping_Status": ping_data["status"],
        "Ping_Latency_ms": f"{ping_data['latency']:.1f}",
        "Port_RTSP_Status": rtsp_data["status"],
        "Port_RTSP_Latency_ms": f"{rtsp_data['latency']:.1f}",
        "App_Layer_Status": "-",  # 現状は未使用のためプレースホルダ
        "App_Layer_Latency_ms": "0",
        "Error_Detail": "; ".join(error_details) if has_error else ""
    }


def init_csv() -> None:
    """ログファイルが存在しない場合、ヘッダーを作成して初期化します。"""
    try:
        # ディレクトリがない場合は作成（念のため）
        os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)

        if not os.path.exists(CSV_FILE):
            with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADERS)
            logger.info(f"Created new network log file: {CSV_FILE}")
    except Exception as e:
        logger.critical(f"Failed to initialize CSV file: {e}")
        # CSVが作れない場合でもプロセス自体は止めない（ログのみ出力）


async def main() -> None:
    """メイン監視ループ。"""
    logger.info(f"⏳ Network Monitor starting... waiting for system warm-up ({STARTUP_DELAY}s).")
    await asyncio.sleep(STARTUP_DELAY)
    
    # ループ開始前にCSV初期化を実行 (Bug Fix)
    init_csv()
    
    logger.info("🚀 Network Monitor started.")

    while True:
        try:
            # 設定ファイルの再読み込みが必要な場合はここでハンドリング可能だが、現在は起動時のみ
            if not getattr(config, "CAMERAS", None):
                logger.warning("No cameras defined in config.CAMERAS. Sleeping...")
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            tasks = [monitor_camera(cam) for cam in config.CAMERAS]
            results = await asyncio.gather(*tasks)

            # 有効な結果のみ抽出
            valid_results = [res for res in results if res is not None]

            if valid_results:
                # CSVへの追記
                try:
                    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
                        writer.writerows(valid_results)
                except Exception as e:
                    logger.error(f"Failed to write to CSV: {e}")

                # 異常検知時のログ出力
                for res in valid_results:
                    if res.get("Error_Detail"):
                        logger.warning(
                            f"Instability detected for {res['Camera_Name']} ({res['IP_Address']}): "
                            f"{res['Error_Detail']}"
                        )

        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)

        # 次のサイクルまで待機
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Network Logger stopped by user.")