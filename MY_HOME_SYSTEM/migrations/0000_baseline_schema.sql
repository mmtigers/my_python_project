-- Issue #330: スキーマ管理のmigrations/一本化。
-- 従来 init_unified_db.py の init_db() がPythonコード内のCREATE TABLE群として
-- 持っていたベースラインスキーマを、そのままSQLとして移設したもの。
-- init_db() は本ファイルを含む migrations/ の適用だけを行う薄いラッパーになった。
--
-- 番号が 0000 なのは意図的: 0001以降のマイグレーションは対象テーブルの存在を
-- 前提とする(存在しないと "no such table" で起動失敗になる)ため、空DBでは
-- 本ファイルが必ず最初に適用される必要がある。既存DB(本番ラズパイ含む)では
-- 全文 CREATE TABLE IF NOT EXISTS のためno-opであり、適用済みの0001〜0008より
-- 後から記録されても実害はない。
--
-- 注意: 本ファイルの各テーブル定義は「0001適用前」のカラム構成である。
-- role/reset_period/description等のカラムは従来どおり0001以降が追加するため、
-- ここに新カラムを足さないこと。以後のスキーマ変更はREADME.mdの規約どおり
-- 新しい NNNN_*.sql として追加する。

-- ==========================================
-- 1. New Core Tables (Design Doc v1.0.0)
-- ==========================================
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT UNIQUE, -- LINE ID等
    name TEXT,
    level INTEGER DEFAULT 1,
    xp INTEGER DEFAULT 0,
    gold INTEGER DEFAULT 0,
    status TEXT DEFAULT '在宅', -- 在宅 or 外出
    job_class TEXT,
    medal_count INTEGER DEFAULT 0,
    avatar TEXT,
    updated_at DATETIME
);

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
);

CREATE TABLE IF NOT EXISTS quest_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    quest_id INTEGER,
    quest_title TEXT,
    status TEXT DEFAULT 'approved', -- approved or pending
    completed_at DATETIME NOT NULL,
    exp_earned INTEGER,
    gold_earned INTEGER
);

-- 統合ログ: 生活イベント (config.SQLITE_TABLE_DAILY_LOGS)
CREATE TABLE IF NOT EXISTS daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    category TEXT NOT NULL, -- 排便, 風呂, 睡眠, 食事 etc.
    detail TEXT,
    timestamp DATETIME NOT NULL
);

-- SwitchBot Meter Logs 温湿度分離 (config.SQLITE_TABLE_SWITCHBOT_LOGS)
CREATE TABLE IF NOT EXISTS switchbot_meter_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    device_name TEXT,
    temperature REAL,
    humidity REAL,
    timestamp DATETIME NOT NULL
);

-- 電力ログ分離 (config.SQLITE_TABLE_POWER_USAGE)
CREATE TABLE IF NOT EXISTS power_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    device_name TEXT,
    wattage REAL,
    timestamp DATETIME NOT NULL
);

-- ==========================================
-- 2. Existing / Legacy Tables (Must Keep)
-- ==========================================
-- 旧統合センサーテーブル (config.SQLITE_TABLE_SENSOR)
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
);

-- 挨拶 (config.SQLITE_TABLE_OHAYO)
CREATE TABLE IF NOT EXISTS ohayo_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    message TEXT,
    timestamp TEXT,
    recognized_keyword TEXT
);

-- 食事 (config.SQLITE_TABLE_FOOD)
CREATE TABLE IF NOT EXISTS food_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    meal_date TEXT,
    meal_time_category TEXT,
    menu_category TEXT,
    timestamp DATETIME
);

-- 旧汎用デイリー記録 (config.SQLITE_TABLE_DAILY)
CREATE TABLE IF NOT EXISTS daily_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    date TEXT,
    category TEXT,
    value TEXT,
    timestamp DATETIME
);

-- 汎用健康 (config.SQLITE_TABLE_HEALTH)
CREATE TABLE IF NOT EXISTS health_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_name TEXT,
    status TEXT,
    note TEXT,
    timestamp DATETIME
);

-- 車検知 (config.SQLITE_TABLE_CAR)
CREATE TABLE IF NOT EXISTS car_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT,
    rule_name TEXT,
    timestamp DATETIME,
    score REAL
);

