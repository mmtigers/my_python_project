# HOME_SYSTEM/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# 1. SwitchBot & Nature Remo 設定
# ==========================================
SWITCHBOT_API_TOKEN = os.getenv("SWITCHBOT_API_TOKEN")
SWITCHBOT_API_SECRET = os.getenv("SWITCHBOT_API_SECRET")
NATURE_REMO_ACCESS_TOKEN = os.getenv("NATURE_REMO_ACCESS_TOKEN")

# ==========================================
# 2. カメラ設定 (複数台対応)
# ==========================================
# 環境変数には "192.168.1.110,192.168.1.111" のようにカンマ区切りで入っているか、
# または直接ここに書き込む想定で柔軟に対応します。

# デフォルトのユーザー/パスワード (共通の場合)
DEFAULT_CAM_USER = os.getenv("CAMERA_USER", "admin")
DEFAULT_CAM_PASS = os.getenv("CAMERA_PASS", "")

# ★カメラリスト定義
CAMERAS = [
    {
        "id": "VIGI_C540_Parking",  # DB記録用のID
        "name": "駐車場カメラ",       # 通知用の名前
        "ip": os.getenv("CAMERA_IP", "192.168.1.110"), # .envのCAMERA_IPを使う
        "user": DEFAULT_CAM_USER,
        "pass": DEFAULT_CAM_PASS
    },
    # 2台目以降を追加する場合はここに記述
    {
        "id": "VIGI_C330I_Garden",
        "name": "庭カメラ",
        "ip": "192.168.1.51", 
        "user": DEFAULT_CAM_USER,
        "pass": DEFAULT_CAM_PASS
    }
]

# 後方互換性用 (診断スクリプトなどが動くように1台目の情報をマッピング)
if CAMERAS:
    CAMERA_IP = CAMERAS[0]["ip"]
    CAMERA_USER = CAMERAS[0]["user"]
    CAMERA_PASS = CAMERAS[0]["pass"]
else:
    CAMERA_IP, CAMERA_USER, CAMERA_PASS = None, None, None

# ==========================================
# 3. 監視デバイスリスト (SwitchBot等)
# ==========================================
MONITOR_DEVICES = [
    # Plug Mini
    {"id": "24587C9CCBCE", "type": "Plug Mini (JP)", "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}},
    {"id": "D83BDA178576", "type": "Plug Mini (JP)", "notify_settings": {"power_threshold_watts": 20.0, "notify_mode": "LOG_ONLY"}},
    {"id": "F09E9E9D599A", "type": "Plug Mini (JP)", "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}},
    # MeterPlus
    {"id": "CFBF5E92AAD0", "type": "MeterPlus", "notify_settings": {}},
    {"id": "E17F2E2DA99F", "type": "MeterPlus", "notify_settings": {}},
    {"id": "E30D45A30356", "type": "MeterPlus", "notify_settings": {}},
    {"id": "E9BA4D43962D", "type": "MeterPlus", "notify_settings": {}},
    # Sensors
    {"id": "E9B20697916C", "type": "Motion Sensor", "notify_settings": {}},
    {"id": "F062114E225F", "type": "Motion Sensor", "notify_settings": {}},
    {"id": "C937D8CB33A3", "type": "Contact Sensor", "notify_settings": {}},
    {"id": "D92743516777", "type": "Contact Sensor", "notify_settings": {}},
    {"id": "E07135DD95B1", "type": "Contact Sensor", "notify_settings": {}},
    {"id": "F5866D92E63D", "type": "Contact Sensor", "notify_settings": {}},
    {"id": "F69BB5721955", "type": "Contact Sensor", "notify_settings": {}},
    # Hubs
    {"id": "DE3B6D1C8AE4", "type": "Hub Mini", "notify_settings": {}},
    {"id": "FEACA2E1797C", "type": "Hub Mini", "notify_settings": {}},
    # Other Cameras (Cloud)
    {"id": "eb66a4f83686d73815zteu", "type": "Indoor Cam", "notify_settings": {}},
    {"id": "ebb1e93d271a144eaf3571", "type": "Pan/Tilt Cam", "notify_settings": {}},
]

# ==========================================
# 4. 通知 & LINE設定
# ==========================================
NOTIFICATION_TARGET = os.getenv("NOTIFICATION_TARGET", "line")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# 高砂見守り
LINE_PARENTS_GROUP_ID = os.getenv("LINE_PARENTS_GROUP_ID", "")
HEALTH_CHECK_TIMES = ["08:00", "20:00"]

# ★修正: 環境変数から子供の名前リストを取得 (カンマ区切りをリストに変換)
children_str = os.getenv("CHILDREN_NAMES", "")
CHILDREN_NAMES = children_str.split(",") if children_str else []

# 体調の選択肢 (主婦向け表現)
CHILD_SYMPTOMS = ["😊 元気いっぱい", "🤒 お熱がある", "🤧 鼻水・咳", "🤮 お腹の調子が悪い", "🤕 怪我した", "✏️ その他"]
CHILD_CHECK_TIME = "07:30"

OHAYO_KEYWORDS = ["おはよ", "おはよう"]
MESSAGE_LENGTH_LIMIT = 30

# ==========================================
# 5. 共通システム設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB_PATH = os.path.join(BASE_DIR, "home_system.db")

SQLITE_TABLE_SENSOR = "device_records"
SQLITE_TABLE_OHAYO = "ohayo_records"
SQLITE_TABLE_FOOD = "food_records"
SQLITE_TABLE_DAILY = "daily_records"
SQLITE_TABLE_HEALTH = "health_records"
SQLITE_TABLE_CAR = "car_records"
SQLITE_TABLE_CHILD = "child_health_records"

# ==========================================
# 6. バックアップ & メニュー
# ==========================================
BACKUP_FILES = [SQLITE_DB_PATH, "config.py", ".env"]

MENU_OPTIONS = {
    "自炊": ["カレーライス", "豚しゃぶ", "焼き魚", "うどん", "味噌汁とご飯", "野菜炒め", "オムライス"],
    "外食": ["マクドナルド", "魚べえ", "サイゼリヤ", "丸亀製麺"],
    "その他": ["スーパーの惣菜", "コンビニ", "冷凍食品", "カップ麺"]
}

# 車の検知ルール
CAR_RULE_KEYWORDS = {
    "LEAVE": ["Exit", "Leave", "Out"],
    "RETURN": ["Enter", "In", "Arrive"]
}