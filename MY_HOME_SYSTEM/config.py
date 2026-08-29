# MY_HOME_SYSTEM/config.py
"""
アプリケーション設定モジュール。

目次:
    1.  環境・機能フラグ設定
    2.  認証・API設定 (Secrets)
    3.  システム・パス設定
    4.  デバイス・ルール設定
    5.  給与(Salary)設定
    6.  ショッピング・美容院予約 監視設定
    7.  土地価格監視設定
    8.  Google Photos 連携設定
    9.  不動産情報(REINFOLIB)設定
    10. NAS & Network設定
    11. 動画処理(タイムラプス・NVR録画)設定
    12. 保持期間・クリーンアップ設定
    13. Sound & Family設定
    14. 外部サイト監視設定 (SUUMO)
    15. 小児科予約監視設定 (Clinic Monitor)
    16. メモリ監視設定
    17. TVロック機能設定
    18. Alexaスキル設定
"""
import os
import sys
import json
import time
import logging
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

# ==========================================
# Bootstrap Helpers (Logger / Storage)
# ==========================================
# 起動シーケンス初期の段階で循環参照を避けるため、標準のloggingで名前空間を合わせる
logger = logging.getLogger("config_init")

def verify_and_initialize_storage(base_path: str, max_retries: int = 5) -> bool:
    """
    指定されたストレージパスの存在確認、ディレクトリ作成、および書き込み権限のテストを行う。
    NAS等のマウント遅延を考慮し、Exponential Backoffによるリトライを実行する。

    Args:
        base_path (str): 確認対象のベースディレクトリパス
        max_retries (int): 最大リトライ回数。デフォルトは5。

    Returns:
        bool: ストレージの初期化と書き込みテストが成功した場合はTrue、最終的に失敗した場合はFalse。
    """
    test_file: str = os.path.join(base_path, ".write_test")

    for attempt in range(max_retries + 1):
        try:
            # 1. ディレクトリの存在確認と作成
            # マウント前の一時的なローカル作成を防ぐため、リトライごとに毎回実行する
            os.makedirs(base_path, exist_ok=True)

            # 2. 書き込み・権限テスト
            # ディレクトリが存在しても、マウント直後の不安定な状態や権限不足をここで検知
            with open(test_file, 'w') as f:
                f.write("test")

            # テストファイルのクリーンアップ
            os.remove(test_file)

            if attempt > 0:
                logger.info(f"✅ Retry {attempt}: Successfully accessed '{base_path}'.")

            return True

        except (OSError, PermissionError, IOError) as e:
            if attempt < max_retries:
                # Exponential Backoff (1s, 2s, 4s, 8s, 16s)
                wait_time: int = 2 ** attempt
                logger.warning(
                    f"⚠️ [Attempt {attempt + 1}/{max_retries}] Failed to access '{base_path}'. "
                    f"Retrying in {wait_time}s... Reason: {e}"
                )
                time.sleep(wait_time)
            else:
                logger.error(
                    f"🚨 [Critical] Max retries ({max_retries}) reached. "
                    f"Failed to access or initialize storage at '{base_path}'. Reason: {e}"
                )
                return False

    return False

if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[%(levelname)s] %(name)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

def ensure_safe_path_with_backoff(
    preferred_path: str,
    fallback_name: str,
    max_retries: int = 5
) -> str:
    """
    NASなどのマウント遅延を考慮してディレクトリを検証し、
    アクセスできない場合はローカルのフォールバックディレクトリを返す。

    Args:
        preferred_path (str): 本来保存したいパス (例: /mnt/nas/home_system/assets)
        fallback_name (str): フォールバック時のディレクトリ名
        max_retries (int): 最大リトライ回数 (デフォルト: 5)

    Returns:
        str: 安全に書き込み可能なパス（成功時は preferred_path、失敗時は fallback_path）
    """
    # 新設した検証・初期化関数に処理を委譲
    is_valid: bool = verify_and_initialize_storage(preferred_path, max_retries)

    if is_valid:
        return preferred_path

    # 最大リトライ回数を超過した場合のフォールバック処理
    base_dir: str = os.path.dirname(os.path.abspath(__file__))
    fallback_root: str = os.path.join(base_dir, "temp_fallback")
    fallback_path: str = os.path.join(fallback_root, fallback_name)

    try:
        os.makedirs(fallback_path, exist_ok=True)
        logger.error(
            f"🚨 【NAS障害・介入要求】\n"
            f"Falling back to local: '{fallback_path}' instead of '{preferred_path}'."
        )
        return fallback_path
    except Exception as fatal_e:
        logger.error(f"❌ [Critical] Failed to create fallback path '{fallback_path}': {fatal_e}")
        # フォールバックディレクトリすら作成できない異常事態のフェイルセーフ
        return preferred_path

