import sqlite3
import os

DB_PATH = "home_system.db"

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"❌ Error: Database file not found at: {os.path.abspath(DB_PATH)}")
        return

    print(f"Connecting to: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    target_table = "quest_master"

    try:
        # テーブル存在確認
        cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{target_table}';")
        if not cur.fetchone():
            print(f"❌ Error: Table '{target_table}' does not exist.")
            return

        # 既存カラム取得
        cur.execute(f"PRAGMA table_info({target_table})")
        columns = [info[1] for info in cur.fetchall()]
        
        # 1. days カラムの追加
        if 'days' not in columns:
            cur.execute(f"ALTER TABLE {target_table} ADD COLUMN days TEXT")
            print(f"✅ Column 'days' added to {target_table}.")
        else:
            print(f"ℹ️ Column 'days' already exists.")

        # 2. description カラムの追加
        if 'description' not in columns:
            cur.execute(f"ALTER TABLE {target_table} ADD COLUMN description TEXT")
            print(f"✅ Column 'description' added to {target_table}.")
        else:
            print(f"ℹ️ Column 'description' already exists.")

    except sqlite3.OperationalError as e:
        print(f"⚠️ SQLite Error: {e}")
    finally:
        conn.commit()
        conn.close()

if __name__ == "__main__":
    migrate()