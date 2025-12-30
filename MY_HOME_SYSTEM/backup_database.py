# MY_HOME_SYSTEM/backup_database.py
import os
import shutil
import zipfile
import datetime
import glob
import logging
import sqlite3
import config
import common

logger = common.setup_logging("backup")

if hasattr(config, "NAS_PROJECT_ROOT"):
    BACKUP_DIR = os.path.join(config.NAS_PROJECT_ROOT, "backups")
else:
    BACKUP_DIR = "/mnt/nas/home_system/backups"

KEEP_GENERATIONS = 7

def _safe_db_copy(src_path: str, dst_path: str):
    """
    稼働中のSQLite DBを安全にコピーする (Online Backup API使用)
    """
    if not os.path.exists(src_path):
        return False

    src_conn = None
    dst_conn = None
    try:
        # 読み取り元 (既存DB)
        src_conn = sqlite3.connect(src_path)
        # 書き込み先 (一時ファイル)
        dst_conn = sqlite3.connect(dst_path)
        
        # バックアップ実行
        with src_conn:
            src_conn.backup(dst_conn)
            
        return True
    except Exception as e:
        logger.error(f"DB Online Backup Error: {e}")
        return False
    finally:
        if dst_conn: dst_conn.close()
        if src_conn: src_conn.close()

def perform_backup():
    """
    DBと設定ファイルをZIP圧縮してバックアップする
    """
    # サーバー稼働中にVACUUMするとロック待ちでタイムアウトするリスクがあるため
    # 定期バックアップでは除外するか、別途メンテナンスモードで行うことを推奨
    # vacuum_db() 
    
    logger.info("📦 バックアップ処理を開始します...")

    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)

    today_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_filename = f"backup_db_{today_str}.zip"
    zip_filepath = os.path.join(BACKUP_DIR, zip_filename)
    
    # 一時的なDBコピー先
    temp_db_name = f"temp_home_system_{today_str}.db"
    temp_db_path = os.path.join(BACKUP_DIR, temp_db_name)

    try:
        # 1. 安全なDBコピーを作成
        if os.path.exists(config.SQLITE_DB_PATH):
            logger.info("  - Creating safe database snapshot...")
            if not _safe_db_copy(config.SQLITE_DB_PATH, temp_db_path):
                raise Exception("DB snapshot failed")
        
        # 2. ZIP作成
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # スナップショットを 'home_system.db' という名前で格納
            if os.path.exists(temp_db_path):
                zipf.write(temp_db_path, arcname="home_system.db")
            
            # 設定ファイル
            target_files = ["config.py", ".env", "family_events.json"]
            for f_name in target_files:
                f_path = os.path.join(config.BASE_DIR, f_name)
                if os.path.exists(f_path):
                    zipf.write(f_path, arcname=f_name)

        # ファイルサイズ確認
        size_mb = os.path.getsize(zip_filepath) / (1024 * 1024)
        logger.info(f"✅ バックアップ完了: {zip_filename} ({size_mb:.2f} MB)")

        # 後始末 (一時ファイルの削除)
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)

        # ローテーション
        _rotate_backups()
        
        return True, zip_filename, size_mb

    except Exception as e:
        logger.error(f"❌ バックアップ失敗: {e}")
        # ゴミ掃除
        if os.path.exists(zip_filepath):
            os.remove(zip_filepath)
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
        return False, str(e), 0

def _rotate_backups():
    # ... (変更なし) ...
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_db_*.zip")))
    if len(files) > KEEP_GENERATIONS:
        files_to_delete = files[:-KEEP_GENERATIONS]
        for f in files_to_delete:
            try:
                os.remove(f)
                logger.info(f"🗑️ 古いバックアップを削除: {os.path.basename(f)}")
            except Exception as e:
                logger.warning(f"削除失敗 {f}: {e}")

if __name__ == "__main__":
    perform_backup()