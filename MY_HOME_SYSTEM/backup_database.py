# MY_HOME_SYSTEM/backup_database.py
import os
import shutil
import zipfile
import datetime
import glob
import logging
import config
import common
import sqlite3

# ログ設定
logger = common.setup_logging("backup")

# バックアップ保存先 (プロジェクトの親ディレクトリ/backups)
if hasattr(config, "NAS_PROJECT_ROOT"):
    BACKUP_DIR = os.path.join(config.NAS_PROJECT_ROOT, "backups")
else:
    BACKUP_DIR = "/mnt/nas/home_system/backups"

# 保持する世代数 (最新7日分)
KEEP_GENERATIONS = 7

def perform_backup():
    """
    DBと設定ファイルをZIP圧縮してバックアップする (画像は除外)
    """

    # ★ 1. まずDBを綺麗にする
    vacuum_db()
    logger.info("📦 バックアップ処理を開始します (軽量版)...")
    


    # 保存先ディレクトリ作成
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)

    # バックアップファイル名の決定
    today_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_filename = f"backup_db_{today_str}.zip"
    zip_filepath = os.path.join(BACKUP_DIR, zip_filename)

    try:
        # ZIP作成
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            
            # 1. データベース (最重要)
            if os.path.exists(config.SQLITE_DB_PATH):
                logger.info("  - Database archiving...")
                zipf.write(config.SQLITE_DB_PATH, arcname="home_system.db")
            
            # 2. 設定ファイル (重要)
            # 復旧時に最低限必要なファイルをバックアップ
            target_files = ["config.py", ".env", "family_events.json"]
            for f_name in target_files:
                f_path = os.path.join(config.BASE_DIR, f_name)
                if os.path.exists(f_path):
                    zipf.write(f_path, arcname=f_name)

            # ※ 画像 (assets/snapshots) は容量削減のため除外しました

        # ファイルサイズ確認
        size_mb = os.path.getsize(zip_filepath) / (1024 * 1024)
        logger.info(f"✅ バックアップ完了: {zip_filename} ({size_mb:.2f} MB)")

        # ローテーション実行 (古いファイルを削除)
        _rotate_backups()
        
        return True, zip_filename, size_mb

    except Exception as e:
        logger.error(f"❌ バックアップ失敗: {e}")
        # 失敗したら作りかけのファイルを消す
        if os.path.exists(zip_filepath):
            os.remove(zip_filepath)
        return False, str(e), 0

def vacuum_db():
    """DBを最適化(VACUUM)してファイルサイズを圧縮する"""
    db_path = config.SQLITE_DB_PATH
    if not os.path.exists(db_path):
        return

    logger.info("🧹 データベースの最適化(VACUUM)を開始します...")
    try:
        # common.pyのヘルパーを使わず、直接排他接続して実行
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        conn.close()
        logger.info("✨ 最適化完了")
    except Exception as e:
        logger.error(f"⚠️ VACUUM失敗（バックアップは継続します）: {e}")

def _rotate_backups():
    """古いバックアップを削除して世代管理する"""
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_db_*.zip")))
    
    if len(files) > KEEP_GENERATIONS:
        # 古い順に削除
        files_to_delete = files[:-KEEP_GENERATIONS]
        for f in files_to_delete:
            try:
                os.remove(f)
                logger.info(f"🗑️ 古いバックアップを削除: {os.path.basename(f)}")
            except Exception as e:
                logger.warning(f"削除失敗 {f}: {e}")

if __name__ == "__main__":
    # テスト実行用
    perform_backup()