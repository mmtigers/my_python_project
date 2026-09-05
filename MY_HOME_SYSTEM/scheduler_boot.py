# MY_HOME_SYSTEM/scheduler.py
import collections
import time
import signal
import subprocess
import sys
import os
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import List, Dict, Optional, TypedDict

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

    # 頻度: 中 (5分) — #411 品質: 実値(interval=300秒=5分)と乖離していた「30分」表記を訂正
    {"script": "monitors/tv_lock_monitor.py",         "interval": 300,  "last_run": 0, "args": []},
    # {"script": "monitors/timelapse_runner.py", "interval": 300, "last_run": 0, "args": []},
    # 頻度: 中 (10分 = 600秒)
    {"script": "monitors/memory_monitor.py",          "interval": 600,  "last_run": 0, "args": []},

    # 頻度: 低 (1時間〜)
    {"script": "monitors/nas_monitor.py",             "interval": 3600, "last_run": 0, "args": []},
]

# #360: 実行中の子プロセス(監視スクリプト)を追跡する。以前は SIGTERM を受けると
# scheduler 本体だけが即死し、実行中の nas_monitor.py 等(最大3600s)が孤児として
# 走り続け、再起動後の新世代と DB 書き込み・保持期間削除が競合していた。
_running_children: Dict[str, subprocess.Popen] = {}
_children_lock = threading.Lock()
_shutdown_event = threading.Event()


def terminate_running_children(timeout: float = 5.0) -> int:
    """実行中の子プロセスを terminate(→timeout後 kill)し、停止した数を返す。"""
    with _children_lock:
        children = list(_running_children.items())
    stopped = 0
    for script, proc in children:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                stopped += 1
                logger.info(f"🛑 Stopped child task: {script}")
        except Exception as e:
            logger.warning(f"Failed to stop child task {script}: {e}")
    return stopped


def _handle_shutdown_signal(signum, _frame) -> None:
    logger.info(f"Received signal {signum}; shutting down scheduler and child tasks...")
    _shutdown_event.set()
    terminate_running_children()


def install_signal_handlers() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_shutdown_signal)
        except (ValueError, OSError):
            # メインスレッド以外から呼ばれた場合等は無視(テスト環境など)
            pass


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

    # 子プロセスがプロジェクトのモジュールを読めるよう PYTHONPATH を設定。
    # #411 S-L5: 以前は既存のPYTHONPATH(start_all.sh等が設定した値)を無条件に
    # 上書きしていた。呼出元の設定を残しつつPROJECT_ROOTを優先させるため先頭に追記する。
    env: Dict[str, str] = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else PROJECT_ROOT
    )

    proc = None
    stderr_tail: "collections.deque[str]" = collections.deque(maxlen=20)
    drain_thread: Optional[threading.Thread] = None

    def _drain_stderr(pipe) -> None:
        # #411 S-L5: 以前は proc.communicate() でstdout/stderrをタスク完了まで
        # 全量メモリに保持していた(最大1時間分の出力を保持しうる)。ログ用途は
        # 末尾20行のみで十分なため、別スレッドで1行ずつ読みながら固定長dequeにのみ
        # 保持し、メモリ使用量を出力量に依存させないようにする。
        try:
            for line in pipe:
                stderr_tail.append(line.rstrip("\n"))
        finally:
            pipe.close()

    try:
        # #360: subprocess.run ではなく Popen で起動して PID を保持し、SIGTERM 受信時に
        # terminate_running_children() から止められるようにする。
        # stdoutは元々破棄するだけなのでDEVNULLに出し、パイプバッファ詰まりを避ける。
        proc = subprocess.Popen(
            [sys.executable, full_path] + args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        with _children_lock:
            _running_children[script_path] = proc

        drain_thread = threading.Thread(target=_drain_stderr, args=(proc.stderr,), daemon=True)
        drain_thread.start()

        # 実行完了を待機
        proc.wait(timeout=3600)  # タイムラプスなど長時間タスクを許容するため60分
        drain_thread.join(timeout=5)

        if proc.returncode == 0:
            logger.debug(f"✅ Finished: {script_path}")
            return True
        else:
            logger.error(f"⚠️ Task failed [{script_path}] (Exit code: {proc.returncode})")
            if stderr_tail:
                # #361: Discord 通知は 2000 字上限のため、stderr は末尾 20 行程度に絞る
                tail = "\n".join(stderr_tail)
                logger.error(f"Stderr (tail): {tail}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"⏰ Timeout: {script_path} exceeded 3600 seconds.")
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        return False
    except Exception as e:
        logger.exception(f"🔥 Unexpected error running {script_path}: {e}")
        return False
    finally:
        with _children_lock:
            if _running_children.get(script_path) is proc:
                _running_children.pop(script_path, None)

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
    install_signal_handlers()

    in_flight: Dict[str, Future] = {}

    with ThreadPoolExecutor(max_workers=max(len(TASKS), 1), thread_name_prefix="scheduler") as executor:
        while not _shutdown_event.is_set():
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

            # CPU負荷軽減のための短いスリープ(シャットダウン要求があれば即抜ける)
            _shutdown_event.wait(10)

    terminate_running_children()
    logger.info("⏰ --- Scheduler stopped ---")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("👋 Scheduler stopped by user.")
    except Exception as e:
        logger.critical(f"💀 Scheduler crashed: {e}", exc_info=True)
        sys.exit(1)