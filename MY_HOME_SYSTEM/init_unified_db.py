# HOME_SYSTEM/init_unified_db.py
import sqlite3
import config

def init_db():
    print(f"[INFO] データベース '{config.SQLITE_DB_PATH}' を初期化します...")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cur = conn.cursor()

    # 1. センサー
    cur.execute(f'''
        CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_SENSOR} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            device_name TEXT NOT NULL,
            device_id TEXT NOT NULL,
            device_type TEXT NOT NULL,
            power_watts REAL,
            temperature_celsius REAL,
            humidity_percent REAL,
            contact_state TEXT,
            movement_state TEXT,
            brightness_state TEXT,
            hub_onoff TEXT,
            cam_onoff TEXT,
            threshold_watts REAL
        )
    ''')
    # 2. おはよう
    cur.execute(f'''
        CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_OHAYO} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT,
            message TEXT,
            timestamp TEXT NOT NULL,
            recognized_keyword TEXT
        )
    ''')
    # 3. 食事記録
    cur.execute(f'''
        CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_FOOD} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT,
            meal_date TEXT NOT NULL,
            meal_time_category TEXT NOT NULL,
            menu_category TEXT NOT NULL,
            timestamp DATETIME NOT NULL
        )
    ''')
    conn.commit()
    conn.close()
    print("[SUCCESS] 全テーブルの準備が完了しました。")

if __name__ == "__main__":
    init_db()