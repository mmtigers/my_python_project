# MY_HOME_SYSTEM/monitors/timelapse_runner.py
import os
import sys
import time
import datetime
import subprocess
import argparse

# プロジェクトルートへのパス解決
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import config
from core.logger import setup_logging

logger = setup_logging("timelapse_runner")

def main():
    # コマンドライン引数の設定
    parser = argparse.ArgumentParser(description="タイムラプス生成ランナー")
    parser.add_argument("--force", action="store_true", help="時刻や実行済みフラグを無視して強制実行する")
    args = parser.parse_args()

    now = datetime.datetime.now()
    
    # 実行条件の判定
    is_target_time = (now.hour == 17 and 30 <= now.minute < 35)
    flag_file = os.path.join(config.LOG_DIR, f"timelapse_{now.strftime('%Y%m%d')}.done")
    
    # 強制実行(--force) または 定時実行の条件を満たした場合
    if args.force or is_target_time:
        if args.force or not os.path.exists(flag_file):
            logger.info(f"⏰ タイムラプス生成を開始します" + (" (手動強制実行)" if args.force else ""))
            
            script_path = os.path.join(PROJECT_ROOT, "monitors", "timelapse_generator.py")
            
            try:
                # 🛡️ 恒久対策: タイムアウトを1800秒(30分)に厳格化。これ以上かかる場合は強制キルしてシステムリソースを開放。
                result = subprocess.run(
                    [sys.executable, script_path],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=1800 
                )
                
                if result.returncode == 0:
                    logger.info("✅ タイムラプス生成が正常に完了しました。")
                    # 定時実行のときのみフラグを作成 (手動テスト時はフラグを作らない)
                    if not args.force:
                        with open(flag_file, "w") as f:
                            f.write(now.isoformat())
                else:
                    logger.error(f"⚠️ タイムラプス生成がエラーを返しました (Exit code: {result.returncode})")
                    if result.stderr:
                        logger.error(f"Stderr: {result.stderr.strip()}")
                        
            except subprocess.TimeoutExpired:
                logger.error("⏰ タイムラプス生成がタイムアウト（10分）しました。")
            except Exception as e:
                logger.exception(f"🔥 予期せぬエラー: {e}")
        else:
            logger.info("ℹ️ 本日のタイムラプスは既に生成済みです。")

if __name__ == "__main__":
    main()