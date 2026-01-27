import sqlite3
import os
import datetime
import shutil
import time
from pathlib import Path
from typing import Tuple
from common import setup_logging
# 設計書 (Source: 137) に従い core.logger を使用
from core.logger import setup_logging
import config

# ロガー設定
logger = setup_logging("backup")

def perform_backup() -> Tuple[bool, str, float]:
    """
    データベースのバックアップを実行する。
    
    【根治策】
    NASへの直接バックアップはファイルロック(CIFS)の問題でハングするため、
    1. ローカル(一時領域)にバックアップを作成
    2. 完成したファイルをNASへ転送
    という2段階方式を採用する。

    Returns:
        Tuple[bool, str, float]: (成功フラグ, メッセージ, バックアップサイズMB)
    """
    src_db_path = config.SQLITE_DB_PATH
    
    # タイムスタンプ生成
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"home_system_{timestamp}.db"
    
    # パス設定
    # 1. 一時保存先 (/tmp または アプリ内tmp)
    temp_dir = Path(config.BASE_DIR) / "temp_backups"
    temp_path = temp_dir / filename
    
    # 2. 最終保存先 (NAS)
    nas_root = getattr(config, "NAS_PROJECT_ROOT", os.path.join(config.NAS_MOUNT_POINT, "home_system"))
    nas_backup_dir = Path(nas_root) / "db_backups"
    nas_final_path = nas_backup_dir / filename

    logger.info("🚀 Starting Robust Backup Process")
    
    try:
        # --- Phase 1: Local Backup (Fast & Safe) ---
        logger.info("Phase 1: Creating local snapshot...")
        os.makedirs(temp_dir, exist_ok=True)
        
        # 既存DBへの接続
        with sqlite3.connect(src_db_path) as src_conn:
            # ローカルファイルへの接続 (ロック問題なし)
            with sqlite3.connect(str(temp_path)) as dst_conn:
                # バックアップ実行
                src_conn.backup(dst_conn, pages=-1)
        
        local_size_bytes = os.path.getsize(temp_path)
        local_size_mb = local_size_bytes / (1024 * 1024)
        logger.info(f"✅ Local backup created: {temp_path} ({local_size_mb:.2f} MB)")

        # --- Phase 2: Transfer to NAS ---
        logger.info("Phase 2: Transferring to NAS...")
        
        # NASディレクトリ確認 (なければ作る)
        if not nas_backup_dir.exists():
            try:
                os.makedirs(nas_backup_dir, exist_ok=True)
            except OSError as e:
                logger.warning(f"Failed to create NAS dir: {e}. Checking if exists...")

        # コピー実行
        shutil.copy2(temp_path, nas_final_path)
        
        # 転送確認
        if nas_final_path.exists() and os.path.getsize(nas_final_path) == local_size_bytes:
            logger.info(f"✅ Transfer successful: {nas_final_path}")
            
            # --- Phase 3: Cleanup ---
            os.remove(temp_path)
            logger.info("🗑️ Local temp file cleaned up.")
            
            return True, "バックアップ完了", local_size_mb
            
        else:
            raise OSError("Transfer verification failed (Size mismatch or file missing)")

    except Exception as e:
        logger.exception(f"❌ Backup failed: {e}")
        # エラー時は一時ファイルが残っていたら消す
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except Exception:
                pass
        return False, str(e), 0.0
    finally:
        # 空の一時ディレクトリなら消しておく
        try:
            if temp_dir.exists() and not os.listdir(temp_dir):
                os.rmdir(temp_dir)
        except Exception:
            pass

if __name__ == "__main__":
    perform_backup()