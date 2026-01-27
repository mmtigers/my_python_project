# MY_HOME_SYSTEM/config.py
import os
import sys
import json
from typing import List, Dict, Optional, Any, Tuple
from dotenv import load_dotenv

# .envファイルのロード
load_dotenv()

# ==========================================
# 0. 環境・機能フラグ設定
# ==========================================
# 環境設定 (development / production)
ENV: str = os.getenv("ENV", "development")

# もちもの使用時の承認フロー設定
ENABLE_APPROVAL_FLOW: bool = os.getenv("ENABLE_APPROVAL_FLOW", "False").lower() == "true"

# ==========================================
# 1. 認証・API設定 (Secrets)
# ==========================================
SWITCHBOT_API_TOKEN: Optional[str] = os.getenv("SWITCHBOT_API_TOKEN")
SWITCHBOT_API_SECRET: Optional[str] = os.getenv("SWITCHBOT_API_SECRET")
NATURE_REMO_ACCESS_TOKEN: Optional[str] = os.getenv("NATURE_REMO_ACCESS_TOKEN")
NATURE_REMO_ACCESS_TOKEN_TAKASAGO: Optional[str] = os.getenv("NATURE_REMO_ACCESS_TOKEN_TAKASAGO")

LINE_CHANNEL_ACCESS_TOKEN: Optional[str] = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET: Optional[str] = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID: Optional[str] = os.getenv("LINE_USER_ID")
LINE_PARENTS_GROUP_ID: str = os.getenv("LINE_PARENTS_GROUP_ID", "")

# Discord Webhooks
DISCORD_WEBHOOK_ERROR: Optional[str] = os.getenv("DISCORD_WEBHOOK_ERROR")
DISCORD_WEBHOOK_REPORT: Optional[str] = os.getenv("DISCORD_WEBHOOK_REPORT")
DISCORD_WEBHOOK_NOTIFY: Optional[str] = os.getenv("DISCORD_WEBHOOK_NOTIFY")
DISCORD_WEBHOOK_URL: Optional[str] = DISCORD_WEBHOOK_NOTIFY or os.getenv("DISCORD_WEBHOOK_URL")

# GMAIL & Gemini
GMAIL_USER: Optional[str] = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD: Optional[str] = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
SALARY_MAIL_SENDER: Optional[str] = os.getenv("SALARY_MAIL_SENDER")

# ==========================================
# 2. システム・パス設定
# ==========================================
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))

# NAS設定
NAS_MOUNT_POINT: str = os.getenv("NAS_MOUNT_POINT", "/mnt/nas")
NAS_PROJECT_ROOT: str = os.path.join(NAS_MOUNT_POINT, "home_system")

# DB & Assets
SQLITE_DB_PATH: str = os.path.join(BASE_DIR, "home_system.db")
ASSETS_DIR: str = os.path.join(NAS_PROJECT_ROOT, "assets")
LOG_DIR: str = os.path.join(BASE_DIR, "logs")

# DBテーブル名定義
SQLITE_TABLE_SENSOR: str = "device_records"
SQLITE_TABLE_OHAYO: str = "ohayo_records"
SQLITE_TABLE_FOOD: str = "food_records"
SQLITE_TABLE_DAILY: str = "daily_records"
SQLITE_TABLE_HEALTH: str = "health_records"
SQLITE_TABLE_CAR: str = "car_records"
SQLITE_TABLE_CHILD: str = "child_health_records"
SQLITE_TABLE_DEFECATION: str = "defecation_records"
SQLITE_TABLE_AI_REPORT: str = "ai_report_records"
SQLITE_TABLE_SHOPPING: str = "shopping_records"
SQLITE_TABLE_NAS: str = "nas_records"
SQLITE_TABLE_BICYCLE: str = "bicycle_parking_records"

BACKUP_FILES: List[str] = [SQLITE_DB_PATH, "config.py", ".env"]

# デフォルトアセット
DEFAULT_ASSETS_DIR: str = os.path.join(BASE_DIR, "defaults")
DEFAULT_SOUND_SOURCE: str = os.path.join(DEFAULT_ASSETS_DIR, "sounds")

