import os
import sys
import sqlite3

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

def update_db_schema():
    print("🛠️ Database Schema Update...")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cur = conn.cursor()
    
    # カラム追加の試行
    try:
        cur.execute("ALTER TABLE quest_master ADD COLUMN start_time TEXT")
        print("✅ Added 'start_time' column.")
    except Exception as e:
        print(f"ℹ️ 'start_time': {e}")

    try:
        cur.execute("ALTER TABLE quest_master ADD COLUMN end_time TEXT")
        print("✅ Added 'end_time' column.")
    except Exception as e:
        print(f"ℹ️ 'end_time': {e}")
        
    conn.commit()
    conn.close()
    print("🏁 Update finished.")

if __name__ == "__main__":
    update_db_schema()