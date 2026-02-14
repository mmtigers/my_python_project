import sqlite3
import os
import datetime
import shutil
import time
from pathlib import Path
from typing import Tuple
from common import setup_logging
# 設計書 (Source: 137) に従い core.logger を使用
from core.logger import setup_logging  # 設計書に従い core.logger を使用 [cite: 137, 354]
from common import send_push           # 通知用ユーティリティ
import config

# ロガー設定
logger = setup_logging("backup")

def perform_backup() -> Tuple[bool, str, float]:
    """
    データベースのバックアップを実行し、NASへ転送する。 [cite: 316]
    
    NASへの転送失敗（権限エラー・接続断等）は、管理者の介入が必要な恒久的障害（ERROR）として扱い、
    即時通知を行う。 [cite: 387, 469, 470]

    Returns:
        Tuple[bool, str, float]: (成功フラグ, メッセージ, バックアップサイズMB)
    """
    src_db_path = config.SQLITE_DB_PATH
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"home_system_{timestamp}.db"
    
    # パス設定
    temp_dir = Path(config.BASE_DIR) / "temp_backups"
    temp_path = temp_dir / filename
    nas_root = getattr(config, "NAS_PROJECT_ROOT", os.path.join(config.NAS_MOUNT_POINT, "home_system"))
    nas_backup_dir = Path(nas_root) / "db_backups"
    nas_final_path = nas_backup_dir / filename

    logger.info("🚀 Starting Robust Backup Process")
    
    try:
        # Phase 1: Local Backup (Fast & Safe)
        os.makedirs(temp_dir, exist_ok=True)
        with sqlite3.connect(src_db_path) as src_conn:
            with sqlite3.connect(str(temp_path)) as dst_conn:
                src_conn.backup(dst_conn, pages=-1)
        
        local_size_mb = os.path.getsize(temp_path) / (1024 * 1024)
        logger.info(f"✅ Local backup created: {local_size_mb:.2f} MB")

        # Phase 2: Transfer to NAS
        if not nas_backup_dir.exists():
            try:
                os.makedirs(nas_backup_dir, exist_ok=True)
            except (PermissionError, OSError) as e:
                _notify_and_log_error(f"NASディレクトリ作成失敗: {e}")
                raise

        shutil.copy2(temp_path, nas_final_path)
        
        # 転送確認
        if nas_final_path.exists() and os.path.getsize(nas_final_path) == os.path.getsize(temp_path):
            os.remove(temp_path)
            logger.info(f"✅ Backup successfully transferred to NAS: {nas_final_path}")
            return True, "バックアップ完了", local_size_mb
        else:
            raise OSError("NAS転送後の整合性確認に失敗しました。")

    except Exception as e:
        error_msg = f"バックアッププロセス異常終了: {str(e)}"
        _notify_and_log_error(error_msg)
        if temp_path.exists():
            os.remove(temp_path)
        return False, str(e), 0.0

def _notify_and_log_error(message: str) -> None:
    """ERRORレベルの記録と管理者への即時通知を行う [cite: 361, 387]"""
    logger.error(f"❌ {message}")
    send_push(
        user_id=getattr(config, "LINE_USER_ID", None),
        messages=[{"type": "text", "text": f"🚨 【重要】バックアップ失敗報\n{message}"}],
        target="discord",
        channel="report"
    )

if __name__ == "__main__":
    perform_backup()