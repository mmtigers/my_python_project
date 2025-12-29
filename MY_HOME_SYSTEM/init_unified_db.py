# HOME_SYSTEM/init_unified_db.py
import sqlite3
import config
import common

logger = common.setup_logging("init_db")

def init_db():
    logger.info(f"データベース初期化: {config.SQLITE_DB_PATH}")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)

    # ★追加: WALモードを有効化 (並列書き込みに強くなる)
    conn.execute("PRAGMA journal_mode=WAL;")
    
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


    # ▼【追加】土地取引価格テーブル
    # 取引ID (trade_id) がAPIから返るため、それをユニークキーとします
    cur.execute(f'''CREATE TABLE IF NOT EXISTS land_price_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id TEXT UNIQUE,     -- APIの取引ID
        prefecture TEXT,          -- 都道府県
        city TEXT,                -- 市区町村
        district TEXT,            -- 町名 (例: 鈴原町)
        type TEXT,                -- 種類 (例: 宅地(土地と建物), 宅地(土地))
        price INTEGER,            -- 取引総額
        area_m2 INTEGER,          -- 面積
        price_per_m2 INTEGER,     -- 平米単価 (計算値またはAPI値)
        transaction_period TEXT,  -- 取引時期 (例: 2024年第3四半期)
        recorded_at DATETIME NOT NULL
    )''')
    
    logger.info("✅ land_price_records テーブル準備完了")


    # ▼【追加】NAS監視記録テーブル
    cur.execute(f'''CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_NAS} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL,
        device_name TEXT,      -- 設定上の名前 (例: BUFFALO LS720D)
        ip_address TEXT,       -- IPアドレス
        status_ping TEXT,      -- 'OK' or 'NG'
        status_mount TEXT,     -- 'OK' or 'NG'
        total_gb INTEGER,      -- 全容量
        used_gb INTEGER,       -- 使用容量
        free_gb INTEGER,       -- 空き容量
        percent REAL           -- 使用率(%)
    )''')
    logger.info("✅ nas_records テーブル準備完了")


    # ----------------------------------------------------------------
    # ▼▼▼ Family Quest テーブル (修正版：最新スキーマに統合) ▼▼▼
    # ----------------------------------------------------------------
    
    # 1. ユーザーマスタ
    cur.execute('''CREATE TABLE IF NOT EXISTS quest_users (
        user_id TEXT PRIMARY KEY,
        name TEXT,
        job_class TEXT,
        level INTEGER DEFAULT 1,
        exp INTEGER DEFAULT 0,
        gold INTEGER DEFAULT 0,
        updated_at DATETIME
    )''')
    
    # 2. クエストマスタ (一本化された最新定義)
    cur.execute('''CREATE TABLE IF NOT EXISTS quest_master (
        quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        quest_type TEXT DEFAULT 'daily',    -- 'daily', 'limited', 'random'
        exp_gain INTEGER DEFAULT 10,
        gold_gain INTEGER DEFAULT 5,
        icon_key TEXT,
        day_of_week TEXT,                   -- '0,1,2,3,4,5,6'
        target_user TEXT DEFAULT 'all',      -- 'all', 'dad', 'mom', 'sun'
        start_date TEXT,                    -- 'YYYY-MM-DD'
        end_date TEXT,                      -- 'YYYY-MM-DD'
        occurrence_chance REAL DEFAULT 1.0   -- 0.0〜1.0
    )''')
    
    # 3. クエスト履歴
    cur.execute('''CREATE TABLE IF NOT EXISTS quest_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        quest_id INTEGER,
        quest_title TEXT,
        exp_earned INTEGER,
        gold_earned INTEGER,
        completed_at DATETIME NOT NULL
    )''')

    # 4. 報酬マスタ (ショップアイテム)
    cur.execute('''CREATE TABLE IF NOT EXISTS reward_master (
        reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        cost_gold INTEGER,
        category TEXT,           -- 'item'(装備), 'consumable'(消耗品/権利)
        icon_key TEXT
    )''')

    # 5. 報酬交換履歴
    cur.execute('''CREATE TABLE IF NOT EXISTS reward_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        reward_id INTEGER,
        reward_title TEXT,
        cost_gold INTEGER,
        redeemed_at DATETIME NOT NULL
    )''')

    # 2. クエストマスタ (タスク定義) の拡張
    # 2. クエストマスタ (タスク定義)
    cur.execute('''CREATE TABLE IF NOT EXISTS quest_master (
        quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        quest_type TEXT DEFAULT 'daily',    -- ★'daily', 'limited', 'random'
        exp_gain INTEGER DEFAULT 10,
        gold_gain INTEGER DEFAULT 5,
        icon_key TEXT,
        day_of_week TEXT,                   -- '0,1,2,3,4'
        target_user TEXT DEFAULT 'all',      -- ★'all', 'dad', 'mom' など
        start_date TEXT,                    -- ★期間限定用
        end_date TEXT,                      -- ★期間限定用
        occurrence_chance REAL DEFAULT 1.0   -- ★ランダム出現確率
    )''')

    logger.info("✅ Quest RPG テーブル準備完了")


    conn.commit()
    conn.close()
    logger.info("全テーブルの準備が完了しました。")

if __name__ == "__main__":
    init_db()