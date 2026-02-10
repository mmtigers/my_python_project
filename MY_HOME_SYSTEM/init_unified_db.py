# MY_HOME_SYSTEM/init_unified_db.py
import sqlite3
import logging
from typing import List, Dict, Any, Optional
import config
import common

logger = common.setup_logging("init_db")

def validate_schema_integrity(conn: sqlite3.Connection) -> None:
    """
    設計書(3.1)に基づくスキーマ整合性の自動検証を行う。
    主要テーブルのカラム定義が期待通りかチェックする。
    """
    # 検証対象のテーブル定義 (テーブル名: [必須カラムリスト])
    expected_schemas: Dict[str, List[str]] = {
        # New Core Tables
        "users": ["name", "level", "xp", "gold", "status"],
        "quests": ["title", "description", "xp_reward", "gold_reward", "difficulty"],
        config.SQLITE_TABLE_DAILY_LOGS: ["category", "detail", "timestamp"],
        config.SQLITE_TABLE_SWITCHBOT_LOGS: ["device_id", "temperature", "humidity", "timestamp"],
        config.SQLITE_TABLE_POWER_USAGE: ["wattage", "timestamp"],
        
        # Legacy/Existing Tables (Critical for current operation)
        config.SQLITE_TABLE_CHILD: ["child_name", "condition", "timestamp"],
        config.SQLITE_TABLE_FOOD: ["menu_category", "meal_time_category"],
        config.SQLITE_TABLE_DEFECATION: ["record_type", "condition"]
    }

    cur = conn.cursor()
    issues: List[str] = []

    for table, columns in expected_schemas.items():
        try:
            cur.execute(f"PRAGMA table_info({table})")
            existing_cols = [row[1] for row in cur.fetchall()]
            
            if not existing_cols:
                # 初期化前は存在しないのが正常だが、init後に呼ぶ前提
                issues.append(f"Missing Table: {table}")
                continue

            for col in columns:
                if col not in existing_cols:
                    issues.append(f"Table '{table}' missing column '{col}'")
        except Exception as e:
            issues.append(f"Error checking {table}: {e}")

    if issues:
        for issue in issues:
            logger.warning(f"⚠️ Schema Integrity Issue: {issue}")
    else:
        logger.info("✅ Schema Integrity Validation Passed.")

