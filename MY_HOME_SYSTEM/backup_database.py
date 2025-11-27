# HOME_SYSTEM/backup_database.py
import os
import shutil
from datetime import datetime
import sys

# 設定読み込み
try:
    import config
except ImportError:
    print("[FATAL] config.py が見つかりません。")
    sys.exit(1)

# バックアップ先フォルダ (HOME_SYSTEM/db_backup)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, "db_backup")

def run_backup():
    print(f"\n--- バックアップ開始 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    # 1. バックアップフォルダ作成
    if not os.path.exists(BACKUP_DIR):
        try:
            os.makedirs(BACKUP_DIR)
            print(f"[INFO] フォルダ作成: {BACKUP_DIR}")
        except OSError as e:
            print(f"[ERROR] フォルダ作成失敗: {e}")
            return

    # 2. ファイルコピー実行
    # config.py で定義されたファイルを対象にする
    target_files = getattr(config, "BACKUP_FILES", [])
    
    if not target_files:
        print("[WARN] config.BACKUP_FILES が設定されていません。")
        return

    success_count = 0
    for file_name in target_files:
        source_path = os.path.join(BASE_DIR, file_name)
        
        if not os.path.exists(source_path):
            print(f"[SKIP] 元ファイルなし: {file_name}")
            continue

        # ファイル名に日時をつける (例: home_system_20251126_180000.db)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name_only, ext = os.path.splitext(file_name)
        backup_name = f"{name_only}_{timestamp}{ext}"
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        try:
            shutil.copy2(source_path, backup_path)
            print(f"[OK] {file_name} -> {backup_name}")
            success_count += 1
        except Exception as e:
            print(f"[ERROR] コピー失敗 ({file_name}): {e}")

    print(f"--- バックアップ完了 (成功: {success_count} / 対象: {len(target_files)}) ---\n")

if __name__ == "__main__":
    run_backup()