# .envファイルのロード
load_dotenv()

# ==========================================
# Type Definitions with Pydantic
# ==========================================
class CameraConfig(BaseModel):
    id: str
    name: str
    nas_folder: Optional[str] = None  # 追加: NASの物理フォルダ名
    location: str
    ip: str
    port: int = 2020
    user: Optional[str] = None
    password: Optional[str] = Field(None, alias="pass")
    rtsp_url: Optional[str] = None
    enabled: bool = True

class NotifySettings(BaseModel):
    power_threshold_watts: Optional[float] = None
    notify_mode: str = "LOG_ONLY"
    target: Optional[str] = None

class DeviceConfig(BaseModel):
    id: str
    type: str
    location: str
    name: str
    notify_settings: NotifySettings = Field(default_factory=NotifySettings)

# ==========================================
# 1. 環境・機能フラグ設定
# ==========================================
ENV: str = os.getenv("ENV", "development")
# BTスピーカー運用の有効/無効。Falseの間はpost_boot_health_checkのSpeakerチェックが
# BT確認をスキップしサウンドカード確認にフォールバックする。
# 再有効化する場合はTrueにした上で、OS側の `sudo systemctl enable --now bluetooth`
# と起動時自動接続(tools/connect_speaker.sh の定期実行)の整備が必要。
ENABLE_BLUETOOTH: bool = False
# Anker SoundCore 2 (tools/connect_speaker.sh, tools/keep_alive_anker.sh と同一デバイス)
SPEAKER_BLUETOOTH_MAC: str = os.getenv("SPEAKER_BLUETOOTH_MAC", "F4:4E:FC:B6:65:D4")

# ==========================================
# 2. 認証・API設定 (Secrets)
# ==========================================
SWITCHBOT_API_TOKEN: Optional[str] = os.getenv("SWITCHBOT_API_TOKEN")
SWITCHBOT_API_SECRET: Optional[str] = os.getenv("SWITCHBOT_API_SECRET")
SWITCHBOT_API_HOST = "https://api.switch-bot.com"
NATURE_REMO_ACCESS_TOKEN: Optional[str] = os.getenv("NATURE_REMO_ACCESS_TOKEN")
NATURE_REMO_ACCESS_TOKEN_TAKASAGO: Optional[str] = os.getenv("NATURE_REMO_ACCESS_TOKEN_TAKASAGO")

LINE_CHANNEL_ACCESS_TOKEN: Optional[str] = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET: Optional[str] = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID: Optional[str] = os.getenv("LINE_USER_ID")
LINE_PARENTS_GROUP_ID: str = os.getenv("LINE_PARENTS_GROUP_ID", "")

# SwitchBot WebhookはLINEと異なり署名検証機構がないため、
# 任意で共有シークレットをクエリパラメータ(?token=...)で要求できるようにする。
# 未設定の場合は従来通り検証なし（後方互換）。
SWITCHBOT_WEBHOOK_TOKEN: Optional[str] = os.getenv("SWITCHBOT_WEBHOOK_TOKEN")

# Discord Webhooks
DISCORD_WEBHOOK_ERROR: Optional[str] = os.getenv("DISCORD_WEBHOOK_ERROR")
DISCORD_WEBHOOK_ERROR_CAM: Optional[str] = os.getenv("DISCORD_WEBHOOK_ERROR_CAM")
DISCORD_WEBHOOK_REPORT: Optional[str] = os.getenv("DISCORD_WEBHOOK_REPORT")
DISCORD_WEBHOOK_NOTIFY: Optional[str] = os.getenv("DISCORD_WEBHOOK_NOTIFY")
DISCORD_WEBHOOK_URL: Optional[str] = DISCORD_WEBHOOK_NOTIFY or os.getenv("DISCORD_WEBHOOK_URL")

