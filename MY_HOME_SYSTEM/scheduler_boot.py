# MY_HOME_SYSTEM/scheduler.py
import time
import subprocess
import sys
import os
from datetime import datetime
from typing import List, Dict, Any, TypedDict

# プロジェクトルートへのパス解決
PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))
sys.path.append(PROJECT_ROOT)

import config
from core.logger import setup_logging

# ロガー設定
logger = setup_logging("scheduler")

class Task(TypedDict):
    """実行タスクのデータ構造定義。"""
    script: str
    interval: int
    last_run: float
    args: List[str]

# === 設定: 定期実行するスクリプトと間隔(秒) ===
# 基本設計書およびこれまでのリファクタリング内容に基づき構成
TASKS: List[Task] = [
    # 頻度: 高 (5分〜10分)
    {"script": "monitors/switchbot_power_monitor.py", "interval": 300,  "last_run": 0, "args": []},
    {"script": "monitors/nature_remo_monitor.py",     "interval": 300,  "last_run": 0, "args": []},
    {"script": "monitors/car_presence_checker.py",    "interval": 600,  "last_run": 0, "args": []},
    {"script": "monitors/server_watchdog.py",         "interval": 600,  "last_run": 0, "args": []},

    # 頻度: 中 (30分)
    {"script": "monitors/bicycle_parking_monitor.py", "interval": 1800, "last_run": 0, "args": ["--save"]},
    {"script": "monitors/clinic_monitor.py",          "interval": 1800, "last_run": 0, "args": []},
    # 頻度: 中 (5分ごとにチェックし、スクリプト内で17:30条件を満たせば処理するラッパー想定、
    # もしくはここで直接時刻判定を行う。今回は一番シンプルな5分毎起動→スクリプト内で時刻判定を想定)
    # ※ただし、より確実にするため、ここでは300秒間隔で起動させます。
    # スクリプト内で `if not (17 <= now.hour <= 18): exit()` のような制御を入れるか、
    # 以下のタスク定義でラッパースクリプトを指定します。
    {"script": "monitors/timelapse_runner.py", "interval": 300, "last_run": 0, "args": []},

    # 頻度: 低 (1時間〜)
    {"script": "monitors/nas_monitor.py",             "interval": 3600, "last_run": 0, "args": []},
    # {"script": "monitors/suumo_monitor.py",           "interval": 3600, "last_run": 0, "args": []},
    {"script": "weekly_analyze_report.py",            "interval": 3600, "last_run": 0, "args": []},
]

def run_script(script_path: str, args: List[str]) -> bool:
    """
    指定されたスクリプトをサブプロセスとして実行する。
    
    Args:
        script_path (str): 実行するスクリプトの相対パス
        args (List[str]): スクリプトに渡す引数
        
    Returns:
        bool: 実行成功(returncode 0)ならTrue
    """
    full_path: str = os.path.join(PROJECT_ROOT, script_path)
    
    if not os.path.exists(full_path):
        logger.error(f"❌ Script not found: {full_path}")
        return False

    logger.info(f"▶️ Executing: {script_path} {' '.join(args)}")
    
    # 子プロセスがプロジェクトのモジュールを読めるよう PYTHONPATH を設定
    env: Dict[str, str] = os.environ.copy()
    env["PYTHONPATH"] = PROJECT_ROOT

    try:
        # 実行完了を待機
        result = subprocess.run(
            [sys.executable, full_path] + args,
            env=env,
            capture_output=True,
            text=True,
            timeout=900  # 1タスク最大5分のタイムアウト
        )

        if result.returncode == 0:
            logger.info(f"✅ Finished: {script_path}")
            return True
        else:
            logger.error(f"⚠️ Task failed [{script_path}] (Exit code: {result.returncode})")
            if result.stderr:
                logger.error(f"Stderr: {result.stderr.strip()}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"⏰ Timeout: {script_path} exceeded 300 seconds.")
        return False
    except Exception as e:
        logger.exception(f"🔥 Unexpected error running {script_path}: {e}")
        return False

def main() -> None:
    """メインループ。"""
    logger.info("⏰ --- MY_HOME_SYSTEM Scheduler Started ---")
    
    while True:
        now: float = time.time()
        
        for task in TASKS:
            # 実行タイミングの判定
            if now - task["last_run"] >= task["interval"]:
                run_script(task["script"], task["args"])
                task["last_run"] = now

        # CPU負荷軽減のための短いスリープ
        time.sleep(10)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("👋 Scheduler stopped by user.")
    except Exception as e:
        logger.critical(f"💀 Scheduler crashed: {e}", exc_info=True)
        sys.exit(1)