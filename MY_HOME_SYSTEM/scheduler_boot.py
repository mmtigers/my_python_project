# MY_HOME_SYSTEM/scheduler.py
import time
import subprocess
import sys
import os
from concurrent.futures import ThreadPoolExecutor, Future
from typing import List, Dict, TypedDict

# プロジェクトルートへのパス解決
PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))
sys.path.append(PROJECT_ROOT)

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
    {"script": "monitors/server_watchdog.py",         "interval": 600,  "last_run": 0, "args": []},

    # 頻度: 中 (30分)
    {"script": "monitors/tv_lock_monitor.py",         "interval": 300,  "last_run": 0, "args": []},
    # {"script": "monitors/timelapse_runner.py", "interval": 300, "last_run": 0, "args": []},
    # 頻度: 中 (10分 = 600秒)
    {"script": "monitors/memory_monitor.py",          "interval": 600,  "last_run": 0, "args": []},

    # 頻度: 低 (1時間〜)
    {"script": "monitors/nas_monitor.py",             "interval": 3600, "last_run": 0, "args": []},
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

    logger.debug(f"▶️ Executing: {script_path} {' '.join(args)}")
    
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
            timeout=3600  # タイムラプスなど長時間タスクを許容するため60分に延長
        )

        if result.returncode == 0:
            logger.debug(f"✅ Finished: {script_path}")
            return True
        else:
            logger.error(f"⚠️ Task failed [{script_path}] (Exit code: {result.returncode})")
            if result.stderr:
                logger.error(f"Stderr: {result.stderr.strip()}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"⏰ Timeout: {script_path} exceeded 3600 seconds.")
        return False
    except Exception as e:
        logger.exception(f"🔥 Unexpected error running {script_path}: {e}")
        return False

def main() -> None:
    """
    メインループ。

    各タスクは ThreadPoolExecutor 上で並列実行する。
    直列実行だと1タスク（例: 長時間かかるNAS監視）がブロックしている間、
    server_watchdog 等の重要な監視タスクまで丸ごと遅延してしまうため。
    同一タスクが実行中の間は、そのタスクだけ次回実行をスキップして
    多重起動（前回実行が長引いた際の連続再実行）を防ぐ。
    """
    logger.info("⏰ --- MY_HOME_SYSTEM Scheduler Started (Parallel Mode) ---")

    in_flight: Dict[str, Future] = {}

    with ThreadPoolExecutor(max_workers=max(len(TASKS), 1), thread_name_prefix="scheduler") as executor:
        while True:
            now: float = time.time()

            for task in TASKS:
                script = task["script"]

                # 前回実行がまだ完了していなければ、今回はスキップして詰まりを防ぐ
                running_future = in_flight.get(script)
                if running_future is not None and not running_future.done():
                    continue

                # 実行タイミングの判定
                if now - task["last_run"] >= task["interval"]:
                    task["last_run"] = now
                    in_flight[script] = executor.submit(run_script, script, task["args"])

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