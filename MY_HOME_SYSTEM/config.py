# MY_HOME_SYSTEM/config.py
"""
アプリケーション設定モジュール。

目次:
    1.  環境・機能フラグ設定
    2.  認証・API設定 (Secrets)
    3.  システム・パス設定
    4.  デバイス・ルール設定
    5.  NAS & Network設定
    6.  動画処理(タイムラプス・NVR録画)設定
    7.  保持期間・クリーンアップ設定
    8.  Sound & Family設定
    9.  メモリ監視設定
    10. TVロック機能設定
    11. Alexaスキル設定
    12. ラズパイ監視(health_watch)設定
    13. NASパスの遅延解決 (Issue #330 PR-B)
    14. Family Quest: YouTubeごほうび券クールダウン設定
"""
import os
import time
import sys
import json
import logging
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from core.utils import retry_with_backoff

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
    # #384: 固定名 ".write_test" だと、scheduler 起動直後に同時に走る複数プロセスが同じ
    # ファイルを open→write→remove して衝突し(片方の os.remove が FileNotFoundError)、
    # 起動のたびにリトライ警告が出る/最悪フォールバックに落ちていた。プロセス固有の名前にする。
    test_file: str = os.path.join(base_path, f".write_test.{os.getpid()}.{time.time_ns()}")

    def _attempt() -> None:
        # 1. ディレクトリの存在確認と作成
        # マウント前の一時的なローカル作成を防ぐため、リトライごとに毎回実行する
        os.makedirs(base_path, exist_ok=True)

        # 2. 書き込み・権限テスト
        # ディレクトリが存在しても、マウント直後の不安定な状態や権限不足をここで検知
        with open(test_file, 'w') as f:
            f.write("test")

        # テストファイルのクリーンアップ
        os.remove(test_file)

    def _on_retry(attempt: int, delay: float, e: BaseException) -> None:
        logger.warning(
            f"⚠️ [Attempt {attempt + 1}/{max_retries}] Failed to access '{base_path}'. "
            f"Retrying in {delay:.0f}s... Reason: {e}"
        )

    try:
        # Exponential Backoff (1s, 2s, 4s, 8s, 16s)
        retry_with_backoff(
            _attempt,
            max_retries=max_retries,
            retryable_exceptions=(OSError, PermissionError, IOError),
            base_delay=1.0,
            max_delay=16.0,
            on_retry=_on_retry,
        )
    except (OSError, PermissionError, IOError) as e:
        logger.error(
            f"🚨 [Critical] Max retries ({max_retries}) reached. "
            f"Failed to access or initialize storage at '{base_path}'. Reason: {e}"
        )
        return False

    return True