# GMAIL & Gemini
GMAIL_USER: Optional[str] = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD: Optional[str] = os.getenv("GMAIL_APP_PASSWORD")
GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
SALARY_MAIL_SENDER: Optional[str] = os.getenv("SALARY_MAIL_SENDER")

# 不動産情報 (WebURLは 9. 不動産情報(REINFOLIB)設定 を参照)
REINFOLIB_API_KEY: Optional[str] = os.getenv("REINFOLIB_API_KEY")

# ==========================================
# 3. システム・パス設定
# ==========================================
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
FALLBACK_ROOT: str = os.path.join(BASE_DIR, "temp_fallback")

# NAS設定
NAS_MOUNT_POINT: str = os.getenv("NAS_MOUNT_POINT", "/mnt/nas")
NAS_PROJECT_ROOT: str = os.path.join(NAS_MOUNT_POINT, "home_system")

# DB & Assets (バックオフ付きの安全なパス取得を適用)
# CI/テストからは環境変数 SQLITE_DB_PATH でDBパスを上書きできるようにする
# (未設定時は従来通りのデフォルトパスを使用)
SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH") or os.path.join(BASE_DIR, "home_system.db")

ASSETS_DIR: str = ensure_safe_path_with_backoff(
    os.path.join(NAS_PROJECT_ROOT, "assets"),
    "assets"
)
LOG_DIR: str = ensure_safe_path_with_backoff(
    os.path.join(BASE_DIR, "logs"),
    "logs"
)
DEVICES_JSON_PATH: str = os.path.join(BASE_DIR, "devices.json")

# --- DBテーブル名定義 ---
SQLITE_TABLE_SENSOR: str = "device_records"
SQLITE_TABLE_SWITCHBOT_LOGS: str = "switchbot_meter_logs"
SQLITE_TABLE_POWER_USAGE: str = "power_usage"
SQLITE_TABLE_DAILY_LOGS: str = "daily_logs"

# Legacy/Specific Tables
SQLITE_TABLE_OHAYO: str = "ohayo_records"
SQLITE_TABLE_FOOD: str = "food_records"
SQLITE_TABLE_HEALTH: str = "health_records"
SQLITE_TABLE_CAR: str = "car_records"
SQLITE_TABLE_CHILD: str = "child_health_records"
SQLITE_TABLE_DEFECATION: str = "defecation_records"
SQLITE_TABLE_AI_REPORT: str = "ai_report_records"
SQLITE_TABLE_SHOPPING: str = "shopping_records"
SQLITE_TABLE_NAS: str = "nas_records"
SQLITE_TABLE_BICYCLE: str = "bicycle_parking_records"

BACKUP_FILES: List[str] = [SQLITE_DB_PATH, "config.py", ".env", "devices.json"]

# デフォルトアセット
DEFAULT_ASSETS_DIR: str = os.path.join(BASE_DIR, "defaults")
DEFAULT_SOUND_SOURCE: str = os.path.join(DEFAULT_ASSETS_DIR, "sounds")

# ==========================================
# 4. デバイス・ルール設定
# ==========================================
NOTIFICATION_TARGET: str = os.getenv("NOTIFICATION_TARGET", "discord")

# --- 子供設定 ---
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

# --- 記念日・イベント設定 ---
IMPORTANT_DATES: List[Dict[str, Any]] = []
_events_path: str = os.path.join(BASE_DIR, "family_events.json")
if os.path.exists(_events_path):
    try:
        with open(_events_path, "r", encoding="utf-8") as f:
            IMPORTANT_DATES = json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ 記念日設定の読み込みに失敗: {e}")

CHECK_ZOROME: bool = True

