import sqlite3
import config
import common

def migrate_bounty_table():
    print(f"📦 Migrating Bounty Table to {config.SQLITE_DB_PATH}...")
    
    with common.get_db_cursor(commit=True) as cur:
        # init_unified_db.py と同じSQLを実行
        cur.execute('''
            CREATE TABLE IF NOT EXISTS bounties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                reward_gold INTEGER DEFAULT 0,
                reward_exp INTEGER DEFAULT 0,
                target_type TEXT NOT NULL,
                target_user_id TEXT,
                status TEXT DEFAULT 'OPEN',
                created_by TEXT NOT NULL,
                assignee_id TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME,
                completed_at DATETIME
            )
        ''')
        print("✅ 'bounties' table created successfully.")

if __name__ == "__main__":
    migrate_bounty_table()