def _get_int_env(name: str, default: int) -> int:
    """環境変数を整数として読み込む。未設定/空文字はデフォルト値、非数値は
    警告ログを出してデフォルト値にフォールバックする(#411 S-L6)。

    以前は各所で int(os.getenv(name, "default")) を直書きしており、環境変数に
    誤って空文字や非数値(例: コメント混じりの値)が設定されるとモジュール
    import時に ValueError が送出され、config 全体のロードが失敗していた。
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"⚠️ 環境変数 {name}='{raw}' は整数として解釈できません。デフォルト値 {default} を使用します。")
        return default

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
# LINE Messaging API 呼び出し(reply/push/get_profile 等)の (接続, 読み取り) タイムアウト秒。
# line-bot-sdk v3 は _request_timeout 未指定だと urllib3 に timeout=None(無期限ブロック)を
# 渡すため、api.line.me への TCP がブラックホール化した場合に BackgroundTasks の
# ワーカースレッドが永久に塞がり、anyio のスレッドプール(既定40)が枯渇すると同期 def の
# 全エンドポイント(/api/quest/* 等)まで停止する。Discord/SwitchBot 系は元々タイムアウト付き。
LINE_API_REQUEST_TIMEOUT: tuple = (5.0, 15.0)

# SwitchBot WebhookはLINEと異なり署名検証機構がないため、
# 任意で共有シークレットをクエリパラメータ(?token=...)で要求できるようにする。
# 未設定の場合は従来通り検証なし（後方互換）。
SWITCHBOT_WEBHOOK_TOKEN: Optional[str] = os.getenv("SWITCHBOT_WEBHOOK_TOKEN")
# switchbot_webhook_fix.py が SwitchBot/LINE の Webhook URL を再登録する際の公開ベースURL
# (例: https://home.example.com)。#405: 以前はスクリプト側で os.environ.get() を直接読んでいた。
WEBHOOK_BASE_URL: Optional[str] = os.getenv("WEBHOOK_BASE_URL")

# Discord Webhooks
DISCORD_WEBHOOK_ERROR: Optional[str] = os.getenv("DISCORD_WEBHOOK_ERROR")
DISCORD_WEBHOOK_REPORT: Optional[str] = os.getenv("DISCORD_WEBHOOK_REPORT")
DISCORD_WEBHOOK_NOTIFY: Optional[str] = os.getenv("DISCORD_WEBHOOK_NOTIFY")
DISCORD_WEBHOOK_URL: Optional[str] = DISCORD_WEBHOOK_NOTIFY or os.getenv("DISCORD_WEBHOOK_URL")

# Gemini
GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

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

# ASSETS_DIR はNAS上のパスであり、import時に検証するとNAS障害・マウント遅延時に
# Exponential Backoff(最悪 約31秒)で全importerをブロックしていたため、
# Issue #330 PR-Bで遅延解決(ファイル末尾のモジュール__getattr__)へ移行した。
# 利用側は従来どおり config.ASSETS_DIR で参照できる(初回アクセス時に検証・キャッシュ)。
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
SQLITE_TABLE_FOOD: str = "food_records"
SQLITE_TABLE_CAR: str = "car_records"
SQLITE_TABLE_CHILD: str = "child_health_records"
SQLITE_TABLE_DEFECATION: str = "defecation_records"
# Issue #584: このテーブルへのINSERT/UPDATE経路はこのリポジトリ内には存在しない
# (`analysis_service.load_ai_report`による読み取りのみ)。`dashboard.py`が
# `timestamp`列を新形式(core.utils.get_now_ioのISO8601)・旧形式("YYYY-MM-DD
# HH:MM:SS"のnaive文字列)の両方でパースできるよう作られている(Issue #410 L-L2)
# ことから、過去に実データが書き込まれていたと考えられ、このリポジトリ管理外の
# 外部プロセス(実機で手動運用、または別リポジトリのスクリプト)がこのテーブルへ
# 書き込む前提の設計と判断している。本リポジトリ側に書込コードを追加する対応は
# 不要(表示側のみで完結する)。
SQLITE_TABLE_AI_REPORT: str = "ai_report_records"
SQLITE_TABLE_SHOPPING: str = "shopping_records"
SQLITE_TABLE_NAS: str = "nas_records"
SQLITE_TABLE_BICYCLE: str = "bicycle_parking_records"

BACKUP_FILES: List[str] = [SQLITE_DB_PATH, "config.py", ".env", "devices.json"]

# デフォルトアセット
DEFAULT_SOUND_SOURCE: str = os.path.join(BASE_DIR, "defaults", "sounds")

# ==========================================
# 4. デバイス・ルール設定
# ==========================================
NOTIFICATION_TARGET: str = os.getenv("NOTIFICATION_TARGET", "discord")

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

# 動体検知の過剰発火を防ぐためのクールダウン（秒）
# デフォルトは60秒。.envで上書き可能。
MOTION_COOLDOWN_SEC: int = _get_int_env("MOTION_COOLDOWN_SEC", 60)

# ==========================================
# 5. NAS & Network設定
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
# ブラウザが送信する Origin ヘッダーは scheme://host[:port] のみでパスを含まない
# (Starlette の CORSMiddleware は allow_origins との完全一致で比較する)。
# FRONTEND_URL は post_boot_health_check.py 等で実際にHTTPリクエストを送る
# 完全なURL(パス込み)として使われているためパスを保持したままにし、
# CORS_ORIGINSに追加する際だけ scheme+netloc のみを取り出す。
_frontend_origin = "{0.scheme}://{0.netloc}".format(urlparse(FRONTEND_URL))
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
    _frontend_origin,
]
ALLOW_ALL_ORIGINS: bool = os.getenv("ALLOW_ALL_ORIGINS", "False").lower() == "true"
if ALLOW_ALL_ORIGINS:
    CORS_ORIGINS = ["*"]

# Issue #547: reset_game.py が管理者向けリセットAPI(POST /api/quest/admin/reset_user)を
# 呼び出す際のサーバーのベースURL。reset_game.pyはunified_serverと同じホストで実行される
# 前提の対話スクリプトのため、既定値はループバックアドレスとする(FRONTEND_URLはLAN内の
# 他端末からのアクセスを想定したホストのIP指定のため、この用途には流用しない)。
RESET_GAME_API_BASE_URL: str = os.getenv("RESET_GAME_API_BASE_URL", "http://127.0.0.1:8000")

UPLOAD_DIR: str = os.path.join(BASE_DIR, "uploads")
# M-9-3: /api/quest/upload にファイルサイズ上限が無く、巨大アップロードで
# ディスクを圧迫し得た。アバター画像用途を想定した上限とする。
# M15/Issue #325: フロントエンド(family-quest/src/components/ui/AvatarUploader.tsx の
# MAX_AVATAR_SIZE_BYTES)と同じ5MBに揃えている。変更時は両方を更新すること。
UPLOAD_MAX_FILE_SIZE_MB: int = _get_int_env("UPLOAD_MAX_FILE_SIZE_MB", 5)

# ==========================================
# 6. 動画処理(タイムラプス・NVR録画)設定
# ==========================================
# NVR録画ファイルのベースディレクトリ
NVR_RECORD_DIR: str = os.path.join(NAS_MOUNT_POINT, "home_system", "nvr_recordings")

# タイムラプス生成設定
# (monitors/smart_timelapse_generator.py が
#  getattr(config, "TIMELAPSE_...", デフォルト値) で参照する。以前はここに対応する
#  定数が定義されておらず、常にハードコードされたデフォルト値へフォールバックしていた)
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

# ==========================================
# 7. 保持期間・クリーンアップ設定
# ==========================================
# NVR録画・カメラスナップショットの保持日数（これを超えたファイルはnas_monitor.pyが自動削除）
RECORDING_RETENTION_DAYS: int = _get_int_env("RECORDING_RETENTION_DAYS", 30)
# #359: 録画VODのHLSセグメントキャッシュ(BASE_DIR/data/hls_streams/vod)の保持日数
HLS_VOD_RETENTION_DAYS: int = _get_int_env("HLS_VOD_RETENTION_DAYS", 3)
# DBバックアップの保持日数
DB_BACKUP_RETENTION_DAYS: int = _get_int_env("DB_BACKUP_RETENTION_DAYS", 30)
DB_BACKUPS_DIR: str = os.path.join(NAS_PROJECT_ROOT, "db_backups")

# ==========================================
# 8. Sound & Family設定
# ==========================================
# SOUND_DIR はASSETS_DIR(遅延解決)配下のため、モジュール__getattr__で遅延解決する

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
# 9. メモリ監視設定
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
# 10. TVロック機能設定
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
# 11. Alexaスキル設定
# ==========================================
# Alexa Developer Consoleでスキルを作成すると発行される "amzn1.ask.skill.xxxx" 形式のID。
# 設定すると、routers/alexa_router.py 経由のリクエストの context.System.application.applicationId
# がこの値と一致するかを ask-sdk-core が検証し、他人のスキルからのリクエストを拒否する。
# 未設定でも動作するが(署名検証だけになる)、本番では設定を強く推奨。
ALEXA_SKILL_ID: Optional[str] = os.getenv("ALEXA_SKILL_ID")

# ==========================================
# 12. ラズパイ監視(health_watch)設定
# ==========================================
# Issue #339: 層2(異常検知時のClaude自動調査)のフックスクリプトの絶対パス。
# 設定すると monitors/health_watch.py が異常検知時(通知抑制の内側)に
# このスクリプトを異常サマリつきで fire-and-forget 起動する。
# 未設定(既定)なら層1の検知・通知のみで、従来と完全に同じ挙動。
# 想定値: <リポジトリ>/MY_HOME_SYSTEM/scripts/claude_investigate.sh
# (Claude Code CLI・ghの実機セットアップが済むまでは未設定のままにすること。
#  docs/runbooks/raspi_claude_log_monitoring.md の層2セクション参照)
HEALTH_WATCH_INVESTIGATE_HOOK: Optional[str] = os.getenv("HEALTH_WATCH_INVESTIGATE_HOOK")

# ==========================================
# 13. NASパスの遅延解決 (Issue #330 PR-B)
# ==========================================
# ASSETS_DIR はNAS上のパスであり、以前はモジュールimport時に
# ensure_safe_path_with_backoff(書き込みテスト + Exponential Backoff、最悪 約31秒)を
# 実行していたため、NAS障害・マウント遅延時にconfigをimportするだけの
# テスト・CLIツール・cronスクリプトまで長時間ブロックしていた。
# PEP 562のモジュール__getattr__により「初回アクセス時に検証・作成し、
# 結果をモジュール属性としてキャッシュする」方式へ変更した。
# 利用側の書き方(config.ASSETS_DIR 等)は従来と変わらない。
# サーバー起動時はunified_server.pyのlifespanが prewarm_nas_paths() を呼び、
# 従来どおり起動時点で検証が走る。

# ASSETS_DIR 配下で自動作成するサブディレクトリ
_ASSETS_SUBDIRS_TO_CREATE: List[str] = []

# ASSETS_DIR から派生する遅延解決パス (属性名 -> ASSETS_DIRからの相対パス)
_ASSETS_DERIVED_PATHS: Dict[str, str] = {
    "SOUND_DIR": "sounds",
}


def _resolve_assets_dir() -> str:
    """ASSETS_DIRを検証・解決し、配下の自動作成サブディレクトリも整える。"""
    path = ensure_safe_path_with_backoff(
        os.path.join(NAS_PROJECT_ROOT, "assets"), "assets"
    )
    for sub in _ASSETS_SUBDIRS_TO_CREATE:
        subdir = os.path.join(path, sub)
        try:
            os.makedirs(subdir, exist_ok=True)
        except Exception as e:
            logger.warning(f"⚠️ Warning: Failed to ensure directory existence '{subdir}': {e}")
    return path


def __getattr__(name: str) -> str:
    """NAS依存パス定数の遅延解決 (PEP 562)。

    通常の属性解決(モジュールglobals)に失敗した場合のみ呼ばれるため、
    一度解決して globals() に書き込んだ後は本関数を経由しない(=キャッシュ)。
    テストが monkeypatch.setattr/delattr で上書き・再解決させることも可能。
    """
    if name == "ASSETS_DIR":
        value = _resolve_assets_dir()
    elif name in _ASSETS_DERIVED_PATHS:
        # ASSETS_DIR の解決(必要なら)を経由して派生パスを組み立てる
        assets_dir = globals().get("ASSETS_DIR") or __getattr__("ASSETS_DIR")
        value = os.path.join(assets_dir, _ASSETS_DERIVED_PATHS[name])
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = value
    return value


def prewarm_nas_paths() -> None:
    """NAS依存の遅延パスをまとめて解決する(サーバー起動時のプリウォーム用)。

    unified_server.pyのlifespanから呼ばれ、遅延化前と同じく起動時点で
    NASの検証・フォールバック判定を済ませる。失敗してもensure_safe_path_with_backoff
    自体がローカルへフォールバックするため例外は送出しない。
    """
    for name in ("ASSETS_DIR", *_ASSETS_DERIVED_PATHS):
        getattr(sys.modules[__name__], name)
    logger.info("✅ NAS依存パスのプリウォーム完了")

# ==========================================
# 14. Family Quest: YouTubeごほうび券クールダウン設定
# ==========================================
# 連続視聴による目の負担を防ぐため、YouTube系のごほうび券(user_inventory経由で
# 使用するreward_master.reward_id)を1枚使用してから次の1枚を使用できるまでの
# クールダウン対象IDを指定する(services/quest_service.py InventoryService.use_item)。
# 10.のTV_UNLOCK_QUEST_IDSと同じ「カンマ区切りの整数」形式。
# 既定値はquest_data.pyのYouTube報酬(10:00/30:00/60:00)の現在のreward_id(10,11,12)。
_youtube_reward_ids_str: str = os.getenv("YOUTUBE_REWARD_IDS", "10,11,12")
YOUTUBE_REWARD_IDS: List[int] = []
if _youtube_reward_ids_str:
    try:
        YOUTUBE_REWARD_IDS = [int(r.strip()) for r in _youtube_reward_ids_str.split(",") if r.strip().isdigit()]
    except Exception as e:
        logger.warning(f"⚠️ YOUTUBE_REWARD_IDS parse error: {e}")

# クールダウンの実際の適用開始日(YYYY-MM-DD、JST基準)。いきなり制限がかかると
# 子どもが困惑するため、この日を迎えるまではクールダウンを実際には強制せず、
# family-quest側で「この日から変わるよ」という予告バナーのみを表示する
# (services/quest_service.py の _is_youtube_cooldown_enforced が判定)。
# 既定値はこの機能を追加した日(2026-09-05)の1週間後。実際にリリースする日程に
# 合わせて調整すること。パース失敗時は安全側(=即時強制)にフォールバックする。
from datetime import date as _date
_youtube_cooldown_enforce_from_str: str = os.getenv("YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM", "2026-09-12")
try:
    YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM: _date = _date.fromisoformat(_youtube_cooldown_enforce_from_str)
except Exception as e:
    logger.warning(f"⚠️ YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM parse error: {e}. 即時強制にフォールバックします。")
    YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM = _date(2000, 1, 1)
