# HOME_SYSTEM/config.py
import os
from dotenv import load_dotenv

# .envファイルを読み込む
# (このファイルと同じ場所にある .env を探します)
load_dotenv()

# ==========================================
# 1. SwitchBot 設定
# ==========================================
# 環境変数から取得 (GitHubには公開されません)
SWITCHBOT_API_TOKEN = os.getenv("SWITCHBOT_API_TOKEN")
SWITCHBOT_API_SECRET = os.getenv("SWITCHBOT_API_SECRET")

# 監視デバイスリスト (電力、温湿度、開閉、人感、カメラ、ハブを網羅)
MONITOR_DEVICES = [
    # --- Plug Mini (電力監視) ---
MONITOR_DEVICES = [
    # --- Plug Mini (電力監視) ---
    {
        "id": "24587C9CCBCE",  # 1Fのトイレ
        "type": "Plug Mini (JP)",
        "notify_settings": {
            "power_threshold_watts": 5.0,
            "notify_mode": "CONTINUOUS" # ★ 従来通り（つけっぱなし警告用）
        }
    },
    {
        "id": "D83BDA178576",  # テレビ
        "type": "Plug Mini (JP)",
        "notify_settings": {
            "power_threshold_watts": 20.0, # ★ 待機電力(数W)を誤検知しないよう少し高めに設定推奨
            "notify_mode": "ON_END_SUMMARY" # ★ 消えた時に「何時から何時まで」を通知
        }
    },
    {
        "id": "F09E9E9D599A",  # 炊飯器
        "type": "Plug Mini (JP)",
        "notify_settings": {
            "power_threshold_watts": 5.0,
            "notify_mode": "ON_START" # ★ 炊き始めだけ通知
        }
    },

    # --- MeterPlus (温湿度監視) ---
    {
        "id": "CFBF5E92AAD0",  # 仕事部屋
        "type": "MeterPlus",
        "notify_settings": {}
    },
    {
        "id": "E17F2E2DA99F",  # 1Fの洗面所
        "type": "MeterPlus",
        "notify_settings": {}
    },
    {
        "id": "E30D45A30356",  # リビング
        "type": "MeterPlus",
        "notify_settings": {}
    },
    {
        "id": "E9BA4D43962D",  # 居間
        "type": "MeterPlus",
        "notify_settings": {}
    },

    # --- Motion Sensor (人感センサー) ---
    {
        "id": "E9B20697916C",  # 和室
        "type": "Motion Sensor",
        "notify_settings": {}
    },
    {
        "id": "F062114E225F",  # 人感センサー
        "type": "Motion Sensor",
        "notify_settings": {}
    },

    # --- Contact Sensor (開閉センサー) ---
    {
        "id": "C937D8CB33A3",  # 玄関
        "type": "Contact Sensor",
        "notify_settings": {}
    },
    {
        "id": "D92743516777",  # 冷蔵庫
        "type": "Contact Sensor",
        "notify_settings": {}
    },
    {
        "id": "E07135DD95B1",  # お母さんの部屋 (高砂)
        "type": "Contact Sensor",
        "notify_settings": {}
    },
    {
        "id": "F5866D92E63D",  # 庭へのドア (高砂)
        "type": "Contact Sensor",
        "notify_settings": {}
    },
    {
        "id": "F69BB5721955",  # トイレ
        "type": "Contact Sensor",
        "notify_settings": {}
    },

    # --- Hub Mini (ハブ) ---
    {
        "id": "DE3B6D1C8AE4",  # ハブミニ E4
        "type": "Hub Mini",
        "notify_settings": {}
    },
    {
        "id": "FEACA2E1797C",  # 高砂のハブミニ
        "type": "Hub Mini",
        "notify_settings": {}
    },

    # --- Cameras (見守りカメラ) ---
    {
        "id": "eb66a4f83686d73815zteu",  # ともやのへや (Indoor Cam)
        "type": "Indoor Cam",
        "notify_settings": {}
    },
    {
        "id": "ebb1e93d271a144eaf3571",  # 高砂の玄関 (Pan/Tilt Cam)
        "type": "Pan/Tilt Cam",
        "notify_settings": {}
    }
]

# ==========================================
# 2. LINE Bot 設定
# ==========================================
# 環境変数から取得
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# おはよう判定の設定
OHAYO_KEYWORDS = ["おはよ", "おはよう"]
MESSAGE_LENGTH_LIMIT = 30


# ==========================================
# 3. 共通システム設定
# ==========================================
# 統合DBの名前 (絶対パス)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB_PATH = os.path.join(BASE_DIR, "home_system.db")

# テーブル名
SQLITE_TABLE_SENSOR = "device_records"
SQLITE_TABLE_OHAYO = "ohayo_records"
SQLITE_TABLE_FOOD = "food_records"

# ==========================================
# 4. バックアップ設定
# ==========================================
# バックアップ対象のファイルリスト
BACKUP_FILES = [
    SQLITE_DB_PATH,                 # データベース (home_system.db)
    "config.py",                    # 設定ファイル
    ".env"                          # ★注意: .env はバックアップには含めますが、Gitには上げません
]

# ==========================================
# 5. 食事メニュー設定 (ここを自由に編集してください！)
# ==========================================
MENU_OPTIONS = {
    "自炊": [
        "カレーライス", 
        "豚しゃぶ", 
        "焼き魚", 
        "うどん", 
        "味噌汁とご飯", 
        "野菜炒め", 
        "オムライス"
    ],
    "外食": [
        "マクドナルド", 
        "魚べえ", 
        "サイゼリヤ", 
        "丸亀製麺"
    ],
    "その他": [
        "スーパーの惣菜",
        "コンビニ", 
        "冷凍食品", 
        "カップ麺"
    ]
}