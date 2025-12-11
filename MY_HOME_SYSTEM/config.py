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
        "location": "伊丹",     # カメラの設置場所
        "ip": os.getenv("CAMERA_IP", "192.168.1.110"), # .envのCAMERA_IPを使う
        "port": 2020,
        "user": DEFAULT_CAM_USER,
        "pass": DEFAULT_CAM_PASS
    },
    # 2台目以降を追加する場合はここに記述
    {
        "id": "VIGI_C330I_Garden",
        "name": "庭カメラ",
        "location": "伊丹",    
        "ip": "192.168.1.51", 
        "port": 2020,
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
    {"id": "24587C9CCBCE", "type": "Plug Mini (JP)", "location": "伊丹", "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}},
    {"id": "D83BDA178576", "type": "Plug Mini (JP)", "location": "伊丹", "notify_settings": {"power_threshold_watts": 20.0, "notify_mode": "LOG_ONLY"}},
    {"id": "F09E9E9D599A", "type": "Plug Mini (JP)", "location": "伊丹", "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}},
    # --- MeterPlus (温湿度監視) ---
    {"id": "CFBF5E92AAD0", "type": "MeterPlus", "location": "伊丹", "notify_settings": {}},
    {"id": "E17F2E2DA99F", "type": "MeterPlus", "location": "高砂", "notify_settings": {}},
    {"id": "E30D45A30356", "type": "MeterPlus", "location": "高砂", "notify_settings": {}},
    {"id": "E9BA4D43962D", "type": "MeterPlus", "location": "伊丹", "notify_settings": {}},

    # --- Motion Sensor (人感センサー) ---
    {"id": "E9B20697916C", "type": "Motion Sensor", "location": "高砂", "notify_settings": {}},
    {"id": "F062114E225F", "type": "Motion Sensor", "location": "伊丹", "notify_settings": {}},

    # --- Contact Sensor (開閉センサー) ---
    {"id": "C937D8CB33A3", "type": "Contact Sensor", "location": "高砂", "notify_settings": {}},
    {"id": "D92743516777", "type": "Contact Sensor", "location": "高砂", "notify_settings": {}},
    {"id": "E07135DD95B1", "type": "Contact Sensor", "location": "高砂", "notify_settings": {}}, # お母さんの部屋
    {"id": "F5866D92E63D", "type": "Contact Sensor", "location": "高砂", "notify_settings": {}}, # 庭へのドア
    
    {"id": "F69BB5721955", "type": "Contact Sensor", "location": "伊丹", "notify_settings": {}}, # トイレ

    # --- Hub Mini ---
    {"id": "DE3B6D1C8AE4", "type": "Hub Mini", "location": "伊丹", "notify_settings": {}},
    {"id": "FEACA2E1797C", "type": "Hub Mini", "location": "高砂", "notify_settings": {}},

    # --- Cloud Cameras ---
    {"id": "eb66a4f83686d73815zteu", "type": "Indoor Cam", "location": "伊丹", "notify_settings": {}},
    {"id": "ebb1e93d271a144eaf3571", "type": "Pan/Tilt Cam", "location": "高砂", "notify_settings": {}}
]

# ==========================================
# 4. 通知 & LINE設定
# ==========================================
NOTIFICATION_TARGET = os.getenv("NOTIFICATION_TARGET", "line")

DISCORD_WEBHOOK_ERROR = os.getenv("DISCORD_WEBHOOK_ERROR")
DISCORD_WEBHOOK_REPORT = os.getenv("DISCORD_WEBHOOK_REPORT")
DISCORD_WEBHOOK_NOTIFY = os.getenv("DISCORD_WEBHOOK_NOTIFY")
DISCORD_WEBHOOK_URL = DISCORD_WEBHOOK_NOTIFY or os.getenv("DISCORD_WEBHOOK_URL")
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
SQLITE_TABLE_DEFECATION = "defecation_records"

# 排便の種類の選択肢 (ブリストルスケールを参考に簡易化)
DEFECATION_TYPES = [
    "🐰 コロコロ (硬い)", 
    "🍌 バナナ (普通)", 
    "💧 軟便・下痢", 
    "🩸 血便・異常"
]

# お腹の症状の選択肢
STOMACH_SYMPTOMS = [
    "⚡ 腹痛あり", 
    "🤢 吐き気・胃痛", 
    "💨 ガス腹・張り", 
    "👌 スッキリした"
]

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

# ==========================================
# 7. 給料明細管理 (Salary Manager)
# ==========================================
# 機密情報はすべて環境変数から取得
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SALARY_MAIL_SENDER = os.getenv("SALARY_MAIL_SENDER")

# PDFパスワードリスト (カンマ区切り文字列をリストに変換)
_passwords_str = os.getenv("SALARY_PDF_PASSWORDS", "")
SALARY_PDF_PASSWORDS = [p.strip() for p in _passwords_str.split(",") if p.strip()]

# ディレクトリ・ファイルパス設定
SALARY_IMAGE_DIR = os.path.join(BASE_DIR, "..", "assets", "salary_images")
SALARY_DATA_DIR = os.path.join(BASE_DIR, "data")
SALARY_CSV_PATH = os.path.join(SALARY_DATA_DIR, "salary_history.csv")
BONUS_CSV_PATH = os.path.join(SALARY_DATA_DIR, "bonus_history.csv")

# ディレクトリ自動作成
for d in [SALARY_IMAGE_DIR, SALARY_DATA_DIR]:
    if not os.path.exists(d):
        os.makedirs(d, exist_ok=True)