# ==========================================
# 3. デバイス・ルール設定
# ==========================================
NOTIFICATION_TARGET: str = os.getenv("NOTIFICATION_TARGET", "discord")

# 子供設定
_children_str: str = os.getenv("CHILDREN_NAMES", "")
CHILDREN_NAMES: List[str] = _children_str.split(",") if _children_str else []
CHILD_SYMPTOMS: List[str] = ["😊 元気いっぱい", "🤒 お熱がある", "🤧 鼻水・咳", "🤮 お腹の調子が悪い", "🤕 怪我した", "✏️ その他"]
CHILD_CHECK_TIME: str = "07:30"

OHAYO_KEYWORDS: List[str] = ["おはよ", "おはよう"]
MESSAGE_LENGTH_LIMIT: int = 30

MENU_OPTIONS: Dict[str, List[str]] = {
    "自炊": ["カレーライス", "豚しゃぶ", "焼き魚", "うどん", "味噌汁とご飯", "野菜炒め", "オムライス"],
    "外食": ["マクドナルド", "魚べえ", "サイゼリヤ", "丸亀製麺"],
    "その他": ["スーパーの惣菜", "コンビニ", "冷凍食品", "カップ麺"]
}

# 記念日・イベント設定
IMPORTANT_DATES: List[Dict[str, Any]] = []
_events_path: str = os.path.join(BASE_DIR, "family_events.json")
if os.path.exists(_events_path):
    try:
        with open(_events_path, "r", encoding="utf-8") as f:
            IMPORTANT_DATES = json.load(f)
    except Exception as e:
        print(f"⚠️ 記念日設定の読み込みに失敗: {e}")

CHECK_ZOROME: bool = True

# 車検知キーワード
CAR_RULE_KEYWORDS: Dict[str, List[str]] = {
    "LEAVE": ["Exit", "Leave", "Out"],
    "RETURN": ["Enter", "In", "Arrive"]
}

