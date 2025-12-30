# HOME_SYSTEM/config.py
import os
from typing import List, Dict, Optional
from dotenv import load_dotenv
import json

# .envファイルのロード
load_dotenv()

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
# 互換性のため
DISCORD_WEBHOOK_URL: Optional[str] = DISCORD_WEBHOOK_NOTIFY or os.getenv("DISCORD_WEBHOOK_URL")

# GMAIL & Gemini
GMAIL_USER: Optional[str] = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD: Optional[str] = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
SALARY_MAIL_SENDER: Optional[str] = os.getenv("SALARY_MAIL_SENDER")

# ==========================================
# 2. システム・パス設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ▼▼▼ 追加・変更 ▼▼▼
# NAS設定
NAS_MOUNT_POINT = os.getenv("NAS_MOUNT_POINT", "/mnt/nas")
NAS_PROJECT_ROOT = os.path.join(NAS_MOUNT_POINT, "home_system")

# DBは速度と安定性のためローカル(SDカード)維持を推奨
SQLITE_DB_PATH = os.path.join(BASE_DIR, "home_system.db")

# 画像データ等は容量節約のためNASへ変更
ASSETS_DIR = os.path.join(NAS_PROJECT_ROOT, "assets")

# ログはローカルのまま
LOG_DIR = os.path.join(BASE_DIR, "logs")

# DBテーブル名定義
SQLITE_TABLE_SENSOR = "device_records"
SQLITE_TABLE_OHAYO = "ohayo_records"
SQLITE_TABLE_FOOD = "food_records"
SQLITE_TABLE_DAILY = "daily_records"
SQLITE_TABLE_HEALTH = "health_records"
SQLITE_TABLE_CAR = "car_records"
SQLITE_TABLE_CHILD = "child_health_records"
SQLITE_TABLE_DEFECATION = "defecation_records"
SQLITE_TABLE_AI_REPORT = "ai_report_records"
SQLITE_TABLE_SHOPPING = "shopping_records"
SQLITE_TABLE_NAS = "nas_records"

# バックアップ対象
BACKUP_FILES = [SQLITE_DB_PATH, "config.py", ".env"]

# ==========================================
# 3. デバイス・ルール設定
# ==========================================
# 通知ターゲット (デフォルト)
NOTIFICATION_TARGET: str = os.getenv("NOTIFICATION_TARGET", "line")

# 子供設定
_children_str = os.getenv("CHILDREN_NAMES", "")
CHILDREN_NAMES: List[str] = _children_str.split(",") if _children_str else []
CHILD_SYMPTOMS = ["😊 元気いっぱい", "🤒 お熱がある", "🤧 鼻水・咳", "🤮 お腹の調子が悪い", "🤕 怪我した", "✏️ その他"]
CHILD_CHECK_TIME = "07:30"

# おはよう設定
OHAYO_KEYWORDS = ["おはよ", "おはよう"]
MESSAGE_LENGTH_LIMIT = 30

# メニュー定義
MENU_OPTIONS: Dict[str, List[str]] = {
    "自炊": ["カレーライス", "豚しゃぶ", "焼き魚", "うどん", "味噌汁とご飯", "野菜炒め", "オムライス"],
    "外食": ["マクドナルド", "魚べえ", "サイゼリヤ", "丸亀製麺"],
    "その他": ["スーパーの惣菜", "コンビニ", "冷凍食品", "カップ麺"]
}

# 記念日・イベント設定 (外部JSON読み込み)
IMPORTANT_DATES = []
_events_path = os.path.join(BASE_DIR, "family_events.json")
if os.path.exists(_events_path):
    try:
        with open(_events_path, "r", encoding="utf-8") as f:
            IMPORTANT_DATES = json.load(f)
    except Exception as e:
        print(f"⚠️ 記念日設定の読み込みに失敗: {e}")

# ゾロ目チェックをするかどうか
CHECK_ZOROME = True


# 車検知キーワード
CAR_RULE_KEYWORDS: Dict[str, List[str]] = {
    "LEAVE": ["Exit", "Leave", "Out"],
    "RETURN": ["Enter", "In", "Arrive"]
}

