# MY_HOME_SYSTEM/update_schema.py
import os
import sys
import sqlite3

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

def update_db_schema():
    print("🛠️ Database Schema Update for Phase 3 (Approval Flow)...")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cur = conn.cursor()
    
    # 1. quest_history テーブルに status カラムを追加
    try:
        # 既存の履歴はすべて 'approved' (承認済み) として扱う
        cur.execute("ALTER TABLE quest_history ADD COLUMN status TEXT DEFAULT 'approved'")
        print("✅ Added 'status' column to quest_history.")
    except Exception as e:
        print(f"ℹ️ 'status' column check: {e}")

    conn.commit()
    conn.close()
    print("🏁 Update finished.")

if __name__ == "__main__":
    update_db_schema()