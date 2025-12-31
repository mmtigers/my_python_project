# HOME_SYSTEM/init_unified_db.py
import sqlite3
import config
import common

logger = common.setup_logging("init_db")

def init_db():
    """
    アプリケーションで使用する全SQLiteテーブルを初期化する。
    重複定義を防ぐため IF NOT EXISTS を使用。
    """
    logger.info(f"データベース初期化開始: {config.SQLITE_DB_PATH}")

    # common.get_db_cursor を使用してトランザクション管理を統一
    # commit=True により、コンテキストを抜ける際に自動コミットされる
    with common.get_db_cursor(commit=True) as cur:
        
        # WALモード有効化 (Performance tuning)
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
            logger.info("✅ WALモードを設定しました")
        except Exception as e:
            logger.warning(f"⚠️ WALモードの設定に失敗しました (無視可能です): {e}")

        # --- IoT & Sensor Data ---
        
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_SENSOR} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                timestamp DATETIME NOT NULL, 
                device_name TEXT, 
                device_id TEXT, 
                device_type TEXT,
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
        
        # --- Logs & Records ---

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_OHAYO} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id TEXT, 
                user_name TEXT, 
                message TEXT, 
                timestamp TEXT, 
                recognized_keyword TEXT
            )
        ''')
        
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_FOOD} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id TEXT, 
                user_name TEXT, 
                meal_date TEXT, 
                meal_time_category TEXT, 
                menu_category TEXT, 
                timestamp DATETIME
            )
        ''')
        
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_DAILY} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id TEXT, 
                user_name TEXT, 
                date TEXT, 
                category TEXT, 
                value TEXT, 
                timestamp DATETIME
            )
        ''')
    
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_HEALTH} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_name TEXT, 
                status TEXT, 
                note TEXT, 
                timestamp DATETIME
            )
        ''')

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_CAR} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT,
                rule_name TEXT,
                timestamp DATETIME NOT NULL
            )
        ''')

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_CHILD} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                user_name TEXT,
                child_name TEXT,
                condition TEXT,
                timestamp DATETIME NOT NULL
            )
        ''')

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_DEFECATION} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                user_name TEXT,
                record_type TEXT,
                condition TEXT,
                note TEXT,
                timestamp DATETIME NOT NULL
            )
        ''')

        # --- AI & External Services ---

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_AI_REPORT} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT,
                timestamp DATETIME NOT NULL
            )
        ''')

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_SHOPPING} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                order_date TEXT,
                item_name TEXT,
                price INTEGER,
                email_id TEXT UNIQUE,
                timestamp DATETIME NOT NULL
            )
        ''')   

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS haircut_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                visit_date TEXT,
                shop_name TEXT,
                menu TEXT,
                price INTEGER,
                email_id TEXT UNIQUE,
                timestamp DATETIME NOT NULL
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS weather_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                min_temp REAL,
                max_temp REAL,
                weather_desc TEXT,
                recorded_at TEXT
            )
        ''')
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS security_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                device_name TEXT,
                classification TEXT,
                image_path TEXT,
                recorded_at TEXT
            )
        ''')

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_BICYCLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                area_name TEXT,
                status_text TEXT,
                waiting_count INTEGER,
                timestamp DATETIME NOT NULL
            )
        ''')

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS land_price_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE,
                prefecture TEXT,
                city TEXT,
                district TEXT,
                type TEXT,
                price INTEGER,
                area_m2 INTEGER,
                price_per_m2 INTEGER,
                transaction_period TEXT,
                recorded_at DATETIME NOT NULL
            )
        ''')

        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_NAS} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                device_name TEXT,
                ip_address TEXT,
                status_ping TEXT,
                status_mount TEXT,
                total_gb INTEGER,
                used_gb INTEGER,
                free_gb INTEGER,
                percent REAL
            )
        ''')

        # --- Family Quest RPG System ---
        
        # Note: 元のコードには重複定義がありましたが、IF NOT EXISTSにより
        # 最初の定義(user_id TEXT)が優先される仕様でした。
        # ここでは実際に有効だった定義のみを記述し、重複を除去しています。

        # 1. ユーザーマスタ
        cur.execute('''
            CREATE TABLE IF NOT EXISTS quest_users (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                job_class TEXT,
                level INTEGER DEFAULT 1,
                exp INTEGER DEFAULT 0,
                gold INTEGER DEFAULT 0,
                medal_count INTEGER DEFAULT 0,
                avatar TEXT DEFAULT '🙂', 
                updated_at DATETIME
            )
        ''')

        # 2. クエストマスタ
        cur.execute('''
            CREATE TABLE IF NOT EXISTS quest_master (
                quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                quest_type TEXT DEFAULT 'daily',
                exp_gain INTEGER DEFAULT 10,
                gold_gain INTEGER DEFAULT 5,
                icon_key TEXT,
                day_of_week TEXT,
                target_user TEXT DEFAULT 'all',
                start_date TEXT,
                end_date TEXT,
                occurrence_chance REAL DEFAULT 1.0
            )
        ''')
        
        # 3. クエスト履歴
        cur.execute('''
            CREATE TABLE IF NOT EXISTS quest_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                quest_id INTEGER,
                quest_title TEXT,
                exp_earned INTEGER,
                gold_earned INTEGER,
                completed_at DATETIME NOT NULL
            )
        ''')

        # 4. 報酬マスタ
        cur.execute('''
            CREATE TABLE IF NOT EXISTS reward_master (
                reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                cost_gold INTEGER,
                category TEXT,
                icon_key TEXT
            )
        ''')

        # 5. 報酬交換履歴
        cur.execute('''
            CREATE TABLE IF NOT EXISTS reward_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                reward_id INTEGER,
                reward_title TEXT,
                cost_gold INTEGER,
                redeemed_at DATETIME NOT NULL
            )
        ''')


        # 6. 装備マスタ
        cur.execute('''
            CREATE TABLE IF NOT EXISTS equipment_master (
                equipment_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT,  -- weapon / armor
                power INTEGER,
                cost_gold INTEGER,
                icon_key TEXT
            )
        ''')

        # 7. ユーザー所有装備 & 装備状態
        # is_equipped: 1=装備中, 0=所持のみ
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_equipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                equipment_id INTEGER,
                is_equipped INTEGER DEFAULT 0,
                acquired_at DATETIME,
                UNIQUE(user_id, equipment_id)
            )
        ''')


        # 8. パーティ状態管理 (ボスバトル用) ★追加
        cur.execute("""
            CREATE TABLE IF NOT EXISTS party_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_boss_id INTEGER DEFAULT 1,
                current_hp INTEGER DEFAULT 0,
                charge_gauge INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """)

        # --- Legacy / Unused Definitions ---
        # 以下のテーブルは元のスクリプトで定義されていましたが、現在の主要ロジックでは
        # おそらく使用されていません。しかし、後方互換性(Zero Regression)のため定義を残します。

        cur.execute("""
            CREATE TABLE IF NOT EXISTS quest_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                points INTEGER DEFAULT 0,
                target_user_id INTEGER,
                FOREIGN KEY (target_user_id) REFERENCES quest_users(id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS quest_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                date TEXT NOT NULL,
                is_completed INTEGER DEFAULT 1,
                FOREIGN KEY (task_id) REFERENCES quest_tasks(id)
            )
        """)

    logger.info("✅ 全テーブルの準備が完了しました。")

if __name__ == "__main__":
    init_db()