# --- 車検知キーワード ---
CAR_RULE_KEYWORDS: Dict[str, List[str]] = {
    "LEAVE": ["Exit", "Leave", "Out"],
    "RETURN": ["Enter", "In", "Arrive"]
}

# --- デバイス設定の読み込み (devices.json) ---
CAMERAS: List[Dict[str, Any]] = []
MONITOR_DEVICES: List[Dict[str, Any]] = []

if os.path.exists(DEVICES_JSON_PATH):
    try:
        with open(DEVICES_JSON_PATH, "r", encoding="utf-8") as f:
            _devices_data = json.load(f)
            if "cameras" in _devices_data:
                CAMERAS = [CameraConfig(**c).model_dump(by_alias=True) for c in _devices_data["cameras"]]
            if "monitor_devices" in _devices_data:
                MONITOR_DEVICES = [DeviceConfig(**d).model_dump() for d in _devices_data["monitor_devices"]]
    except ValidationError as ve:
        logger.error(f"❌ devices.json Validation Error: {ve}")
    except Exception as e:
        logger.warning(f"⚠️ devices.json load failed: {e}")
else:
    logger.info(f"ℹ️ devices.json not found at {DEVICES_JSON_PATH}. Running without device config.")

# カメラ互換性用変数
if CAMERAS:
    CAMERA_IP: Optional[str] = CAMERAS[0].get("ip")
    CAMERA_USER: Optional[str] = CAMERAS[0].get("user")
    CAMERA_PASS: Optional[str] = CAMERAS[0].get("pass")
else:
    CAMERA_IP, CAMERA_USER, CAMERA_PASS = None, None, None

# 動体検知の過剰発火を防ぐためのクールダウン（秒）
# デフォルトは60秒。.envで上書き可能。
MOTION_COOLDOWN_SEC: int = int(os.getenv("MOTION_COOLDOWN_SEC", "60"))

# ==========================================
# 5. 給与(Salary)設定
# ==========================================
_passwords_str: str = os.getenv("SALARY_PDF_PASSWORDS", "")
SALARY_PDF_PASSWORDS: List[str] = [p.strip() for p in _passwords_str.split(",") if p.strip()]

SALARY_IMAGE_DIR: str = os.path.join(ASSETS_DIR, "salary_images")
SALARY_DATA_DIR: str = os.path.join(BASE_DIR, "data")
SALARY_CSV_PATH: str = os.path.join(SALARY_DATA_DIR, "salary_history.csv")
BONUS_CSV_PATH: str = os.path.join(SALARY_DATA_DIR, "bonus_history.csv")

# ==========================================
# 6. ショッピング・美容院予約 監視設定
# ==========================================
# --- ショッピング解析設定 ---
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

# --- 美容院・散髪予約の設定 ---
HAIRCUT_TARGETS: List[Dict[str, Any]] = [
    {
        "platform": "HotPepperBeauty",
        "sender": "reserve@beauty.hotpepper.jp",
        "subject_keywords": ["ご予約が確定いたしました"]
    }
]
HAIRCUT_CYCLE_DAYS: int = 60

# --- 自転車駐車場 ---
BICYCLE_PARKING_URL: str = "https://www.midi-kintetsu.com/mpns/pa/h-itami/teiki/index.php"

# ==========================================
# 7. 土地価格監視設定
# ==========================================
LAND_PRICE_TARGETS: List[Dict[str, Any]] = [
    {
        "city_code": "28207",
        "city_name": "伊丹市",
        "districts": ["鈴原町"],
        "filter_chome": list(range(1, 9))
    },
    {
        "city_code": "28216",
        "city_name": "高砂市",
        "districts": ["西畑", "鍵町"],
        "filter_chome": [1]
    },
    {
        "city_code": "29201",
        "city_name": "奈良市",
        "districts": ["西九条町"],
        "filter_chome": [1]
    }
]

# ==========================================
# 8. Google Photos 連携設定
# ==========================================
GOOGLE_PHOTOS_CREDENTIALS: str = os.path.join(BASE_DIR, "google_photos_credentials.json")
GOOGLE_PHOTOS_TOKEN: str = os.path.join(BASE_DIR, "google_photos_token.json")
GOOGLE_PHOTOS_SCOPES: List[str] = ['https://www.googleapis.com/auth/photoslibrary']

