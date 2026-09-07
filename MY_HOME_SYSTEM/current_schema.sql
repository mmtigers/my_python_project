-- このファイルは生成物です。手で編集しないでください。
--
-- migrations/ 配下の全マイグレーションを空の SQLite DB へ適用した結果のスキーマ
-- (sqlite_master の CREATE 文を作成順に列挙したもの)で、参照用ドキュメントです。
-- 実行時にはどこからも読み込まれず、スキーマの唯一の定義元は migrations/ です
-- (Issue #330。位置づけの経緯は migrations/README.md の「current_schema.sql との関係」参照)。
--
-- 再生成: MY_HOME_SYSTEM/ で
--   python init_unified_db.py --dump-schema
-- tests/test_current_schema_sql.py が「本ファイル == 再生成結果」を検証するため、
-- マイグレーションを追加・変更した PR では必ず再生成してコミットすること。
CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT UNIQUE, 
    name TEXT,
    level INTEGER DEFAULT 1,
    xp INTEGER DEFAULT 0,
    gold INTEGER DEFAULT 0,
    status TEXT DEFAULT '在宅', 
    job_class TEXT,
    medal_count INTEGER DEFAULT 0,
    avatar TEXT,
    updated_at DATETIME
);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE quests (
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
);
CREATE TABLE quest_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    quest_id INTEGER,
    quest_title TEXT,
    status TEXT DEFAULT 'approved', 
    completed_at DATETIME NOT NULL,
    exp_earned INTEGER,
    gold_earned INTEGER
, linked_history_id INTEGER DEFAULT NULL, medals_earned INTEGER DEFAULT 0);
CREATE TABLE daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    category TEXT NOT NULL, 
    detail TEXT,
    timestamp DATETIME NOT NULL
);
CREATE TABLE switchbot_meter_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    device_name TEXT,
    temperature REAL,
    humidity REAL,
    timestamp DATETIME NOT NULL
);
CREATE TABLE power_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    device_name TEXT,
    wattage REAL,
    timestamp DATETIME NOT NULL
);
CREATE TABLE device_records (
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
, nas_usage_percent REAL);
CREATE TABLE ohayo_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    message TEXT,
    timestamp TEXT,
    recognized_keyword TEXT
);
CREATE TABLE food_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    meal_date TEXT,
    meal_time_category TEXT,
    menu_category TEXT,
    timestamp DATETIME
);
CREATE TABLE daily_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    date TEXT,
    category TEXT,
    value TEXT,
    timestamp DATETIME
);
CREATE TABLE health_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_name TEXT,
    status TEXT,
    note TEXT,
    timestamp DATETIME
);
CREATE TABLE car_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT,
    rule_name TEXT,
    timestamp DATETIME,
    score REAL
);
CREATE TABLE child_health_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    child_name TEXT,
    condition TEXT,
    timestamp DATETIME NOT NULL
);
CREATE TABLE defecation_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    record_type TEXT,
    condition TEXT,
    note TEXT,
    timestamp DATETIME NOT NULL
);
CREATE TABLE ai_report_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT,
    timestamp DATETIME NOT NULL
);
CREATE TABLE shopping_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    order_date TEXT,
    item_name TEXT,
    price INTEGER,
    email_id TEXT UNIQUE,
    timestamp DATETIME NOT NULL
);
CREATE TABLE haircut_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    visit_date TEXT,
    shop_name TEXT,
    menu TEXT,
    price INTEGER,
    email_id TEXT UNIQUE,
    timestamp DATETIME NOT NULL
);
CREATE TABLE weather_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    location TEXT DEFAULT '伊丹',
    min_temp REAL,
    max_temp REAL,
    weather_desc TEXT,
    max_pop INTEGER,
    umbrella_level TEXT,
    recorded_at TEXT,
    UNIQUE(date, location)
);
CREATE TABLE security_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    device_name TEXT,
    classification TEXT,
    image_path TEXT,
    recorded_at TEXT
);
CREATE TABLE bicycle_parking_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area_name TEXT,
    status_text TEXT,
    waiting_count INTEGER,
    timestamp DATETIME NOT NULL
);
CREATE TABLE land_price_records (
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
);
CREATE TABLE nas_records (
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
);
CREATE TABLE quest_users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    job_class TEXT,
    level INTEGER DEFAULT 1,
    exp INTEGER DEFAULT 0,
    gold INTEGER DEFAULT 0,
    medal_count INTEGER DEFAULT 0,
    avatar TEXT DEFAULT '🙂',
    updated_at DATETIME
, role TEXT);
CREATE TABLE reward_master (
    reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    cost_gold INTEGER,
    category TEXT,
    icon_key TEXT,
    desc TEXT,
    target TEXT DEFAULT 'all'
, description TEXT);
CREATE TABLE reward_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    reward_id INTEGER,
    reward_title TEXT,
    cost_gold INTEGER,
    redeemed_at DATETIME NOT NULL
);
CREATE TABLE equipment_master (
    equipment_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT, 
    power INTEGER,
    cost_gold INTEGER,
    icon_key TEXT
);
CREATE TABLE user_equipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    equipment_id INTEGER,
    is_equipped INTEGER DEFAULT 0,
    acquired_at DATETIME,
    UNIQUE(user_id, equipment_id)
);
CREATE TABLE party_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_boss_id INTEGER DEFAULT 1,
    current_hp INTEGER DEFAULT 0,
    max_hp INTEGER DEFAULT 100,
    week_start_date TEXT,
    is_defeated INTEGER DEFAULT 0,
    total_damage INTEGER DEFAULT 0,
    charge_gauge INTEGER DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE user_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    reward_id INTEGER,
    status TEXT DEFAULT 'owned',
    purchased_at DATETIME NOT NULL,
    used_at DATETIME,
    FOREIGN KEY(reward_id) REFERENCES reward_master(reward_id)
);
CREATE TABLE family_mileage (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    target_name TEXT NOT NULL,
    current_exp INTEGER DEFAULT 0,
    target_exp INTEGER NOT NULL,
    updated_at DATETIME
);
CREATE TABLE family_mileage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT NOT NULL,
    achieved_exp INTEGER NOT NULL,
    target_exp INTEGER NOT NULL,
    completed_at DATETIME NOT NULL
);
CREATE TABLE bounties (
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
);
CREATE TABLE suumo_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT UNIQUE,
    title TEXT,
    address TEXT,
    rent_price INTEGER,
    url TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_power_usage_device_ts
ON power_usage (device_id, timestamp DESC);
CREATE INDEX idx_switchbot_logs_device_ts
ON switchbot_meter_logs (device_id, timestamp DESC);
CREATE INDEX idx_device_records_device_ts
ON device_records (device_id, timestamp DESC);
CREATE TABLE "quest_master" (
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
        occurrence_chance REAL DEFAULT 1.0,
        start_time TEXT,
        end_time TEXT,
        days TEXT,
        pre_requisite_quest_id INTEGER DEFAULT NULL,
        reset_period TEXT DEFAULT 'daily'
);
