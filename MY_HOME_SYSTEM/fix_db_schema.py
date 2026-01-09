import sqlite3
import shutil
import os
import sys
import datetime

# --- Configuration ---
# config.py からパスを読み込もうとしますが、失敗した場合は以下を使用します
DEFAULT_DB_PATH = "/home/masahiro/develop/MY_HOME_SYSTEM/home_system.db"

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import config
    DB_PATH = config.SQLITE_DB_PATH
except ImportError:
    print(f"⚠️ config.py not found or failed to import. Using default path.")
    DB_PATH = DEFAULT_DB_PATH

def create_backup(db_path):
    """データベースのバックアップを作成する"""
    if not os.path.exists(db_path):
        print(f"❌ Error: Database file not found at {db_path}")
        sys.exit(1)
        
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.{timestamp}.bak"
    
    print(f"📦 Creating backup...")
    try:
        shutil.copy2(db_path, backup_path)
        print(f"✅ Backup created: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        sys.exit(1)

def analyze_and_fix(db_path):
    """スキーマ診断と修復を実行する"""
    print(f"\n🔍 Connecting to database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Schema Diagnostic
    try:
        cursor.execute("PRAGMA table_info(users);")
        columns_info = cursor.fetchall()
    except sqlite3.OperationalError as e:
        print(f"❌ Error reading table info: {e}")
        conn.close()
        return

    current_columns = [col['name'] for col in columns_info]
    print(f"📊 Current 'users' columns: {current_columns}")

    if 'id' in current_columns:
        print("\n✅ 'id' column ALREADY EXISTS. No action needed.")
        conn.close()
        return

    print("\n⚠️  MISSING COLUMN DETECTED: 'id' is not in 'users' table.")
    
    # 2. Smart Fix Strategy
    print("\n" + "="*50)
    print("🛠️  REMEDIATION PLANS")
    print("="*50)
    print("SQLite limitation: Cannot add PRIMARY KEY via ALTER TABLE.")
    print("Please choose a strategy:")
    
    print("\n[Plan A] Code Fix (Recommended for Safety)")
    print("   Do NOT change the database schema.")
    print("   Instead, update your SQL queries to use the internal 'rowid'.")
    print("   Example: Change `SELECT id, name...` to `SELECT rowid AS id, name...`")
    
    print("\n[Plan B] Schema Migration (Add Column)")
    print("   Execute: `ALTER TABLE users ADD COLUMN id INTEGER;`")
    print("   Note: This will add a nullable INTEGER column, NOT a PRIMARY KEY.")
    print("   You may need to manually populate IDs afterwards.")

    choice = input("\n👉 Select Plan (A/B) or 'q' to quit: ").strip().upper()

    if choice == 'A':
        print("\n✅ Plan A Selected.")
        print("Action: No DB changes made.")
        print("Please edit `send_ai_report.py` and replace `u.id` with `u.rowid AS id` in your queries.")
    
    elif choice == 'B':
        print("\n✅ Plan B Selected.")
        confirm = input(f"⚠️  Are you sure you want to alter '{db_path}'? (yes/no): ")
        if confirm.lower() == 'yes':
            try:
                # Add column
                cursor.execute("ALTER TABLE users ADD COLUMN id INTEGER;")
                conn.commit()
                print("✅ SQL Executed: ALTER TABLE users ADD COLUMN id INTEGER;")
                
                # Verify
                cursor.execute("PRAGMA table_info(users);")
                new_cols = [col['name'] for col in cursor.fetchall()]
                print(f"📊 New columns: {new_cols}")
                
                if 'id' in new_cols:
                    print("🎉 Fix applied successfully.")
                else:
                    print("❌ Verification failed. Column not found.")
            except Exception as e:
                print(f"❌ Migration failed: {e}")
                conn.rollback()
        else:
            print("🚫 Operation cancelled.")
    
    else:
        print("👋 Exiting without changes.")

    conn.close()

if __name__ == "__main__":
    print("🛡️  DB Schema Fix Tool (DBRE Edition)")
    create_backup(DB_PATH)
    analyze_and_fix(DB_PATH)