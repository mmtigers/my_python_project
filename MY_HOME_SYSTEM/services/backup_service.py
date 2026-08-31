import sqlite3
import os
import datetime
import shutil
from pathlib import Path
from typing import Tuple
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
                # ここで通知すると、下の外側except節でも再度通知され二重送信になるため、
                # ログのみ残してメッセージ付きで再送出し、通知は外側の1箇所に一本化する。
                logger.error(f"❌ NASディレクトリ作成失敗: {e}")
                raise OSError(f"NASディレクトリ作成失敗: {e}") from e

        shutil.copy2(temp_path, nas_final_path)

        # 転送確認
        if nas_final_path.exists() and os.path.getsize(nas_final_path) == os.path.getsize(temp_path):
            os.remove(temp_path)
            logger.info(f"✅ Backup successfully transferred to NAS: {nas_final_path}")
            _backup_config_files(nas_backup_dir, timestamp, src_db_path)
            return True, "バックアップ完了", local_size_mb
        else:
            raise OSError("NAS転送後の整合性確認に失敗しました。")

    except Exception as e:
        error_msg = f"バックアッププロセス異常終了: {str(e)}"
        _notify_and_log_error(error_msg)
        if temp_path.exists():
            os.remove(temp_path)
        # #248: shutil.copy2()がNAS側の容量不足・切断等でコピー途中に失敗した場合、
        # または転送後の整合性確認(サイズ比較)に失敗した場合、NAS側には書きかけ・
        # 破損した不完全なファイル(nas_final_path)がそのまま残置されていた。
        # ローカルの一時ファイルと同様に、NAS側の不完全なファイルも削除を試みる。
        # 削除自体の失敗(NASが切断されている等)でこの例外処理全体が中断しない
        # よう、個別にtry-exceptで保護する。
        if nas_final_path.exists():
            try:
                os.remove(nas_final_path)
            except OSError as cleanup_err:
                logger.error(f"❌ NAS側の不完全なバックアップファイルの削除に失敗: {cleanup_err}")
        return False, str(e), 0.0

def _backup_config_files(nas_backup_dir: Path, timestamp: str, src_db_path: str) -> None:
    """config.BACKUP_FILES に列挙された設定ファイル(DB以外)をNASへコピーする。

    DBエントリ(src_db_path)は上のPhase 1/2で既にバックアップ済みのためスキップする。
    個々のファイルのコピー失敗はDBバックアップ自体の成否には影響させず、ログのみ残す。
    """
    for entry in getattr(config, "BACKUP_FILES", []):
        if entry == src_db_path:
            continue
        src_path = entry if os.path.isabs(entry) else os.path.join(config.BASE_DIR, entry)
        if not os.path.exists(src_path):
            logger.warning(f"⚠️ バックアップ対象ファイルが見つかりません: {src_path}")
            continue
        src_path_obj = Path(src_path)
        dest_path = nas_backup_dir / f"{src_path_obj.stem}_{timestamp}{src_path_obj.suffix}"
        try:
            shutil.copy2(src_path, dest_path)
            logger.info(f"✅ 設定ファイルをバックアップしました: {src_path} -> {dest_path}")
        except OSError as e:
            logger.error(f"❌ 設定ファイルのバックアップ失敗 ({src_path}): {e}")

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