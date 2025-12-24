# HOME_SYSTEM/init_unified_db.py
import sqlite3
import config
import common

logger = common.setup_logging("init_db")

def init_db():
    logger.info(f"データベース初期化: {config.SQLITE_DB_PATH}")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cur = conn.cursor()

    # 既存のテーブル定義...
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

    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_CAR} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        rule_name TEXT,
        timestamp DATETIME NOT NULL
    )''')

    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_CHILD} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        user_name TEXT,
        child_name TEXT,
        condition TEXT,
        timestamp DATETIME NOT NULL
    )''')

    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_DEFECATION} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        user_name TEXT,
        record_type TEXT,
        condition TEXT,
        note TEXT,
        timestamp DATETIME NOT NULL
    )''')

    # ▼【追加】AIレポート保存用テーブル
    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_AI_REPORT} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT,          -- 生成されたメッセージ本文
        timestamp DATETIME NOT NULL
    )''')

    # ▼【追加】購入履歴テーブル (重複防止のため email_id に UNIQUE 制約)
    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_SHOPPING} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT,       -- Amazon / Rakuten
        order_date TEXT,     -- 注文日 (YYYY-MM-DD)
        item_name TEXT,      -- 商品名（件名から抜粋）
        price INTEGER,       -- 金額
        email_id TEXT UNIQUE,-- GmailのMessage-ID (重複登録防止)
        timestamp DATETIME NOT NULL
    )''')   

    # ▼【追加】散髪履歴テーブル
    cur.execute(f'''CREATE TABLE IF NOT EXISTS haircut_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT,       -- HotPepperBeauty など
        visit_date TEXT,     -- 来店日時 (YYYY-MM-DD HH:MM)
        shop_name TEXT,      -- 店名
        menu TEXT,           -- メニュー内容 (カットなど)
        price INTEGER,       -- 金額
        email_id TEXT UNIQUE,-- 重複防止
        timestamp DATETIME NOT NULL
    )''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS weather_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,       -- YYYY-MM-DD
            min_temp REAL,          -- 最低気温
            max_temp REAL,          -- 最高気温
            weather_desc TEXT,      -- 天気記述
            recorded_at TEXT
        )
    ''')
    logger.info("✅ weather_history テーブル準備完了")
    
    # 2. 防犯ログテーブル (ダッシュボード表示用)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS security_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            device_name TEXT,
            classification TEXT,    -- person, vehicle, intrusion
            image_path TEXT,        -- 画像ファイルのパス
            recorded_at TEXT
        )
    ''')
    logger.info("✅ security_logs テーブル準備完了")


    # ▼【追加】駐輪場待機数レコード
    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_BICYCLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        area_name TEXT,      -- エリア名（例：阪急伊丹駅前地下 Aブロック）
        status_text TEXT,    -- 取得した状態テキスト（例：5人待ち、空きあり）
        waiting_count INTEGER, -- 待機人数（数値抽出、空きなら0）
        timestamp DATETIME NOT NULL
    )''')
    logger.info("✅ bicycle_parking_records テーブル準備完了")


    conn.commit()
    conn.close()
    logger.info("全テーブルの準備が完了しました。")

if __name__ == "__main__":
    init_db()