# カメラ設定
DEFAULT_CAM_USER: str = os.getenv("CAMERA_USER", "admin")
DEFAULT_CAM_PASS: str = os.getenv("CAMERA_PASS", "")
CAMERAS: List[Dict[str, Any]] = [
    {
        "id": "VIGI_C540_Parking",
        "name": "駐車場カメラ",
        "location": "伊丹",
        "ip": os.getenv("CAMERA_IP", "192.168.1.110"),
        "port": 2020,
        "user": DEFAULT_CAM_USER,
        "pass": DEFAULT_CAM_PASS
    },
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

if CAMERAS:
    CAMERA_IP: Optional[str] = CAMERAS[0].get("ip")
    CAMERA_USER: Optional[str] = CAMERAS[0].get("user")
    CAMERA_PASS: Optional[str] = CAMERAS[0].get("pass")
else:
    CAMERA_IP, CAMERA_USER, CAMERA_PASS = None, None, None

# 監視デバイス (SwitchBot等)
MONITOR_DEVICES: List[Dict[str, Any]] = [
    # --- 🏠 伊丹 (自宅) ---
    {"id": "24587C9CCBCE", "type": "Plug Mini (JP)", "location": "伊丹", "name": "1Fのトイレ", "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}},
    {"id": "D83BDA178576", "type": "Plug Mini (JP)", "location": "伊丹", "name": "テレビ", "notify_settings": {"power_threshold_watts": 20.0, "notify_mode": "LOG_ONLY"}},
    {"id": "F09E9E9D599A", "type": "Plug Mini (JP)", "location": "伊丹", "name": "炊飯器", "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}},
    {"id": "CFBF5E92AAD0", "type": "MeterPlus", "location": "伊丹", "name": "仕事部屋", "notify_settings": {}},
    {"id": "E9BA4D43962D", "type": "MeterPlus", "location": "伊丹", "name": "居間", "notify_settings": {}},
    {"id": "F062114E225F", "type": "Motion Sensor", "location": "伊丹", "name": "人感センサー", "notify_settings": {}},
    {"id": "DE3B6D1C8AE4", "type": "Hub Mini", "location": "伊丹", "name": "ハブミニ E4", "notify_settings": {}},
    {"id": "eb66a4f83686d73815zteu", "type": "Indoor Cam", "location": "伊丹", "name": "ともやのへや", "notify_settings": {}},

    # --- 👵 高砂 (実家) ---
    {"id": "D92743516777", "type": "Contact Sensor", "location": "高砂", "name": "冷蔵庫", "notify_settings": {}},
    {"id": "C937D8CB33A3", "type": "Contact Sensor", "location": "高砂", "name": "玄関", "notify_settings": {}},
    {"id": "E07135DD95B1", "type": "Contact Sensor", "location": "高砂", "name": "お母さんの部屋", "notify_settings": {}},
    {"id": "F69BB5721955", "type": "Contact Sensor", "location": "高砂", "name": "トイレ", "notify_settings": {}},
    {"id": "F5866D92E63D", "type": "Contact Sensor", "location": "高砂", "name": "庭へのドア", "notify_settings": {}},
    {"id": "E17F2E2DA99F", "type": "MeterPlus", "location": "高砂", "name": "1Fの洗面所", "notify_settings": {}},
    {"id": "E30D45A30356", "type": "MeterPlus", "location": "高砂", "name": "リビング", "notify_settings": {}},
    {"id": "E9B20697916C", "type": "Motion Sensor", "location": "高砂", "name": "和室", "notify_settings": {}},
    {"id": "FEACA2E1797C", "type": "Hub Mini", "location": "高砂", "name": "高砂のハブミニ", "notify_settings": {}},
    {"id": "ebb1e93d271a144eaf3571", "type": "Pan/Tilt Cam", "location": "高砂", "name": "高砂の玄関", "notify_settings": {}},
]

# 給与PDFパスワード
_passwords_str: str = os.getenv("SALARY_PDF_PASSWORDS", "")
SALARY_PDF_PASSWORDS: List[str] = [p.strip() for p in _passwords_str.split(",") if p.strip()]

SALARY_IMAGE_DIR: str = os.path.join(ASSETS_DIR, "salary_images")
SALARY_DATA_DIR: str = os.path.join(BASE_DIR, "data")
SALARY_CSV_PATH: str = os.path.join(SALARY_DATA_DIR, "salary_history.csv")
BONUS_CSV_PATH: str = os.path.join(SALARY_DATA_DIR, "bonus_history.csv")

# ショッピング解析設定
SHOPPING_TARGETS: List[Dict[str, Any]] = [
    {
        "platform": "Amazon",
        "sender": "auto-confirm@amazon.co.jp",
        "subject_keywords": ["Amazon.co.jpのご注文", "注文済み", "Amazon.co.jp order"]
    },
    {
        "platform": "Rakuten",
        "sender": "order@rakuten.co.jp",
        "subject_keywords": ["注文内容ご確認", "ご注文内容の確認", "発送のご案内"]
    }
]

# 美容院・散髪予約の設定
HAIRCUT_TARGETS: List[Dict[str, Any]] = [
    {
        "platform": "HotPepperBeauty",
        "sender": "reserve@beauty.hotpepper.jp",
        "subject_keywords": ["ご予約が確定いたしました"]
    }
]
HAIRCUT_CYCLE_DAYS: int = 60

# 自転車駐車場
BICYCLE_PARKING_URL: str = "https://www.midi-kintetsu.com/mpns/pa/h-itami/teiki/index.php"

# ==========================================
# 4. 土地価格監視設定
# ==========================================
LAND_PRICE_TARGETS: List[Dict[str, Any]] = [
    {
        "city_code": "28207",     # 兵庫県伊丹市
        "city_name": "伊丹市",
        "districts": ["鈴原町"],
        "filter_chome": list(range(1, 9))
    },
    {
        "city_code": "28216",     # 兵庫県高砂市
        "city_name": "高砂市",
        "districts": ["西畑", "鍵町"],
        "filter_chome": [1]
    },
    {
        "city_code": "29201",     # 奈良県奈良市
        "city_name": "奈良市",
        "districts": ["西九条町"],
        "filter_chome": [1]
    }
]

# ==========================================
# 5. 不動産情報ライブラリ (Secrets)
# ==========================================
# ★修正点: セキュリティ保護のためハードコードを削除し、環境変数から読み込みます
REINFOLIB_API_KEY: Optional[str] = os.getenv("REINFOLIB_API_KEY")

GOOGLE_PHOTOS_CREDENTIALS: str = os.path.join(BASE_DIR, "google_photos_credentials.json")
GOOGLE_PHOTOS_TOKEN: str = os.path.join(BASE_DIR, "google_photos_token.json")
GOOGLE_PHOTOS_SCOPES: List[str] = ['https://www.googleapis.com/auth/photoslibrary']

REINFOLIB_WEB_URL: str = "https://www.reinfolib.mlit.go.jp/"

# ==========================================
# 6. NAS & Network
# ==========================================
NAS_IP: str = os.getenv("NAS_IP", "192.168.1.20")
NAS_CHECK_TIMEOUT: int = 5

# ★修正点: 環境依存パスを環境変数化
QUEST_DIST_DIR: str = os.getenv("QUEST_DIST_DIR", "/home/masahiro/develop/family-quest/dist")

FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://192.168.1.200:8000/quest")
CORS_ORIGINS: List[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    FRONTEND_URL,
]
ALLOW_ALL_ORIGINS: bool = os.getenv("ALLOW_ALL_ORIGINS", "False").lower() == "true"
if ALLOW_ALL_ORIGINS:
    CORS_ORIGINS = ["*"]

UPLOAD_DIR: str = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ==========================================
# 7. Sound & Family
# ==========================================
SOUND_DIR: str = os.path.join(ASSETS_DIR, "sounds")
if not os.path.exists(SOUND_DIR):
    os.makedirs(SOUND_DIR, exist_ok=True)

SOUND_PLAYER_CMD: str = "mpg123"
SOUND_PLAYER_ARGS: List[str] = ["-o", "pulse"]

SOUND_MAP: Dict[str, str] = {
    "level_up": "level_up.mp3",
    "quest_clear": "quest_clear.mp3",
    "medal_get": "medal_get.mp3",
    "submit": "submit.mp3",
    "approve": "approve.mp3",
    "attack_hit": "attack.mp3",
    "boss_defeat_fanfare": "fanfare.mp3",
}

FAMILY_SETTINGS: Dict[str, Any] = {
    "members": ["智矢", "涼花", "将博", "春菜"],
    "styles": {
        "智矢": {"color": "#1E90FF", "age": "5歳", "icon": "👦"},
        "涼花": {"color": "#FF69B4", "age": "2歳", "icon": "👧"},
        "将博": {"color": "#2E8B57", "age": "35歳", "icon": "👨"},
        "春菜": {"color": "#FF8C00", "age": "ママ", "icon": "👩"},
    }
}

NVR_RECORD_DIR: str = os.path.join(NAS_MOUNT_POINT, "home_system", "nvr_recordings")
ENABLE_BATTLE_EFFECT: bool = False

# ==========================================
# 8. 外部サイト監視設定 (Monitor Settings)
# ==========================================
SUUMO_SEARCH_URL: Optional[str] = os.getenv("SUUMO_SEARCH_URL")
SUUMO_MAX_BUDGET: int = 70000
SUUMO_MONITOR_INTERVAL: int = 3600

# ==========================================
# 9. 小児科予約監視設定
# ==========================================
CLINIC_MONITOR_URL: str = os.getenv("CLINIC_MONITOR_URL", "https://ssc6.doctorqube.com/itami-shounika/")
CLINIC_HTML_DIR: str = os.path.join(ASSETS_DIR, "clinic_html")
CLINIC_MONITOR_START_HOUR: int = 6
CLINIC_MONITOR_END_HOUR: int = 19
CLINIC_REQUEST_TIMEOUT: int = 10
CLINIC_USER_AGENT: str = os.getenv("CLINIC_USER_AGENT", "MyHomeSystem/1.0 (Family Health Monitor)")

# ディレクトリ自動作成
for d in [ASSETS_DIR, LOG_DIR, SALARY_IMAGE_DIR, SALARY_DATA_DIR, CLINIC_HTML_DIR]:
    try:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
    except PermissionError:
        print(f"⚠️ Warning: Failed to create directory '{d}' due to permission error.", file=sys.stderr)
    except Exception as e:
        print(f"⚠️ Warning: Unexpected error creating directory '{d}': {e}", file=sys.stderr)