# ==========================================
# 9. 不動産情報(REINFOLIB)設定
# ==========================================
# APIキー(REINFOLIB_API_KEY)は 2. 認証・API設定 (Secrets) を参照
REINFOLIB_WEB_URL: str = "https://www.reinfolib.mlit.go.jp/"

# ==========================================
# 10. NAS & Network設定
# ==========================================
NAS_IP: str = os.getenv("NAS_IP", "192.168.1.20")
NAS_CHECK_TIMEOUT: int = 5
# 書き込みテストがタイムアウトした際の再試行回数。
# autofsのアイドルアンマウント後の初回アクセスやNAS本体のディスクスピンアップは
# NAS_CHECK_TIMEOUT(5秒)を超えることがあるため、単発のタイムアウトで即座に
# 障害と判定せず、Exponential Backoffで再試行する。
NAS_WRITE_CHECK_RETRIES: int = 3

_default_quest_dir = os.path.join(os.path.dirname(BASE_DIR), "family-quest", "dist")
QUEST_DIST_DIR: str = os.getenv("QUEST_DIST_DIR", _default_quest_dir)

FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://192.168.1.200:8000/quest")
# M-8-2: 以前はここ(config.py)と unified_server.py の両方に別々のCORS許可
# オリジンリストがあり、実際に使われるのは unified_server.py 側のハードコード
# だけだったため、config.py側やALLOW_ALL_ORIGINS環境変数を変更しても
# CORS設定に一切反映されない「死に設定」になっていた。ここに一本化する。
CORS_ORIGINS: List[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8501",   # Streamlitダッシュボード
    "http://192.168.1.200:5173",  # LAN内フロントエンド開発サーバー
    "https://m-mhts.com",      # Cloudflare Tunnel公開ドメイン
    FRONTEND_URL,
]
ALLOW_ALL_ORIGINS: bool = os.getenv("ALLOW_ALL_ORIGINS", "False").lower() == "true"
if ALLOW_ALL_ORIGINS:
    CORS_ORIGINS = ["*"]

UPLOAD_DIR: str = os.path.join(BASE_DIR, "uploads")
# M-9-3: /api/quest/upload にファイルサイズ上限が無く、巨大アップロードで
# ディスクを圧迫し得た。アバター画像用途を想定し余裕を持って10MBとする。
UPLOAD_MAX_FILE_SIZE_MB: int = int(os.getenv("UPLOAD_MAX_FILE_SIZE_MB", "10"))

# ==========================================
# 11. 動画処理(タイムラプス・NVR録画)設定
# ==========================================
# テンポラリ動画保存先ディレクトリ (バックオフ付きの安全なパス取得を適用)
TMP_VIDEO_DIR: str = ensure_safe_path_with_backoff(
    os.path.join(NAS_PROJECT_ROOT, "tmp_video"),
    "tmp_video"
)

# NVR録画ファイルのベースディレクトリ
NVR_RECORD_DIR: str = os.path.join(NAS_MOUNT_POINT, "home_system", "nvr_recordings")

# タイムラプス生成設定
# (monitors/smart_timelapse_generator.py, monitors/scheduled_timelapse.py が
#  getattr(config, "TIMELAPSE_...", デフォルト値) で参照する。以前はここに対応する
#  定数が定義されておらず、常にハードコードされたデフォルト値へフォールバックしていた)
from datetime import time as _dt_time

TIMELAPSE_FPS_ANALYZE: int = 1
TIMELAPSE_WIDTH: int = 320
TIMELAPSE_HEIGHT: int = 180
TIMELAPSE_BG_HISTORY: int = 120
TIMELAPSE_BG_VAR_THRESH: int = 16
TIMELAPSE_MORPH_KERNEL_SIZE: int = 3
TIMELAPSE_MIN_AREA_THRESHOLD: int = 300
TIMELAPSE_ROI_X: int = 0
TIMELAPSE_ROI_Y: int = 0
TIMELAPSE_ROI_W: int = TIMELAPSE_WIDTH
TIMELAPSE_ROI_H: int = TIMELAPSE_HEIGHT
TIMELAPSE_GAP_THRESH: int = 5
TIMELAPSE_BUFFER_SEC: int = 3
TIMELAPSE_SPEEDUP_FACTOR: int = 4
TIMELAPSE_DEBUG_FFMPEG: bool = False
TIMELAPSE_FAST_STREAM_COPY_MODE: bool = False
TIMELAPSE_FONT_FILE: str = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
TIMELAPSE_MAX_FILE_SIZE_MB: int = 22