# カメラ設定 (後方互換性維持)
DEFAULT_CAM_USER = os.getenv("CAMERA_USER", "admin")
DEFAULT_CAM_PASS = os.getenv("CAMERA_PASS", "")
CAMERAS = [
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

# 後方互換性用変数
if CAMERAS:
    CAMERA_IP = CAMERAS[0]["ip"]
    CAMERA_USER = CAMERAS[0]["user"]
    CAMERA_PASS = CAMERAS[0]["pass"]
else:
    CAMERA_IP, CAMERA_USER, CAMERA_PASS = None, None, None

# 監視デバイス (SwitchBot等)
MONITOR_DEVICES = [
    # --- 🏠 伊丹 (自宅) ---
    # Plug
    {"id": "24587C9CCBCE", "type": "Plug Mini (JP)", "location": "伊丹", "name": "1Fのトイレ", "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}},
    {"id": "D83BDA178576", "type": "Plug Mini (JP)", "location": "伊丹", "name": "テレビ", "notify_settings": {"power_threshold_watts": 20.0, "notify_mode": "LOG_ONLY"}},
    {"id": "F09E9E9D599A", "type": "Plug Mini (JP)", "location": "伊丹", "name": "炊飯器", "notify_settings": {"power_threshold_watts": 5.0, "notify_mode": "LOG_ONLY"}},
    # Meter
    {"id": "CFBF5E92AAD0", "type": "MeterPlus", "location": "伊丹", "name": "仕事部屋", "notify_settings": {}},
    {"id": "E9BA4D43962D", "type": "MeterPlus", "location": "伊丹", "name": "居間", "notify_settings": {}},
    # Motion
    {"id": "F062114E225F", "type": "Motion Sensor", "location": "伊丹", "name": "人感センサー", "notify_settings": {}},
    # Hub
    {"id": "DE3B6D1C8AE4", "type": "Hub Mini", "location": "伊丹", "name": "ハブミニ E4", "notify_settings": {}},
    # Cam
    {"id": "eb66a4f83686d73815zteu", "type": "Indoor Cam", "location": "伊丹", "name": "ともやのへや", "notify_settings": {}},

    # --- 👵 高砂 (実家) ---
    # Contact (開閉) - ここが重要！
    {"id": "D92743516777", "type": "Contact Sensor", "location": "高砂", "name": "冷蔵庫", "notify_settings": {}},
    {"id": "C937D8CB33A3", "type": "Contact Sensor", "location": "高砂", "name": "玄関", "notify_settings": {}},
    {"id": "E07135DD95B1", "type": "Contact Sensor", "location": "高砂", "name": "お母さんの部屋", "notify_settings": {}},
    {"id": "F69BB5721955", "type": "Contact Sensor", "location": "高砂", "name": "トイレ", "notify_settings": {}},
    {"id": "F5866D92E63D", "type": "Contact Sensor", "location": "高砂", "name": "庭へのドア", "notify_settings": {}},
    # Meter
    {"id": "E17F2E2DA99F", "type": "MeterPlus", "location": "高砂", "name": "1Fの洗面所", "notify_settings": {}},
    {"id": "E30D45A30356", "type": "MeterPlus", "location": "高砂", "name": "リビング", "notify_settings": {}},
    # Motion
    {"id": "E9B20697916C", "type": "Motion Sensor", "location": "高砂", "name": "和室", "notify_settings": {}},
    # Hub
    {"id": "FEACA2E1797C", "type": "Hub Mini", "location": "高砂", "name": "高砂のハブミニ", "notify_settings": {}},
    # Cam
    {"id": "ebb1e93d271a144eaf3571", "type": "Pan/Tilt Cam", "location": "高砂", "name": "高砂の玄関", "notify_settings": {}},
]

# 給与PDFパスワード
_passwords_str = os.getenv("SALARY_PDF_PASSWORDS", "")
SALARY_PDF_PASSWORDS = [p.strip() for p in _passwords_str.split(",") if p.strip()]

# ディレクトリ・ファイルパス設定
SALARY_IMAGE_DIR = os.path.join(ASSETS_DIR, "salary_images")
SALARY_DATA_DIR = os.path.join(BASE_DIR, "data")
SALARY_CSV_PATH = os.path.join(SALARY_DATA_DIR, "salary_history.csv")
BONUS_CSV_PATH = os.path.join(SALARY_DATA_DIR, "bonus_history.csv")


# ▼ 追加: ショッピング解析設定
# プライベートな検索クエリはここで管理
SHOPPING_TARGETS = [
    {
        "platform": "Amazon",
        "sender": "auto-confirm@amazon.co.jp",
        # 「注文済み」を追加
        "subject_keywords": ["Amazon.co.jpのご注文", "注文済み", "Amazon.co.jp order"]
    },
    {
        "platform": "Rakuten",
        "sender": "order@rakuten.co.jp",
        # 「ご注文内容の確認」を追加
        "subject_keywords": ["注文内容ご確認", "ご注文内容の確認", "発送のご案内"]
    }
]

# ▼ 美容院・散髪予約の設定 (確定情報に更新)
HAIRCUT_TARGETS = [
    {
        "platform": "HotPepperBeauty",
        "sender": "reserve@beauty.hotpepper.jp",
        "subject_keywords": ["ご予約が確定いたしました"]
    }
]

# 散髪サイクルの目安 (日)
HAIRCUT_CYCLE_DAYS = 60

# 自動作成ディレクトリ
for d in [ASSETS_DIR, LOG_DIR, SALARY_IMAGE_DIR, SALARY_DATA_DIR]:
    if not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


# 自転車駐車場監視設定
BICYCLE_PARKING_URL = "https://www.midi-kintetsu.com/mpns/pa/h-itami/teiki/index.php"
SQLITE_TABLE_BICYCLE = "bicycle_parking_records"


# ==========================================
# 4. 土地価格監視設定
# ==========================================
# 国土交通省API用設定 (都道府県コード2桁 + 市区町村コード3桁)
# 伊丹市=28207, 高砂市=28216, 奈良市=29201
LAND_PRICE_TARGETS = [
    {
        "city_code": "28207",     # 兵庫県伊丹市
        "city_name": "伊丹市",
        "districts": ["鈴原町"],
        "filter_chome": list(range(1, 9)) # 1丁目〜8丁目
    },
    {
        "city_code": "28216",     # 兵庫県高砂市
        "city_name": "高砂市",
        "districts": ["西畑", "鍵町"],
        "filter_chome": [1]       # 西畑は1丁目のみ (鍵町など丁目が無いエリアは自動で通過します)
    },
    {
        "city_code": "29201",     # 奈良県奈良市
        "city_name": "奈良市",
        "districts": ["西九条町"],
        "filter_chome": [1]       # 1丁目のみ
    }
]

# ==========================================
# 5. 不動産情報ライブラリ (新・土地価格API)
# ==========================================
# 申請URL: https://www.reinfolib.mlit.go.jp/api/request/
# ここに取得したAPIキーを入力してください
REINFOLIB_API_KEY = "8fe0cddac7dc402eb1018843395734ec"


# Google Photos API設定
GOOGLE_PHOTOS_CREDENTIALS = os.path.join(BASE_DIR, "google_photos_credentials.json")
GOOGLE_PHOTOS_TOKEN = os.path.join(BASE_DIR, "google_photos_token.json")
GOOGLE_PHOTOS_SCOPES = ['https://www.googleapis.com/auth/photoslibrary']

# WebサイトのURL (通知用)
REINFOLIB_WEB_URL = "https://www.reinfolib.mlit.go.jp/"

# ==========================================
# 6. NAS設定 (BUFFALO LS720D)
# ==========================================
NAS_IP = "192.168.1.20"
NAS_MOUNT_POINT = "/mnt/nas"
NAS_CHECK_TIMEOUT = 5  # Ping等のタイムアウト(秒)


# Family Quest Frontend
# 開発環境と本番環境でパスが変わる場合は環境変数化を検討
QUEST_DIST_DIR = "/home/masahiro/develop/family-quest/dist"

# === Network & Security Settings ===

# フロントエンドのオリジン設定
# 環境変数 FRONTEND_URL が設定されていればそれを優先、なければデフォルト値を使用
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://192.168.1.200:5173")

# CORSで許可するオリジンのリスト
CORS_ORIGINS = [
    "http://localhost:5173",      # ローカル開発用 (Viteデフォルト)
    "http://127.0.0.1:5173",      # ローカル開発用 (IP指定)
    FRONTEND_URL,                 # 本番/検証環境 (LAN内など)
]

# 必要に応じて追加: "*" を許可するかどうか (セキュリティリスクがあるため、開発中以外はFalse推奨)
ALLOW_ALL_ORIGINS = os.getenv("ALLOW_ALL_ORIGINS", "False").lower() == "true"
if ALLOW_ALL_ORIGINS:
    CORS_ORIGINS = ["*"]