CREATE TABLE device_records (
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
        , battery_level INTEGER);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE ohayo_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT,
            message TEXT,
            timestamp TEXT NOT NULL,
            recognized_keyword TEXT
        );
CREATE TABLE daily_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT,
            date TEXT NOT NULL,
            category TEXT NOT NULL, -- 外出 / 面会
            value TEXT NOT NULL,    -- はい / いいえ
            timestamp DATETIME NOT NULL
        );
CREATE TABLE health_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT,
            status TEXT,  -- 元気/普通/不調
            note TEXT,
            timestamp DATETIME NOT NULL
        );
CREATE TABLE car_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,   -- LEAVE (外出) / RETURN (帰宅)
        rule_name TEXT,
        timestamp DATETIME NOT NULL
    , score REAL);
CREATE TABLE child_health_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,       -- 記録者のID
        user_name TEXT,     -- 記録者の名前
        child_name TEXT,    -- 子供の名前
        condition TEXT,     -- 症状
        timestamp DATETIME NOT NULL
    );
CREATE TABLE defecation_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,       -- 記録者のID
        user_name TEXT,     -- 記録者の名前
        record_type TEXT,   -- "排便" or "症状"
        condition TEXT,     -- バナナ、下痢、腹痛など
        note TEXT,          -- メモ (オプション)
        timestamp DATETIME NOT NULL
    );
CREATE TABLE ai_report_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT,          -- 生成されたメッセージ本文
        timestamp DATETIME NOT NULL
    );
CREATE TABLE shopping_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT,       -- Amazon / Rakuten
        order_date TEXT,     -- 注文日 (YYYY-MM-DD)
        item_name TEXT,      -- 商品名（件名から抜粋）
        price INTEGER,       -- 金額
        email_id TEXT UNIQUE,-- GmailのMessage-ID (重複登録防止)
        timestamp DATETIME NOT NULL
    );
CREATE TABLE haircut_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT,       -- HotPepperBeauty など
        visit_date TEXT,     -- 来店日時 (YYYY-MM-DD HH:MM)
        shop_name TEXT,      -- 店名
        menu TEXT,           -- メニュー内容 (カットなど)
        price INTEGER,       -- 金額
        email_id TEXT UNIQUE,-- 重複防止
        timestamp DATETIME NOT NULL
    );
CREATE TABLE haircut_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_date TEXT UNIQUE,
                    created_at TEXT
                );
CREATE TABLE food_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT,  -- YYYY-MM-DD
                        menu TEXT,
                        created_at TEXT
                    , menu_category TEXT, meal_date TEXT, meal_time_category TEXT, user_id TEXT, user_name TEXT, timestamp DATETIME);
CREATE TABLE security_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            device_name TEXT,
            classification TEXT,
            image_path TEXT
        , recorded_at TEXT);
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
CREATE TABLE app_rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ranking_type TEXT, -- 'free' (無料人気) or 'grossing' (売上人気)
            rank INTEGER,
            app_id TEXT,
            title TEXT,
            developer TEXT,
            icon_url TEXT,
            score REAL,
            recorded_at TEXT,
            UNIQUE(date, ranking_type, rank)
        );
CREATE TABLE bicycle_parking_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        area_name TEXT,      -- エリア名（例：阪急伊丹駅前地下 Aブロック）
        status_text TEXT,    -- 取得した状態テキスト（例：5人待ち、空きあり）
        waiting_count INTEGER, -- 待機人数（数値抽出、空きなら0）
        timestamp DATETIME NOT NULL
    );
CREATE TABLE land_price_records (
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
    );
CREATE TABLE nas_records (
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
    );
CREATE TABLE quest_users (
        user_id TEXT PRIMARY KEY,
        name TEXT,
        job_class TEXT,
        level INTEGER DEFAULT 1,
        exp INTEGER DEFAULT 0,
        gold INTEGER DEFAULT 0,
        updated_at DATETIME
    , avatar TEXT DEFAULT '🙂', medal_count INTEGER DEFAULT 0, role TEXT);
CREATE TABLE quest_master (
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
    , start_time TEXT, end_time TEXT, days TEXT, pre_requisite_quest_id INTEGER DEFAULT NULL, reset_period TEXT DEFAULT 'weekly_monday');
CREATE TABLE quest_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        quest_id INTEGER,
        quest_title TEXT,
        exp_earned INTEGER,
        gold_earned INTEGER,
        completed_at DATETIME NOT NULL
    , status TEXT DEFAULT 'approved');
CREATE TABLE reward_master (
        reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        cost_gold INTEGER,
        category TEXT,           -- 'item'(装備), 'consumable'(消耗品/権利)
        icon_key TEXT
    , description TEXT, target TEXT DEFAULT 'all', desc TEXT);
CREATE TABLE reward_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        reward_id INTEGER,
        reward_title TEXT,
        cost_gold INTEGER,
        redeemed_at DATETIME NOT NULL
    );
CREATE TABLE quest_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            points INTEGER DEFAULT 0,
            target_user_id INTEGER,
            FOREIGN KEY (target_user_id) REFERENCES quest_users(id)
        );
CREATE TABLE quest_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            date TEXT NOT NULL,
            is_completed INTEGER DEFAULT 1,
            FOREIGN KEY (task_id) REFERENCES quest_tasks(id)
        );
CREATE TABLE equipment_master (
                equipment_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT,  -- weapon / armor
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
                charge_gauge INTEGER DEFAULT 0,
                updated_at TEXT
            , max_hp INTEGER DEFAULT 1000, week_start_date TEXT DEFAULT '', is_defeated INTEGER DEFAULT 0, total_damage INTEGER DEFAULT 0);
CREATE TABLE user_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                reward_id INTEGER,
                status TEXT DEFAULT 'owned',  -- owned:所持, pending:使用申請中, consumed:使用済
                purchased_at DATETIME NOT NULL,
                used_at DATETIME,
                FOREIGN KEY(reward_id) REFERENCES reward_master(reward_id)
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
                property_id TEXT UNIQUE,  -- 物件固有ID (URLの一部など)
                title TEXT,
                rent_price INTEGER,       -- 家賃 + 管理費
                url TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            , address TEXT);
CREATE TABLE users (
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
            );
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
CREATE TABLE daily_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                category TEXT NOT NULL, -- 排便, 風呂, 睡眠, 食事 etc.
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
CREATE TABLE youtube_subscriptions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_url TEXT UNIQUE NOT NULL,
                        is_active INTEGER DEFAULT 1,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
