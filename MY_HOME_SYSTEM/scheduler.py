# MY_HOME_SYSTEM/scheduler.py
import time
import subprocess
import sys
import logging
import os  # <--- 追加
from datetime import datetime
import common

# ロガー設定
logger = common.setup_logging("scheduler")

# === 設定: 定期実行するスクリプトと間隔(秒) ===
# 修正: パスを monitors/ 始まりに変更
TASKS = [
    # 頻度: 高 (5分〜10分)
    {"script": "monitors/switchbot_power_monitor.py", "interval": 300,  "last_run": 0},
    {"script": "monitors/nature_remo_monitor.py",     "interval": 300,  "last_run": 0},
    {"script": "monitors/car_presence_checker.py",    "interval": 600,  "last_run": 0},
    {"script": "monitors/server_watchdog.py",         "interval": 600,  "last_run": 0},

    # 頻度: 中 (30分)
    {"script": "monitors/bicycle_parking_monitor.py", "interval": 1800, "last_run": 0},

    # 頻度: 低 (1時間〜)
    {"script": "monitors/nas_monitor.py",             "interval": 3600, "last_run": 0},
    {"script": "monitors/haircut_monitor.py",         "interval": 3600, "last_run": 0},
    # 頻度: 低 (SUUMO監視 - 1時間に1回)
    # config.SUUMO_MONITOR_INTERVAL (3600秒) で設定
    {"script": "monitors/suumo_monitor.py",           "interval": 3600, "last_run": 0},
]

def run_script(script_name):
    """サブプロセスとしてスクリプトを実行"""
    try:
        cmd = [sys.executable, script_name]
        logger.info(f"▶️ Task Start: {script_name}")
        
        # 修正: サブプロセスが親ディレクトリの common.py をimportできるようにする
        current_env = os.environ.copy()
        cwd = os.getcwd()
        current_path = current_env.get("PYTHONPATH", "")
        # 現在のディレクトリをPYTHONPATHの先頭に追加
        current_env["PYTHONPATH"] = f"{cwd}{os.pathsep}{current_path}"

        start_time = time.time()
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            check=False,
            env=current_env  # <--- 環境変数を渡す
        )
        duration = time.time() - start_time

        if result.returncode == 0:
            logger.info(f"✅ Task Success: {script_name} ({duration:.1f}s)")
        else:
            logger.error(f"❌ Task Failed: {script_name} (Code: {result.returncode})\nError:\n{result.stderr}")
            
    except Exception as e:
        logger.error(f"🔥 Scheduler Error ({script_name}): {e}")

def main():
    logger.info("🚀 System Scheduler Started (Season 5 - Refactored)")
    logger.info(f"📋 Registered Tasks: {len(TASKS)}")

    try:
        while True:
            current_time = time.time()
            
            for task in TASKS:
                if current_time - task["last_run"] >= task["interval"]:
                    run_script(task["script"])
                    task["last_run"] = time.time()
            
            time.sleep(10)

    except KeyboardInterrupt:
        logger.info("🛑 Scheduler Stopped by User")
    except Exception as e:
        logger.critical(f"💀 Scheduler Crashed: {e}")

if __name__ == "__main__":
    main()