TIMELAPSE_CAMERAS: Dict[str, str] = {
    "entrance": os.path.join(NVR_RECORD_DIR, "entrance"),
    "garden": os.path.join(NVR_RECORD_DIR, "garden"),
    "parking": os.path.join(NVR_RECORD_DIR, "parking"),
}
TIMELAPSE_SCHEDULES: Dict[str, tuple] = {
    "morning": (_dt_time(7, 50), _dt_time(8, 30), _dt_time(8, 30), _dt_time(9, 0)),
    "evening": (_dt_time(15, 0), _dt_time(16, 0), _dt_time(16, 0), _dt_time(16, 30)),
}
TIMELAPSE_FPS: str = "15"
TIMELAPSE_BITRATE: str = "1500k"
TIMELAPSE_MAXRATE: str = "2000k"
TIMELAPSE_SEGMENT_TIME: str = "40"

# ==========================================
# 12. 保持期間・クリーンアップ設定
# ==========================================
# NVR録画・カメラスナップショットの保持日数（これを超えたファイルはnas_monitor.pyが自動削除）
RECORDING_RETENTION_DAYS: int = int(os.getenv("RECORDING_RETENTION_DAYS", "30"))
# DBバックアップの保持日数
DB_BACKUP_RETENTION_DAYS: int = int(os.getenv("DB_BACKUP_RETENTION_DAYS", "30"))
DB_BACKUPS_DIR: str = os.path.join(NAS_PROJECT_ROOT, "db_backups")

# ==========================================
# 13. Sound & Family設定
# ==========================================
SOUND_DIR: str = os.path.join(ASSETS_DIR, "sounds")

SOUND_PLAYER_CMD: str = "mpg123"
SOUND_PLAYER_ARGS: List[str] = ["-o", "pulse"]

SOUND_MAP: Dict[str, str] = {
    "level_up": "level_up.mp3",
    "quest_clear": "quest_clear.mp3",
    "medal_get": "medal_get.mp3",
    "submit": "submit.mp3",
    "approve": "approve.mp3",
}

# 注意: "members" のキー名（実名）は LINE Bot 側のメッセージ文字列マッチング等
# (例: handlers/line_handler.py の `if child in msg_text`) で機能的に使用されているため、
# 安易に匿名化・外部化すると多数の呼び出し箇所を壊すリスクがある。
# そのため名前自体はこのファイルに残しているが、年齢などの個人を特定しうる付加情報は
# family_members.local.json (gitignore対象) から読み込み、tracked source には
# プレースホルダーのみを置くようにしている。
FAMILY_SETTINGS: Dict[str, Any] = {
    "members": ["智矢", "涼花", "将博", "春菜"],
    "styles": {
        "智矢": {"color": "#1E90FF", "age": None, "icon": "👦"},
        "涼花": {"color": "#FF69B4", "age": None, "icon": "👧"},
        "将博": {"color": "#2E8B57", "age": None, "icon": "👨"},
        "春菜": {"color": "#FF8C00", "age": None, "icon": "👩"},
    }
}

# family_members.local.json が存在すれば、年齢等の表示専用データをマージする。
# ファイルが無くても (CI・新規チェックアウト等) 上記プレースホルダーのままアプリは起動できる。
_family_local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "family_members.local.json")
if os.path.exists(_family_local_path):
    try:
        with open(_family_local_path, "r", encoding="utf-8") as _f:
            _family_local_overrides = json.load(_f)
        for _name, _overrides in _family_local_overrides.items():
            if _name in FAMILY_SETTINGS["styles"] and isinstance(_overrides, dict):
                FAMILY_SETTINGS["styles"][_name].update(_overrides)
    except Exception as _e:
        logger.warning(f"family_members.local.json の読み込みに失敗しました（プレースホルダーで続行します）: {_e}")

