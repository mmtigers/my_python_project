# HOME_SYSTEM/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# 1. SwitchBot & Nature Remo & Camera 設定
# ==========================================
SWITCHBOT_API_TOKEN = os.getenv("SWITCHBOT_API_TOKEN")
SWITCHBOT_API_SECRET = os.getenv("SWITCHBOT_API_SECRET")
NATURE_REMO_ACCESS_TOKEN = os.getenv("NATURE_REMO_ACCESS_TOKEN")
CAMERA_IP = os.getenv("CAMERA_IP")
CAMERA_USER = os.getenv("CAMERA_USER")
CAMERA_PASS = os.getenv("CAMERA_PASS")

# 監視デバイスリスト
MONITOR_DEVICES = [
    # 電力監視 (Plug Mini)
    {
        "id": "24587C9CCBCE", "type": "Plug Mini (JP)", # 1Fトイレ
        "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}
    },
    {
        "id": "D83BDA178576", "type": "Plug Mini (JP)", # テレビ
        "notify_settings": {"power_threshold_watts": 20.0, "notify_mode": "LOG_ONLY"}
    },
    {
        "id": "F09E9E9D599A", "type": "Plug Mini (JP)", # 炊飯器
        "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}
    },
    # 温湿度計 (MeterPlus)
    {"id": "CFBF5E92AAD0", "type": "MeterPlus", "notify_settings": {}}, # 仕事部屋
    {"id": "E17F2E2DA99F", "type": "MeterPlus", "notify_settings": {}}, # 1F洗面所
    {"id": "E30D45A30356", "type": "MeterPlus", "notify_settings": {}}, # リビング
    {"id": "E9BA4D43962D", "type": "MeterPlus", "notify_settings": {}}, # 居間
    # 人感センサー
    {"id": "E9B20697916C", "type": "Motion Sensor", "notify_settings": {}}, # 和室
    {"id": "F062114E225F", "type": "Motion Sensor", "notify_settings": {}}, # 人感
    # 開閉センサー
    {"id": "C937D8CB33A3", "type": "Contact Sensor", "notify_settings": {}}, # 玄関
    {"id": "D92743516777", "type": "Contact Sensor", "notify_settings": {}}, # 冷蔵庫
    {"id": "E07135DD95B1", "type": "Contact Sensor", "notify_settings": {}}, # 母部屋
    {"id": "F5866D92E63D", "type": "Contact Sensor", "notify_settings": {}}, # 庭ドア
    {"id": "F69BB5721955", "type": "Contact Sensor", "notify_settings": {}}, # トイレ
    # その他
    {"id": "DE3B6D1C8AE4", "type": "Hub Mini", "notify_settings": {}},
    {"id": "FEACA2E1797C", "type": "Hub Mini", "notify_settings": {}},
    {"id": "eb66a4f83686d73815zteu", "type": "Indoor Cam", "notify_settings": {}},
    {"id": "ebb1e93d271a144eaf3571", "type": "Pan/Tilt Cam", "notify_settings": {}},
    # 監視カメラ
    {"id": "VIGI_C540_W", "type": "ONVIF Camera", "notify_settings": {"notify_mode": "REALTIME"} } # 検知即通知
]

# === 2. 通知 & LINE設定 ===
NOTIFICATION_TARGET = os.getenv("NOTIFICATION_TARGET", "line") # line or discord
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# ★追加: 高砂見守り設定
LINE_PARENTS_GROUP_ID = os.getenv("LINE_PARENTS_GROUP_ID", "")
HEALTH_CHECK_TIMES = ["08:00", "20:00"]

OHAYO_KEYWORDS = ["おはよ", "おはよう"]
MESSAGE_LENGTH_LIMIT = 30

# === 3. システム & DB設定 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB_PATH = os.path.join(BASE_DIR, "home_system.db")

SQLITE_TABLE_SENSOR = "device_records"
SQLITE_TABLE_OHAYO = "ohayo_records"
SQLITE_TABLE_FOOD = "food_records"
SQLITE_TABLE_DAILY = "daily_records"
SQLITE_TABLE_HEALTH = "health_records"

# === 4. バックアップ & メニュー ===
BACKUP_FILES = [SQLITE_DB_PATH, "config.py", ".env"]

MENU_OPTIONS = {
    "自炊": ["カレーライス", "豚しゃぶ", "焼き魚", "うどん", "味噌汁とご飯", "野菜炒め", "オムライス"],
    "外食": ["マクドナルド", "魚べえ", "サイゼリヤ", "丸亀製麺"],
    "その他": ["スーパーの惣菜", "コンビニ", "冷凍食品", "カップ麺"]
}