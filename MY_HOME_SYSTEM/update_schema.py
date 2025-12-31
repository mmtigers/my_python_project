# MY_HOME_SYSTEM/update_schema.py
import os
import sys
import sqlite3

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

def update_db_schema():
    print("🛠️ Database Schema Update for Phase 2 (Medals)...")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cur = conn.cursor()
    
    # quest_users テーブルに medal_count カラムを追加
    try:
        cur.execute("ALTER TABLE quest_users ADD COLUMN medal_count INTEGER DEFAULT 0")
        print("✅ Added 'medal_count' column to quest_users.")
    except Exception as e:
        # すでに存在する場合はエラーになるので無視
        print(f"ℹ️ 'medal_count' column check: {e}")

    conn.commit()
    conn.close()
    print("🏁 Update finished.")

if __name__ == "__main__":
    update_db_schema()