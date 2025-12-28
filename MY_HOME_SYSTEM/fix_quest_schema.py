import sqlite3
import config

def fix_schema():
    print(f"Connecting to {config.SQLITE_DB_PATH}...")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    
    # 古い定義や重複の可能性があるテーブルを一度削除します
    tables_to_drop = [
        "quest_users", 
        "quest_tasks",   # 旧仕様
        "quest_status",  # 旧仕様
        "quest_rewards", # 旧仕様
        "quest_master",  # 新仕様も念のためリセットして整合性を取る
        "reward_master"
    ]
    
    for table in tables_to_drop:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            print(f"Dropped table: {table}")
        except Exception as e:
            print(f"Error dropping {table}: {e}")
            
    conn.commit()
    conn.close()
    print("✅ Cleanup complete. Please restart 'unified_server.py' to recreate tables with correct schema.")

if __name__ == "__main__":
    fix_schema()