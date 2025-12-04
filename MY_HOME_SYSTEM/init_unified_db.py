# HOME_SYSTEM/init_unified_db.py
import sqlite3
import config
import common

logger = common.setup_logging("init_db")

def init_db():
    logger.info(f"データベース初期化: {config.SQLITE_DB_PATH}")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cur = conn.cursor()

    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_SENSOR} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME NOT NULL, device_name TEXT, device_id TEXT, device_type TEXT,
        power_watts REAL, temperature_celsius REAL, humidity_percent REAL, contact_state TEXT, movement_state TEXT,
        brightness_state TEXT, hub_onoff TEXT, cam_onoff TEXT, threshold_watts REAL)''')
        
    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_OHAYO} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, user_name TEXT, message TEXT, timestamp TEXT, recognized_keyword TEXT)''')
        
    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_FOOD} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, user_name TEXT, meal_date TEXT, meal_time_category TEXT, menu_category TEXT, timestamp DATETIME)''')
        
    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_DAILY} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, user_name TEXT, date TEXT, category TEXT, value TEXT, timestamp DATETIME)''')
    
    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_HEALTH} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_name TEXT, status TEXT, note TEXT, timestamp DATETIME)''')

    conn.commit()
    conn.close()
    logger.info("全テーブルの準備が完了しました。")

if __name__ == "__main__":
    init_db()