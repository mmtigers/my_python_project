# MY_HOME_SYSTEM/scheduler.py
import time
import subprocess
import sys
import logging
from datetime import datetime
import common  # 既存のlogging設定を利用

# ロガー設定
logger = common.setup_logging("scheduler")

# === 設定: 定期実行するスクリプトと間隔(秒) ===
TASKS = [
    # 頻度: 高 (5分〜10分)
    {"script": "switchbot_power_monitor.py", "interval": 300,  "last_run": 0}, # 5分: 電源・家電監視
    {"script": "nature_remo_monitor.py",     "interval": 300,  "last_run": 0}, # 5分: Nature Remo 監視
    {"script": "car_presence_checker.py",    "interval": 600,  "last_run": 0}, # 10分: 車の有無 (画像解析)
    {"script": "server_watchdog.py",         "interval": 600,  "last_run": 0}, # 10分: サーバー死活監視

    # 頻度: 中 (30分)
    {"script": "bicycle_parking_monitor.py", "interval": 1800, "last_run": 0}, # 30分: 駐輪場空き状況

    # 頻度: 低 (1時間〜)
    {"script": "nas_monitor.py",             "interval": 3600, "last_run": 0}, # 60分: NAS容量・Ping監視
    {"script": "haircut_monitor.py",         "interval": 3600, "last_run": 0}, # 60分: 散髪予約メール確認
]

def run_script(script_name):
    """サブプロセスとしてスクリプトを実行"""
    try:
        # 現在のPythonインタプリタを使用
        cmd = [sys.executable, script_name]
        logger.info(f"▶️ Task Start: {script_name}")
        
        # 実行 (完了を待つ)
        start_time = time.time()
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            check=False
        )
        duration = time.time() - start_time

        if result.returncode == 0:
            logger.info(f"✅ Task Success: {script_name} ({duration:.1f}s)")
        else:
            logger.error(f"❌ Task Failed: {script_name} (Code: {result.returncode})\nError:\n{result.stderr}")
            
    except Exception as e:
        logger.error(f"🔥 Scheduler Error ({script_name}): {e}")

def main():
    logger.info("🚀 System Scheduler Started (Season 5)")
    logger.info(f"📋 Registered Tasks: {len(TASKS)}")

    # 初回実行の分散を防ぐため、起動直後は少し待機しても良いが、
    # ここでは即時計測を開始し、次回以降intervalに従う単純ループとする
    
    try:
        while True:
            current_time = time.time()
            
            for task in TASKS:
                # 経過時間をチェック
                if current_time - task["last_run"] >= task["interval"]:
                    run_script(task["script"])
                    task["last_run"] = time.time()
            
            # CPU負荷軽減
            time.sleep(10)

    except KeyboardInterrupt:
        logger.info("🛑 Scheduler Stopped by User")
    except Exception as e:
        logger.critical(f"💀 Scheduler Crashed: {e}")

if __name__ == "__main__":
    main()