def init_db() -> None:
    """
    アプリケーションで使用する全SQLiteテーブルを初期化する。
    設計書 v1.0.0 (Section 3.2) および既存機能の後方互換性を維持する。
    """
    logger.info(f"データベース初期化開始: {config.SQLITE_DB_PATH}")

    with common.get_db_cursor(commit=True) as cur:
        # WALモード有効化
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
        except Exception as e:
            logger.warning(f"⚠️ WALモード設定失敗: {e}")

        # ==========================================
        # 1. New Core Tables (Design Doc v1.0.0)
        # ==========================================

        # Users (旧: quest_users から移行予定)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE, -- LINE ID等
                name TEXT,
                level INTEGER DEFAULT 1,
                xp INTEGER DEFAULT 0,
                gold INTEGER DEFAULT 0,
                status TEXT DEFAULT '在宅', -- 在宅/外出
                job_class TEXT,
                medal_count INTEGER DEFAULT 0,
                avatar TEXT, 
                updated_at DATETIME
            )
        ''')

        # Quests (旧: quest_master から移行予定)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS quests (
                quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                xp_reward INTEGER DEFAULT 10,
                gold_reward INTEGER DEFAULT 5,
                difficulty INTEGER DEFAULT 1,
                quest_type TEXT DEFAULT 'daily',
                icon_key TEXT,
                start_date TEXT,
                end_date TEXT
            )
        ''')

        # Quest History
        cur.execute('''
            CREATE TABLE IF NOT EXISTS quest_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                quest_id INTEGER,
                quest_title TEXT,
                status TEXT DEFAULT 'approved', -- approved/pending
                completed_at DATETIME NOT NULL, -- completed_at に修正
                exp_earned INTEGER,
                gold_earned INTEGER
            )
        ''')

        # Daily Logs (統合ログ: 生活イベント)
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_DAILY_LOGS} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                category TEXT NOT NULL, -- 排便, 風呂, 睡眠, 食事 etc.
                detail TEXT,
                timestamp DATETIME NOT NULL
            )
        ''')

        # SwitchBot Meter Logs (温湿度分離)
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_SWITCHBOT_LOGS} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                device_name TEXT,
                temperature REAL,
                humidity REAL,
                timestamp DATETIME NOT NULL
            )
        ''')

        # Power Usage (電力ログ分離)
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_POWER_USAGE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                device_name TEXT,
                wattage REAL,
                timestamp DATETIME NOT NULL
            )
        ''')

        # ==========================================
        # 2. Existing / Legacy Tables (Must Keep)
        # ==========================================
        # これらは config.py で定義された定数を使用し、既存コードとの互換性を保ちます。

        # Sensor Data (Old integrated table - for compatibility)
        # config.pyで "device_records" と定義されていたテーブル
        cur.execute('''
            CREATE TABLE IF NOT EXISTS device_records (
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

        # Ohayo (挨拶)
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
        
        # Food (食事)
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
        
        # Daily Records (Old Generic)
        # config.py で SQLITE_TABLE_DAILY と定義されているもの
        cur.execute('''
            CREATE TABLE IF NOT EXISTS daily_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id TEXT, 
                user_name TEXT, 
                date TEXT, 
                category TEXT, 
                value TEXT, 
                timestamp DATETIME
            )
        ''')
    
        # Health (汎用健康)
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_HEALTH} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_name TEXT, 
                status TEXT, 
                note TEXT, 
                timestamp DATETIME
            )
        ''')

        # Car (車検知)
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_CAR} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT,
                rule_name TEXT,
                timestamp DATETIME,
                score REAL
            )
        ''')

        # Child Health (子供体調 - 重要)
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

        # Defecation (排便記録 - 重要)
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

        # AI Report
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_AI_REPORT} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT,
                timestamp DATETIME NOT NULL
            )
        ''')

        # Shopping
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

        # Haircut
        cur.execute('''
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

        # Weather
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
        
        # Security Logs (Images)
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

        # Bicycle Parking
        cur.execute(f'''
            CREATE TABLE IF NOT EXISTS {config.SQLITE_TABLE_BICYCLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                area_name TEXT,
                status_text TEXT,
                waiting_count INTEGER,
                timestamp DATETIME NOT NULL
            )
        ''')

        # Land Price
        cur.execute('''
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

        # NAS Monitoring
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

        # ==========================================
        # 3. Game & Quest System (Legacy/Transitional)
        # ==========================================
        
        # 旧ユーザーテーブル (quest_users)
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

        # 旧クエストマスタ (quest_master)
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
                pre_requisite_quest_id INTEGER,
                occurrence_chance REAL DEFAULT 1.0
            )
        ''')
        
        # 報酬マスタ
        cur.execute('''
            CREATE TABLE IF NOT EXISTS reward_master (
                reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                cost_gold INTEGER,
                category TEXT,
                icon_key TEXT,
                desc TEXT,
                target TEXT DEFAULT 'all'
            )
        ''')

        # 報酬履歴
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

        # 装備マスタ
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

        # ユーザー装備
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

        # ボス戦 (Party State)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS party_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_boss_id INTEGER DEFAULT 1,
                current_hp INTEGER DEFAULT 0,
                max_hp INTEGER DEFAULT 100,
                week_start_date TEXT,
                is_defeated INTEGER DEFAULT 0,
                total_damage INTEGER DEFAULT 0, 
                charge_gauge INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """)

        # インベントリ
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                reward_id INTEGER,
                status TEXT DEFAULT 'owned',
                purchased_at DATETIME NOT NULL,
                used_at DATETIME,
                FOREIGN KEY(reward_id) REFERENCES reward_master(reward_id)
            )
        ''')

        # ギルド掲示板
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

        # SUUMO
        cur.execute('''
            CREATE TABLE IF NOT EXISTS suumo_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id TEXT UNIQUE,
                title TEXT,
                address TEXT,
                rent_price INTEGER,
                url TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    # 検証実行
    try:
        with sqlite3.connect(config.SQLITE_DB_PATH) as conn:
            validate_schema_integrity(conn)
    except Exception as e:
        logger.error(f"Schema Validation Failed: {e}")

    logger.info("✅ 全テーブルの準備・初期化が完了しました。")

if __name__ == "__main__":
    init_db()