# ==========================================
# 14. 外部サイト監視設定 (SUUMO)
# ==========================================
SUUMO_SEARCH_URL: Optional[str] = os.getenv("SUUMO_SEARCH_URL")
SUUMO_MAX_BUDGET: int = 70000
SUUMO_MONITOR_INTERVAL: int = 3600

# ==========================================
# 15. 小児科予約監視設定 (Clinic Monitor)
# ==========================================
CLINIC_MONITOR_URL: str = os.getenv("CLINIC_MONITOR_URL", "https://ssc6.doctorqube.com/itami-shounika/")
CLINIC_HTML_DIR: str = os.path.join(ASSETS_DIR, "clinic_html")
CLINIC_STATS_CSV: str = os.path.join(ASSETS_DIR, "clinic_stats.csv")
CLINIC_GRAPH_PATH: str = os.path.join(ASSETS_DIR, "clinic_trend.png")

CLINIC_MONITOR_START_HOUR: int = int(os.getenv("CLINIC_MONITOR_START_HOUR", "8"))
CLINIC_MONITOR_END_HOUR: int = int(os.getenv("CLINIC_MONITOR_END_HOUR", "19"))
CLINIC_REQUEST_TIMEOUT: int = int(os.getenv("CLINIC_REQUEST_TIMEOUT", "10"))
CLINIC_USER_AGENT: str = os.getenv("CLINIC_USER_AGENT", "MyHomeSystem/1.0 (Family Health Monitor)")

# 自動作成ディレクトリへの追加 (printをloggerに置き換え)
for d in [ASSETS_DIR, LOG_DIR, SALARY_IMAGE_DIR, SALARY_DATA_DIR, CLINIC_HTML_DIR]:
    try:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
    except Exception as e:
        logger.warning(f"⚠️ Warning: Failed to ensure directory existence '{d}': {e}")

# ==========================================
# 16. メモリ監視設定
# ==========================================
# システム全体のメモリ使用率警告閾値 (%)
MEMORY_ALERT_PERCENT: float = 85.0
# MY_HOME_SYSTEM関連プロセスのメモリ上限 (MB)
PROCESS_MEMORY_LIMIT_MB: float = 500.0
# 通知スパム防止のためのクールダウンタイム (秒) - 例: 2時間
MEMORY_ALERT_COOLDOWN_SEC: int = 7200
# 最終通知時刻を記録する一時ファイルパス
MEMORY_ALERT_LAST_NOTIFY_FILE: str = os.path.join(FALLBACK_ROOT, "last_memory_alert.txt")

# ==========================================
# 17. TVロック機能設定
# ==========================================
_tv_unlock_quest_ids_str: str = os.getenv("TV_UNLOCK_QUEST_IDS", "")
TV_UNLOCK_QUEST_IDS: List[int] = []
if _tv_unlock_quest_ids_str:
    try:
        # 数字のみを抽出してint型に変換
        TV_UNLOCK_QUEST_IDS = [int(q.strip()) for q in _tv_unlock_quest_ids_str.split(",") if q.strip().isdigit()]
    except Exception as e:
        logger.warning(f"⚠️ TV_UNLOCK_QUEST_IDS parse error: {e}")

TV_PLUG_DEVICE_ID: Optional[str] = os.getenv("TV_PLUG_DEVICE_ID")

# ==========================================
# 18. Alexaスキル設定
# ==========================================
# Alexa Developer Consoleでスキルを作成すると発行される "amzn1.ask.skill.xxxx" 形式のID。
# 設定すると、routers/alexa_router.py 経由のリクエストの context.System.application.applicationId
# がこの値と一致するかを ask-sdk-core が検証し、他人のスキルからのリクエストを拒否する。
# 未設定でも動作するが(署名検証だけになる)、本番では設定を強く推奨。
ALEXA_SKILL_ID: Optional[str] = os.getenv("ALEXA_SKILL_ID")
