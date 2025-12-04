# HOME_SYSTEM/backup_database.py
import os
import shutil
from datetime import datetime
import sys
import common
import config

logger = common.setup_logging("backup")
BACKUP_DIR = os.path.join(common.config.BASE_DIR, "db_backup")

def run_backup():
    logger.info("--- バックアップ開始 ---")
    
    if not os.path.exists(BACKUP_DIR):
        try:
            os.makedirs(BACKUP_DIR)
            logger.info(f"フォルダ作成: {BACKUP_DIR}")
        except OSError as e:
            logger.error(f"フォルダ作成失敗: {e}")
            return

    target_files = getattr(config, "BACKUP_FILES", [])
    success_count = 0
    
    for file_name in target_files:
        source_path = os.path.join(common.config.BASE_DIR, file_name) if not os.path.isabs(file_name) else file_name
        
        if not os.path.exists(source_path):
            logger.warning(f"元ファイルなし: {file_name}")
            continue

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name_only, ext = os.path.splitext(os.path.basename(file_name))
        backup_name = f"{name_only}_{timestamp}{ext}"
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        try:
            shutil.copy2(source_path, backup_path)
            logger.info(f"[OK] {os.path.basename(file_name)} -> {backup_name}")
            success_count += 1
        except Exception as e:
            logger.error(f"コピー失敗: {e}")

    logger.info(f"--- バックアップ完了 (成功: {success_count} / 対象: {len(target_files)}) ---")

if __name__ == "__main__":
    run_backup()