# HOME_SYSTEM/config.py
import os

# ==========================================
# 1. SwitchBot 設定 (MY_HOME_MONITOR/config.py から転記)
# ==========================================
SWITCHBOT_API_TOKEN = "b09c0711d0d0f4da0e21b54f7ae5902c69d763ce8b15bf592f11a10dfaa2efe4c1251dece5cb262be67f6a626cb08f38"
SWITCHBOT_API_SECRET = "d6873a1676f65ca19e51ab8a6043f994"

# 監視デバイスリスト (電力、温湿度、開閉、人感、カメラ、ハブを網羅)
MONITOR_DEVICES = [
    # --- Plug Mini (電力監視) ---
    {
        "id": "24587C9CCBCE",  # 1Fのトイレ (Plug Mini)
        "type": "Plug Mini (JP)",
        "notify_settings": {"power_threshold_watts": 0.0} # 必要なら閾値を設定
    },
    {
        "id": "D83BDA178576",  # テレビ (Plug Mini)
        "type": "Plug Mini (JP)",
        "notify_settings": {"power_threshold_watts": 100.0} # 例: つけっぱなし検知
    },
    {
        "id": "F09E9E9D599A",  # 炊飯器 (Plug Mini)
        "type": "Plug Mini (JP)",
        "notify_settings": {}
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
# 2. LINE Bot 設定 (LINEBOT/config.py から転記)
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = "VKwvvJqOgFlxoZ8whaurQ4VJzS8XP4h+fiY+6siLLP5YDiSkZKsQ2wDuMpMy2Tc63dQD8sc/GGtveva483EMoGqf6Bhhub9spNrc596NYM2YkIdhVZ/V7onv077Ltv83WaDnlXQ06fZQ4RIJsy9+KwdB04t89/1O/w1cDnyilFU="
LINE_CHANNEL_SECRET = "9db9eb67bee0a6e8e08619f174a4b60d"

# 通知先ユーザーID (send_line.pyの設定などから確認)
LINE_USER_ID = "Ud16cff6e78c41ade3bb7daf572c437fb"

# おはよう判定の設定
OHAYO_KEYWORDS = ["おはよ", "おはよう"]
MESSAGE_LENGTH_LIMIT = 30

# ==========================================
# 3. 共通システム設定
# ==========================================
# 統合DBの名前
SQLITE_DB_PATH = "home_system.db"
# テーブル名
SQLITE_TABLE_SENSOR = "device_records"
SQLITE_TABLE_OHAYO = "ohayo_records"

# ==========================================
# 4. バックアップ設定
# ==========================================
# バックアップ対象のファイルリスト
# データベースだけでなく、プログラム本体も含めるのが推奨です
BACKUP_FILES = [
    SQLITE_DB_PATH,                 # データベース (home_system.db)
    "config.py",                    # 設定ファイル
]