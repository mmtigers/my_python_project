# HOME_SYSTEM/backup_database.py
import os
import shutil
from datetime import datetime, timedelta
import sys
import common
import config

# ロガー設定
logger = common.setup_logging("backup")
BACKUP_DIR = os.path.join(common.config.BASE_DIR, "db_backup")

def delete_old_backups(days_to_keep=30):
    """古いバックアップファイルを削除する"""
    logger.info(f"--- 古いバックアップの整理 ({days_to_keep}日以前) ---")
    now = datetime.now()
    deleted_count = 0
    
    if not os.path.exists(BACKUP_DIR):
        return

    for filename in os.listdir(BACKUP_DIR):
        file_path = os.path.join(BACKUP_DIR, filename)
        # ファイルかどうか確認
        if not os.path.isfile(file_path):
            continue
            
        # タイムスタンプを確認
        file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
        if now - file_time > timedelta(days=days_to_keep):
            try:
                os.remove(file_path)
                logger.info(f"削除: {filename}")
                deleted_count += 1
            except Exception as e:
                logger.error(f"削除失敗 {filename}: {e}")
    
    if deleted_count > 0:
        logger.info(f"合計 {deleted_count} 個の古いファイルを削除しました。")

def run_backup():
    logger.info("--- バックアップ開始 ---")
    
    if not os.path.exists(BACKUP_DIR):
        try:
            os.makedirs(BACKUP_DIR)
        except OSError as e:
            logger.error(f"フォルダ作成失敗: {e}")
            return

    # 1. バックアップ実行
    target_files = getattr(config, "BACKUP_FILES", [])
    success_count = 0
    total_size = 0
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for file_name in target_files:
        source_path = os.path.join(common.config.BASE_DIR, file_name) if not os.path.isabs(file_name) else file_name
        
        if not os.path.exists(source_path):
            logger.warning(f"元ファイルなし: {file_name}")
            continue

        name_only, ext = os.path.splitext(os.path.basename(file_name))
        backup_name = f"{name_only}_{timestamp}{ext}"
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        try:
            shutil.copy2(source_path, backup_path)
            logger.info(f"[OK] {os.path.basename(file_name)} -> {backup_name}")
            success_count += 1
            total_size += os.path.getsize(backup_path)
        except Exception as e:
            logger.error(f"コピー失敗: {e}")

    # 2. 古いファイルの掃除
    delete_old_backups(days_to_keep=30)

    # 3. 通知
    msg = f"📦 バックアップ完了\n成功: {success_count}ファイル\n容量: {total_size/1024:.1f} KB"
    logger.info(msg)
    
    # 成功時もDiscord/LINEに通知 (Discord推奨)
    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord")

if __name__ == "__main__":
    run_backup()