-- 子供体調 (config.SQLITE_TABLE_CHILD)
CREATE TABLE IF NOT EXISTS child_health_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    child_name TEXT,
    condition TEXT,
    timestamp DATETIME NOT NULL
);

-- 排便記録 (config.SQLITE_TABLE_DEFECATION)
CREATE TABLE IF NOT EXISTS defecation_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    record_type TEXT,
    condition TEXT,
    note TEXT,
    timestamp DATETIME NOT NULL
);

-- AIレポート (config.SQLITE_TABLE_AI_REPORT)
CREATE TABLE IF NOT EXISTS ai_report_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT,
    timestamp DATETIME NOT NULL
);

-- ショッピング (config.SQLITE_TABLE_SHOPPING)
CREATE TABLE IF NOT EXISTS shopping_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    order_date TEXT,
    item_name TEXT,
    price INTEGER,
    email_id TEXT UNIQUE,
    timestamp DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS haircut_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    visit_date TEXT,
    shop_name TEXT,
    menu TEXT,
    price INTEGER,
    email_id TEXT UNIQUE,
    timestamp DATETIME NOT NULL
);

-- 天気 (Issue #114: location/umbrella_level等を含む実運用スキーマ準拠の構成)
CREATE TABLE IF NOT EXISTS weather_history (
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

CREATE TABLE IF NOT EXISTS security_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    device_name TEXT,
    classification TEXT,
    image_path TEXT,
    recorded_at TEXT
);

-- 駐輪場 (config.SQLITE_TABLE_BICYCLE)
CREATE TABLE IF NOT EXISTS bicycle_parking_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area_name TEXT,
    status_text TEXT,
    waiting_count INTEGER,
    timestamp DATETIME NOT NULL
);

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
);

-- NAS監視 (config.SQLITE_TABLE_NAS)
CREATE TABLE IF NOT EXISTS nas_records (
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

-- ==========================================
-- 3. Game & Quest System (Legacy/Transitional)
-- ==========================================
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
);

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
    occurrence_chance REAL DEFAULT 1.0,
    start_time TEXT,
    end_time TEXT
);

CREATE TABLE IF NOT EXISTS reward_master (
    reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    cost_gold INTEGER,
    category TEXT,
    icon_key TEXT,
    desc TEXT,
    target TEXT DEFAULT 'all'
);

CREATE TABLE IF NOT EXISTS reward_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    reward_id INTEGER,
    reward_title TEXT,
    cost_gold INTEGER,
    redeemed_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS equipment_master (
    equipment_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT, -- weapon or armor
    power INTEGER,
    cost_gold INTEGER,
    icon_key TEXT
);

CREATE TABLE IF NOT EXISTS user_equipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    equipment_id INTEGER,
    is_equipped INTEGER DEFAULT 0,
    acquired_at DATETIME,
    UNIQUE(user_id, equipment_id)
);

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
);

CREATE TABLE IF NOT EXISTS user_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    reward_id INTEGER,
    status TEXT DEFAULT 'owned',
    purchased_at DATETIME NOT NULL,
    used_at DATETIME,
    FOREIGN KEY(reward_id) REFERENCES reward_master(reward_id)
);

CREATE TABLE IF NOT EXISTS family_mileage (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    target_name TEXT NOT NULL,
    current_exp INTEGER DEFAULT 0,
    target_exp INTEGER NOT NULL,
    updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS family_mileage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT NOT NULL,
    achieved_exp INTEGER NOT NULL,
    target_exp INTEGER NOT NULL,
    completed_at DATETIME NOT NULL
);

-- ギルド掲示板
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
);

CREATE TABLE IF NOT EXISTS suumo_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT UNIQUE,
    title TEXT,
    address TEXT,
    rent_price INTEGER,
    url TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- インデックス (5〜10分間隔で継続的に書き込まれるログテーブルは
-- device_id + timestamp での検索・直近値取得が頻発するため付与)
-- ==========================================
CREATE INDEX IF NOT EXISTS idx_power_usage_device_ts
ON power_usage (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_switchbot_logs_device_ts
ON switchbot_meter_logs (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_device_records_device_ts
ON device_records (device_id, timestamp DESC);
