#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Production Grade Batch Downloader (v2.4.0 Universal Support)
-------------------------------------------------
Features:
- Multi-List Support: Automatically processes all files in 'list/' directory.
- Smart Organization: Creates subfolders based on list filenames.
- Atomic File Writes & Robust Error Handling.
- Download History Management.
- Discord Notifications.
- Schedule: 02:00 - 06:00.
- Universal Support: Uses yt-dlp for ALL supported sites (not just YouTube).
- Specialized Scraping: Specific logic for 'missav'.
- Bot Detection Safeguards: Jittered per-task delays (extra-conservative for
  YouTube/missav), yt-dlp request throttling, optional cookies file, a
  per-run task cap so a single run can't burst through a huge backlog
  (round-robin across source lists so no single list starves the others),
  a cross-run cooldown once bot detection is suspected (manually clearable
  with --clear-cooldown), a startup warning when yt-dlp itself is stale,
  and an immediate session abort on 403/429/503 / "Sign in to confirm"
  style errors or Cloudflare-style challenge pages.
"""

import os
import sys
import time
import re
import random
import shutil
import datetime
import logging
import signal
import fcntl
import requests
from collections import defaultdict
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Any, Set, NamedTuple, Dict, Iterable
from dataclasses import dataclass, field

from file_utils import sanitize_filename as _shared_sanitize_filename
from file_utils import DiscordCircuitBreaker
from file_utils import resolve_my_home_system_root
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from concurrent.futures import ThreadPoolExecutor, as_completed

# External Libraries
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yt_dlp

# ==========================================
# 0. 環境設定 & ロギング
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("Downloader")

FORCE_MODE = "--force" in sys.argv
CLEAR_COOLDOWN_MODE = "--clear-cooldown" in sys.argv

CURRENT_DIR = Path(__file__).resolve().parent
# 品質: プロジェクトルート解決をfile_utils.resolve_my_home_system_rootへ集約。
# notification_service が見つからない場合は下の except ImportError で無効化
# されるだけなので、ここで解決に失敗しても他の環境で安全に動く。
PROJECT_ROOT = resolve_my_home_system_root(CURRENT_DIR)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

def _standalone_send_discord_webhook(messages, image_data=None, channel="notify") -> bool:
    """MY_HOME_SYSTEM(LINE Bot SDKやconfig.py、DBを要する)を持たない単独環境向けの
    簡易Discord Webhook送信フォールバック。DISCORD_WEBHOOK_ERROR/DISCORD_WEBHOOK_NOTIFY
    (未設定時はDISCORD_WEBHOOK_URL)を直接参照し、追加の依存関係なしでテキスト通知のみ送る。
    """
    url = None
    if channel == "error":
        url = os.getenv("DISCORD_WEBHOOK_ERROR") or os.getenv("DISCORD_WEBHOOK_URL")
    else:
        url = os.getenv("DISCORD_WEBHOOK_NOTIFY") or os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        return False
    text = "\n".join(
        (m.get("text", "") if isinstance(m, dict) else str(m)) for m in messages
    )
    try:
        resp = requests.post(url, json={"content": text[:2000]}, timeout=CONFIG.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"⚠️ Discord Webhook送信に失敗しました: {e}")
        return False

try:
    from services.notification_service import _send_discord_webhook
except ImportError:
    logger.warning(
        "⚠️ Notification Service not found. "
        "DISCORD_WEBHOOK_NOTIFY/DISCORD_WEBHOOK_ERROR(またはDISCORD_WEBHOOK_URL)による"
        "簡易Discord通知にフォールバックします（いずれも未設定なら通知は送信されません）。"
    )
    _send_discord_webhook = _standalone_send_discord_webhook

def _resolve_cookies_file() -> Optional[Path]:
    """YouTube等のボット検知回避用Cookieファイルを解決する。

    環境変数 YOUTUBE_COOKIES_FILE が設定されていてもファイルが存在しない場合は
    警告ログを出し、Cookie無し（未設定）の状態にフォールバックする。
    """
    cookies_env = os.getenv("YOUTUBE_COOKIES_FILE")
    if not cookies_env:
        return None
    cookies_path = Path(cookies_env)
    if not cookies_path.exists():
        logger.warning(f"⚠️ YOUTUBE_COOKIES_FILE で指定されたファイルが見つかりません: {cookies_path}")
        return None
    return cookies_path

# ==========================================
# 1. コンフィグレーション
# ==========================================
@dataclass(frozen=True)
class AppConfig:
    RESTRICT_TIME: bool = not FORCE_MODE
    START_HOUR: int = 2
    END_HOUR: int = 6
    MIN_FREE_SPACE_GB: int = 50

    # 【追加】機能フラグ: 環境変数で制御可能にする (デフォルトはFalse=無効のまま維持)
    ENABLE_YOUTUBE_DL: bool = os.getenv("ENABLE_YOUTUBE_DL", "false").lower() == "true"
    BASE_SAVE_DIR: Path = Path(os.getenv("VIDEO_SAVE_DIR", "/mnt/nas/ddd"))
    LIST_FILE_PATH: Path = CURRENT_DIR / "list.txt"
    LIST_DIR_PATH: Path = CURRENT_DIR / "list"
    HISTORY_FILE_PATH: Path = CURRENT_DIR / "history.txt"
    LOCK_FILE_PATH: Path = CURRENT_DIR / ".batch_download_discord.lock"
    NAS_MOUNT_POINT: Path = Path("/mnt/nas")
    NAS_MARKER_FILE: str = ".mounted"
    # NASを経由せずローカルディスク(外付けHDD等)に直接保存する単独環境向け。
    # falseにするとverify_nas_mount()自体をスキップし、NAS未マウントでも起動できる。
    REQUIRE_NAS_MOUNT: bool = os.getenv("DDD_REQUIRE_NAS_MOUNT", "true").lower() == "true"
    # missavのHLSフラグメント(数千個の小ファイル、動画1本あたり数GB)を一時保存
    # する先。NAS上のBASE_SAVE_DIR配下に置くと、autofsのアイドルアンマウント後の
    # 再マウント遅延やNAS本体側の応答遅延（本リポジトリのnas_monitor関連の過去の
    # 調査で判明済み）が、大量の小ファイルへの書き込み直後の読み込みで顕在化し、
    # yt-dlp側で"fragment not found"として一部フラグメントが欠落する実害が
    # 実機で確認された。ローカルディスク（NASを経由しない）に隔離することで
    # この種のマウント遅延の影響を受けないようにする。
    # なお、tempfile.gettempdir()(多くの環境で/tmp)をそのまま既定値にすると、
    # /tmpがtmpfs(RAMディスク)で運用されているシステム（一部のRaspberry Pi OS
    # 構成を含む）では、動画1本分(数GB)の書き込みでメモリを圧迫し、OOMや
    # SSH切断を引き起こしうる。そのためCURRENT_DIR（本スクリプトの設置先、
    # list.txt/history.txt等と同じ実ディスク上のディレクトリ）を既定値とする。
    LOCAL_TMP_DIR: Path = Path(os.getenv("DDD_LOCAL_TMP_DIR", str(CURRENT_DIR / "tmp_fragments")))
    # LOCAL_TMP_DIRの空き容量がこれを下回る場合、フラグメント書き込みで
    # ディスクを圧迫する前に安全側でダウンロードを中断する。
    LOCAL_TMP_MIN_FREE_SPACE_GB: int = int(os.getenv("DDD_LOCAL_TMP_MIN_FREE_SPACE_GB", "10"))

    # セグメント取得等のHTTPタイムアウト(秒)。単身赴任先PC等、自宅回線より
    # 低速な回線では既定の20秒だと大きめのHLSセグメントが間に合わずタイムアウト
    # →連続失敗でレート制限とみなされ処理中断、が起きうるため環境変数で調整可能にする
    # (未設定時は従来通り20秒=自宅ラズパイ側の挙動は変わらない)。
    REQUEST_TIMEOUT: int = int(os.getenv("DDD_REQUEST_TIMEOUT", "20"))
    MAX_RETRIES: int = 3
    # #397: HLSセグメント1個あたりの取得試行回数と、リトライ前の初回待機秒
    # (指数バックオフ: 1秒→2秒)。数千セグメント中1つの一時的なタイムアウトで
    # 数GBのダウンロードが丸ごと破棄されるのを防ぐ。BotDetectionError は
    # リトライ対象外(即座にセッション中断)。
    SEGMENT_DOWNLOAD_MAX_ATTEMPTS: int = 3
    SEGMENT_RETRY_BASE_DELAY: float = 1.0

    # 【追加】ボット検知/レート制限対策
    # タスク間隔: 固定秒数だと機械的なアクセスパターンとして検知されやすいため、
    # サイトごとにランダムなジッターを持たせる（YouTube/missavはより保守的な間隔にする）。
    DEFAULT_SLEEP_RANGE: Tuple[float, float] = (5.0, 10.0)
    YOUTUBE_SLEEP_RANGE: Tuple[float, float] = (8.0, 20.0)
    MISSAV_SLEEP_RANGE: Tuple[float, float] = (8.0, 20.0)
    # 連続失敗数がこの値に達したら、レート制限/ブロックの可能性を疑って処理全体を中断する
    CONSECUTIVE_FAILURE_THRESHOLD: int = 3
    # Discord Webhookへの通知が連続でこの回数失敗したら、Webhookが機能していないと
    # 判断してそれ以降の送信をスキップする(サーキットブレーカー)
    DISCORD_CIRCUIT_BREAKER_THRESHOLD: int = 3
    # yt-dlp自体のリクエスト間スリープ（メタデータ取得等、内部リクエストの間隔を空ける）
    YTDLP_SLEEP_INTERVAL: float = 3.0
    YTDLP_MAX_SLEEP_INTERVAL: float = 8.0
    # Cookie未設定のままだと多くの動画で「Sign in to confirm you're not a bot」に
    # 遭遇しやすくなる。環境変数 YOUTUBE_COOKIES_FILE でブラウザからエクスポートした
    # cookies.txt（Netscape形式）を指定可能（未設定ならCookie無しで動作する）。
    YOUTUBE_COOKIES_FILE: Optional[Path] = field(default_factory=_resolve_cookies_file)
    # yt-dlpの例外メッセージにこれらの文字列が含まれる場合、ボット検知/レート制限と
    # 判断し、個別タスクのスキップではなくセッション全体を即座に中断する。
    # 注: "429"/"403"/"503" は生のステータスコードとしての一致だが、
    # NetworkManagerのセッションはstatus_forcelist=[500,502,503,504]で
    # 自動リトライするため、503が続いた場合ここに届く例外メッセージは
    # 実際には requests.exceptions.RetryError の
    # "too many 503 error responses" のような文言になる。"503"を含めておくことで
    # このリトライ尽き後のメッセージもボット検知として拾えるようにしている。
    # #396: 以前の "sign in to confirm" は、yt-dlpの年齢制限メッセージ
    # "Sign in to confirm your age. This video may be inappropriate for some users."
    # にも部分一致し、年齢制限動画1本でセッション中断+12時間クールダウンに
    # 入っていた。ボット検知に固有の "not a bot" まで含む文言に絞る
    # (アポストロフィは _is_bot_detection_error 側で ' に正規化して比較する)。
    BOT_DETECTION_MARKERS: Tuple[str, ...] = (
        "sign in to confirm you're not a bot",
        "confirm you're not a bot",
        "429",
        "403",
        "503",
        "too many requests",
    )
    # #396: これらの文言を含むメッセージはボット検知ではなく動画個別の事情
    # (年齢制限等)であり、当該タスクのスキップに留める。マーカー判定より優先する。
    BOT_DETECTION_EXCLUDED_MARKERS: Tuple[str, ...] = (
        "confirm your age",
    )
    # missav等、requestsで直接HTMLを取得するスクレイピング先向け。
    # これらのステータスコードやページ内マーカーはCloudflare等のボット対策サービスが
    # 典型的に返す応答であり、通常の「ページ構成変更で抽出失敗」とは区別して扱う。
    SCRAPING_BLOCK_STATUS_CODES: Tuple[int, ...] = (403, 429, 503)
    SCRAPING_BLOCK_PAGE_MARKERS: Tuple[str, ...] = (
        "just a moment",
        "checking your browser",
        "attention required! | cloudflare",
        "cf-browser-verification",
        "access denied",
    )
    # 1回の実行で処理するタスク数の上限。ジッターを入れても「1晩で数百件」のような
    # 突発的な大量アクセスそのものが異常なパターンとして見えてしまうため、実行あたりの
    # 総量を絞り、残りは（パージされない限り）翌回の実行へ持ち越す。0以下で上限なし。
    MAX_TASKS_PER_RUN: int = 30
    # ボット検知を検知した場合、その晩だけでなく一定時間はNASクールダウンさせる。
    # cron等で「毎晩同じ時刻に再試行→また検知される」を防ぐための実行間クールダウン。
    BOT_DETECTION_COOLDOWN_HOURS: float = 12.0
    BOT_DETECTION_COOLDOWN_FILE: Path = CURRENT_DIR / ".bot_detection_cooldown"
    # yt-dlpのバージョンは YYYY.MM.DD 形式。YouTube側の変更に追従できていない
    # （＝古い）yt-dlpは、ボット検知云々以前にダウンロード失敗の最大要因になるため、
    # この日数を超えて更新されていなければ起動時に警告する。
    YTDLP_STALENESS_WARN_DAYS: int = 45

    # 抽出パターン (Specialized Scraping)
    # missavはm3u8形式かつJS難読化されているため、正規表現のリストではなく専用関数で解析します
    URL_PATTERNS: List[Tuple[str, str]] = field(default_factory=list)

    SHOW_PROGRESS_BAR: bool = sys.stdout.isatty()

    @property
    def nas_marker_path(self) -> Path:
        return self.NAS_MOUNT_POINT / self.NAS_MARKER_FILE

CONFIG = AppConfig()


class BotDetectionError(Exception):
    """YouTube等からボット検知/レート制限（429やSign-in要求等）を検知した際に送出する。

    通常のダウンロード失敗（そのタスクだけスキップして続行）とは区別し、
    セッション全体を即座に中断すべき明確なシグナルとして扱う。
    """
    pass


def _is_bot_detection_error(exc: Exception) -> bool:
    # M-7-2: "403"/"429"/"503" のような数字だけのマーカーを単純な部分文字列
    # マッチ(in)で判定すると、エラーメッセージに埋め込まれた動画ID等の
    # 英数字列(例: "...AbC403XyZ...")に偶然含まれる数字列にまで誤爆し、
    # BOT_DETECTION_COOLDOWN_HOURS(12時間)ものセッション全停止を誤って
    # 引き起こし得た。数字のみのマーカーは単語境界(\b)で厳密に判定し、
    # フレーズマーカーは従来通り部分文字列一致とする。
    # #396: yt-dlpのメッセージは "you’re"(U+2019) のような引用符を使うことがある
    # ため、ASCIIのアポストロフィに正規化してからマーカーと比較する。
    message = str(exc).lower().replace("’", "'")
    # #396: 年齢制限("Sign in to confirm your age")等、ボット検知ではないことが
    # 明確な文言を含む場合は、マーカーに一致しても誤検知として扱わない。
    if any(excluded in message for excluded in CONFIG.BOT_DETECTION_EXCLUDED_MARKERS):
        return False
    for marker in CONFIG.BOT_DETECTION_MARKERS:
        if marker.isdigit():
            if re.search(rf"\b{re.escape(marker)}\b", message):
                return True
        elif marker in message:
            return True
    return False


def _round_robin_flatten(groups: Iterable[List["DownloadTask"]]) -> List["DownloadTask"]:
    """複数グループのリストを、グループ順ではなくラウンドロビンで1本のリストに平坦化する。

    MAX_TASKS_PER_RUNで先頭から打ち切る前提の呼び出し元があるため、単純に
    グループを連結すると「収集順が先のリストファイルだけが毎回上限を使い切り、
    他のリストファイルが慢性的に後回しになる」飢餓状態が起こり得る。各グループから
    1件ずつ順番に取り出すことで、上限で打ち切られてもソース間で公平に分配される。
    """
    result: List["DownloadTask"] = []
    materialized = [list(g) for g in groups]
    max_len = max((len(g) for g in materialized), default=0)
    for i in range(max_len):
        for g in materialized:
            if i < len(g):
                result.append(g[i])
    return result


def _normalize_url(url: str) -> str:
    """URLのフラグメント(#以降)を除去して正規化する。

    MissAVの検索結果画面からURLをコピーすると
    'https://missav.live/dm18/ja/xxx-000#<検索セッションのハッシュ>_search' の
    ようにフラグメントが付与される。フラグメントはHTTPリクエストには送信され
    ず動画ページ自体は同一だが、素の文字列比較をしている履歴管理・重複排除・
    保存ファイル名生成がこれを別URL/別名として扱ってしまう。そのためlist.txt
    等から読み込む時点でフラグメントを取り除き、検索画面URLと実際の動画URLを
    同一のものとして扱えるようにする。
    """
    scheme, netloc, path, query, _fragment = urlsplit(url)
    return urlunsplit((scheme, netloc, path, query, ""))


def _packer_base_n_digits(num: int, radix: int) -> str:
    """p,a,c,k,e,d形式のJSパッカーが使う復元関数(JS側の"e")と同じ規則で、
    数値numをradix進数の文字列表現に変換する(D-L5)。

    JS側の実装:
        e = function(c) {
            return (c < a ? '' : e(parseInt(c / a)))
                + ((c = c % a) > 35 ? String.fromCharCode(c + 29) : c.toString(36))
        }
    各桁(0〜radix-1)は、35以下ならbase36の数字/小文字(0-9a-z)、36以上なら
    大文字(A-Z、String.fromCharCode(c+29)で得られる)で表現される。radixの
    大きさに関わらず桁の文字集合は最大62種(0-9a-zA-Z)に固定される点がポイントで、
    radix自体をbase36の桁数(36種)と取り違えると、radixが36以外(典型的には62)の
    ページで誤った単語に置換されてしまう。
    """
    def digit_char(d: int) -> str:
        if d > 35:
            return chr(d + 29)
        return "0123456789abcdefghijklmnopqrstuvwxyz"[d]

    if num == 0:
        return "0"
    digits = []
    while num > 0:
        digits.append(digit_char(num % radix))
        num //= radix
    return "".join(reversed(digits))


def _looks_like_block_page(html: str) -> bool:
    """取得したHTMLがCloudflare等のボット検知チャレンジページかを判定する。

    このようなページはHTTPステータス200で返ってくることも多く、
    ステータスコードだけでは検知できないためページ本文の内容で判定する。
    """
    lowered = html.lower()
    return any(marker in lowered for marker in CONFIG.SCRAPING_BLOCK_PAGE_MARKERS)


class DownloadTask(NamedTuple):
    url: str
    source_name: str

# ==========================================
# 2. マネージャー & ユーティリティ
# ==========================================
_discord_circuit_breaker = DiscordCircuitBreaker(failure_threshold=CONFIG.DISCORD_CIRCUIT_BREAKER_THRESHOLD)

class DiscordNotifier:
    @staticmethod
    def send(text: str, is_error: bool = False) -> None:
        if _discord_circuit_breaker.is_open:
            # Webhookへの連続送信失敗を検知しているため、無駄なリクエストを
            # 重ねないよう以降の送信をスキップする。
            logger.warning(f"⚠️ Discord Webhookへの連続送信失敗を検知しているため、通知をスキップします: {text[:50]}")
            return
        channel = 'error' if is_error else 'notify'
        message = {"type": "text", "text": text}
        try:
            sent = _send_discord_webhook([message], channel=channel)
        except Exception as e:
            logger.error(f"⚠️ Discord通知エラー: {e}", exc_info=True)
            sent = False
        if sent:
            _discord_circuit_breaker.record_success()
        else:
            _discord_circuit_breaker.record_failure()

class HistoryManager:
    @staticmethod
    def load_history() -> Set[str]:
        history = set()
        if CONFIG.HISTORY_FILE_PATH.exists():
            try:
                with open(CONFIG.HISTORY_FILE_PATH, "r", encoding="utf-8") as f:
                    history = {line.strip() for line in f if line.strip()}
            except Exception as e:
                # M-7-1: 読み込み失敗を握りつぶすと、既にダウンロード済みのURLが
                # 全て「未ダウンロード」扱いになり、全件の再ダウンロード・再通知の
                # 嵐を引き起こす。方針として安全側(空の履歴として続行)には倒すが、
                # 原因調査ができるよう必ずログには残す。
                logger.error(f"⚠️ 履歴ファイルの読み込みに失敗しました: {e}", exc_info=True)
        return history

    @staticmethod
    def add_history(url: str) -> None:
        try:
            with open(CONFIG.HISTORY_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(f"{url}\n")
        except Exception as e:
            # M-7-1: 書き込み失敗を握りつぶすと、このURLは次回実行時も
            # 「未ダウンロード」のままになり再ダウンロード・再通知が続く。
            # ここで処理自体を止めるほどではないため続行するが、ログには残す。
            logger.error(f"⚠️ 履歴ファイルへの書き込みに失敗しました (url={url}): {e}", exc_info=True)

class CooldownManager:
    """ボット検知発生後の実行間クールダウンを管理するクラス。

    cron等で毎晩同じ時刻に実行される運用を想定し、検知直後の1回だけでなく
    「クールダウン期限」をファイルへ永続化することで、次回以降の実行が
    期限内であれば処理そのものをスキップするようにする。
    """

    @staticmethod
    def is_in_cooldown() -> Optional[datetime.datetime]:
        """クールダウン中であれば解除予定時刻を、そうでなければNoneを返す。"""
        path = CONFIG.BOT_DETECTION_COOLDOWN_FILE
        if not path.exists():
            return None
        try:
            until = datetime.datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            # 壊れたクールダウンファイルは安全側（＝クールダウンしない）に倒す
            return None
        return until if datetime.datetime.now() < until else None

    @staticmethod
    def trigger_cooldown() -> None:
        until = datetime.datetime.now() + datetime.timedelta(hours=CONFIG.BOT_DETECTION_COOLDOWN_HOURS)
        try:
            # アトミック書き込み: 書き込み中断で壊れたファイルが残ると
            # is_in_cooldown() 側のパース失敗＝安全側（クールダウンしない）に倒れて
            # しまうため、他の状態ファイルと同じtmp経由replaceパターンにしておく。
            tmp_path = CONFIG.BOT_DETECTION_COOLDOWN_FILE.with_suffix('.tmp')
            tmp_path.write_text(until.isoformat(), encoding="utf-8")
            tmp_path.replace(CONFIG.BOT_DETECTION_COOLDOWN_FILE)
            logger.info(f"🧊 クールダウンを設定しました（解除予定: {until.strftime('%Y-%m-%d %H:%M:%S')}）")
        except OSError as e:
            logger.error(f"⚠️ クールダウンファイルの書き込みに失敗しました: {e}", exc_info=True)

    @staticmethod
    def clear() -> None:
        try:
            CONFIG.BOT_DETECTION_COOLDOWN_FILE.unlink(missing_ok=True)
        except OSError:
            pass

class NetworkManager:
    @staticmethod
    def create_session() -> requests.Session:
        session = requests.Session()
        retries = Retry(total=CONFIG.MAX_RETRIES, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
        session.mount("http://", HTTPAdapter(max_retries=retries))
        session.mount("https://", HTTPAdapter(max_retries=retries))
        session.headers.update({'User-Agent': 'Mozilla/5.0 ... Chrome/120.0.0.0 Safari/537.36'})
        return session

class FileSystemManager:
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        return _shared_sanitize_filename(filename)

    @staticmethod
    def ensure_dir(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except PermissionError:
            DiscordNotifier.send(f"❌ 権限エラー: {path}", is_error=True)
            return False
        except OSError as e:
            # #236: PermissionError以外のOSError(読み取り専用マウントのErrno 30、
            # NAS切断時のErrno 5、ディスクフル時のErrno 28等)はここで捕捉されず、
            # 専用のDiscord通知を経由しないままrun_lockedのexcept Exceptionまで
            # 伝播し、インフラ障害の原因究明が遅れていた。extract_youtube_urls.pyの
            # process_subscriptions(#185)と同様にOSError全般を捕捉する。
            DiscordNotifier.send(f"❌ ディレクトリ作成エラー: {path} ({e})", is_error=True)
            return False

    @staticmethod
    def sweep_stale_fragment_dirs() -> None:
        """#398: クラッシュ・強制終了等で未クリーンアップのまま残った
        CONFIG.LOCAL_TMP_DIR配下の "*.fragments.tmp" ディレクトリを一掃する。

        通常は_download_with_ytdlpのfinally節でtmp_dirごとに削除されるが、
        プロセスがSIGKILL等でfinallyすら実行できずに終了した場合、数GB規模の
        フラグメント断片(SDカード等、Piのローカルディスク上)が残り続け、
        LOCAL_TMP_MIN_FREE_SPACE_GBチェックにより後続の全ダウンロードが
        失敗する形で顕在化する。ロック取得後(_run_locked冒頭)に呼び出す前提
        (他プロセスとの競合が無いことが保証された状態でのみ安全に一掃できる)。
        """
        if not CONFIG.LOCAL_TMP_DIR.exists():
            return
        for stale_dir in CONFIG.LOCAL_TMP_DIR.glob("*.fragments.tmp"):
            try:
                shutil.rmtree(stale_dir)
                logger.info(f"🧹 残留フラグメントディレクトリを削除しました: {stale_dir}")
            except OSError as e:
                logger.warning(f"⚠️ 残留フラグメントディレクトリの削除に失敗しました ({stale_dir}): {e}")

    @staticmethod
    def check_disk_space(path: Path, min_free_gb: Optional[int] = None) -> bool:
        threshold_gb = CONFIG.MIN_FREE_SPACE_GB if min_free_gb is None else min_free_gb
        try:
            check_path = path
            while not check_path.exists():
                check_path = check_path.parent
                if check_path == check_path.parent: break
            total, used, free = shutil.disk_usage(check_path)
            if (free // (2**30)) < threshold_gb:
                DiscordNotifier.send(f"⚠️ DISK FULL ({path}): 残り {free // (2**30)}GB", is_error=True)
                return False
            return True
        except Exception as e:
            logger.error(f"⚠️ ディスク容量チェックに失敗しました（安全のためダウンロードを中断します）: {e}", exc_info=True)
            return False

class SystemHealthChecker:
    @staticmethod
    def is_within_time_window() -> bool:
        if not CONFIG.RESTRICT_TIME: return True
        return CONFIG.START_HOUR <= datetime.datetime.now().hour < CONFIG.END_HOUR

    @staticmethod
    def verify_nas_mount() -> bool:
        if not CONFIG.REQUIRE_NAS_MOUNT:
            return True
        if not CONFIG.NAS_MOUNT_POINT.exists() or not CONFIG.nas_marker_path.exists():
            DiscordNotifier.send("⛔ CRITICAL: NASマウントエラー", is_error=True)
            return False
        return True
    
    @staticmethod
    def check_dependencies() -> None:
        if shutil.which("ffmpeg") is None:
            logger.warning("⚠️ ffmpeg not found.")
        SystemHealthChecker.check_yt_dlp_freshness()

    @staticmethod
    def check_yt_dlp_freshness() -> None:
        """yt-dlpのバージョン（YYYY.MM.DD形式）が古すぎないか警告する。

        YouTube側の仕様変更への追従が遅れたyt-dlpは、ボット検知以前に
        単純な抽出失敗（403等）の最大要因になるため、更新を促す。
        バージョン文字列が想定形式でない場合は判定せず静かにスキップする。
        """
        try:
            installed = datetime.datetime.strptime(yt_dlp.version.__version__, "%Y.%m.%d")
        except (ValueError, AttributeError):
            return

        age_days = (datetime.datetime.now() - installed).days
        if age_days > CONFIG.YTDLP_STALENESS_WARN_DAYS:
            logger.warning(
                f"⚠️ yt-dlpのバージョンが古い可能性があります "
                f"({yt_dlp.version.__version__} / 約{age_days}日前)。"
                "YouTube側の仕様変更に伴う抽出失敗やブロックのリスクが上がるため、"
                "'pip install -U yt-dlp' での更新を推奨します。"
            )

# ==========================================
# 3. ダウンロード戦略 (Strategy Pattern)
# ==========================================
class DownloadStrategy(ABC):
    def __init__(self, save_base_dir: Path, session: requests.Session):
        self.save_base_dir = save_base_dir
        self.session = session

    @abstractmethod
    def download(self, task: DownloadTask) -> bool:
        pass

    def _determine_save_dir(self, source_name: str, category: str = "others") -> Optional[Path]:
        if source_name == "list":
            target_dir = self.save_base_dir / category
        else:
            target_dir = self.save_base_dir / category / source_name
        
        if not FileSystemManager.ensure_dir(target_dir): return None
        if not FileSystemManager.check_disk_space(target_dir): return None
        return target_dir

    def _should_skip(self, filepath: Path) -> bool:
        if filepath.exists() and filepath.stat().st_size > 0:
            logger.info(f"⏭️ Skip: {filepath.name}")
            return True
        return False

# ★UniversalYtDlpStrategy
class UniversalYtDlpStrategy(DownloadStrategy):
    def download(self, task: DownloadTask) -> bool:
        logger.info(f"🎥 Universal処理: {task.url} (List: {task.source_name})")
        
        # YouTubeの場合は "youtube" フォルダ、それ以外は "others" フォルダに分類
        category = "youtube" if "youtube.com" in task.url or "youtu.be" in task.url else "others"
        
        target_dir = self._determine_save_dir(task.source_name, category)
        if not target_dir: return False

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            # D-L1: 保存先ディレクトリ(target_dir、リストファイル名由来のsource_name
            # を含みうる)をouttmpl文字列へf-stringで直接埋め込むと、source_nameに
            # '%'が含まれる場合にyt-dlpのテンプレート展開(%(...)s)と衝突し、
            # "the following fields are missing"等のテンプレートエラーで
            # ダウンロードが失敗しうる。'paths'オプションでディレクトリを分離し、
            # outtmplはファイル名部分のみのテンプレートにする。
            'paths': {'home': str(target_dir)},
            'outtmpl': '%(title)s.%(ext)s',
            'merge_output_format': 'mp4',
            'quiet': True, 'no_warnings': True, 'nopart': False,
            # M-7-3: リスト1行がプレイリストURL(またはチャンネルURL)だった場合、
            # noplaylistが無いとyt-dlpがその1タスクの中で全件を無制限にダウンロード
            # してしまい、MAX_TASKS_PER_RUNによる1回あたりの上限governanceが
            # まるごと迂回されてしまう。単一動画のみを対象にする。
            'noplaylist': True,
            # 動画タイトルがそのままファイル名になるため、極端に長いタイトルで
            # ext4等のファイル名長制限（255バイト）に抵触して失敗するのを防ぐ。
            # #175: yt-dlpのtrim_file_nameは文字数ベース(no_ext[:trim_file_name]の
            # 単純なスライス)であり、バイト数を保証しない。UTF-8で1文字3バイトの
            # 日本語では、以前の150文字は最大450バイトとなり255バイト上限を
            # 容易に超過しうる不十分な値だった(約85文字超で失敗)。拡張子
            # (merge_output_formatで固定される".mp4"等、最大5バイト程度)分の
            # 余白を見込み、日本語(3バイト/文字)でも255バイトに収まる80文字に
            # 変更する(80*3+5=245バイト、安全マージンあり)。
            'trim_file_name': 80,
            # ボット検知対策: yt-dlp自身が発行する内部リクエスト（メタデータ取得等）の
            # 間にもランダムなスリープを挟み、機械的なアクセスパターンを避ける。
            'sleep_interval_requests': CONFIG.YTDLP_SLEEP_INTERVAL,
            'sleep_interval': CONFIG.YTDLP_SLEEP_INTERVAL,
            'max_sleep_interval': CONFIG.YTDLP_MAX_SLEEP_INTERVAL,
        }
        if CONFIG.YOUTUBE_COOKIES_FILE is not None:
            ydl_opts['cookiefile'] = str(CONFIG.YOUTUBE_COOKIES_FILE)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(task.url, download=False)
                filename = Path(ydl.prepare_filename(info)).with_suffix('.mp4')

                if self._should_skip(filename): return True

                logger.info(f"📥 DL開始: {info.get('title')}")
                # D-L2: 以前はここで改めてydl.download([task.url])を呼んでおり、
                # 直前のextract_info(download=False)と合わせてメタデータ取得の
                # ネットワークリクエストが2回発生していた（ボット検知対策として
                # 抑えているはずのアクセス回数を自ら増やしてしまっていた）。
                # 既に取得済みのinfoをprocess_ie_resultへ渡し、再抽出せずに
                # ダウンロードを実行する。
                ydl.process_ie_result(info, download=True)
                DiscordNotifier.send(f"✅ 動画保存完了\nファイル: `{filename.name}`")
                return True
        except Exception as e:
            logger.error(f"⚠️ Universal DL エラー: {e}", exc_info=True)
            if _is_bot_detection_error(e):
                # 429やSign-in要求はこのタスクだけの問題ではなくIP/アカウント単位の
                # 制限である可能性が高いため、通常の失敗として握りつぶさず上位へ伝播させる。
                raise BotDetectionError(f"{task.url}: {e}") from e
            return False

# ★スクレイピングが必要な特定サイト専用 (missav用)
class ScrapingStrategy(DownloadStrategy):
    def download(self, task: DownloadTask) -> bool:
        category = "missav"
        target_dir = self._determine_save_dir(task.source_name, category)
        if not target_dir: return False

        html = self._fetch_html(task.url)
        if not html: return False

        m3u8_url = self._extract_m3u8_url(html)
        if not m3u8_url:
            logger.warning("⚠️ M3U8リンクの抽出に失敗しました。ページ構成が変更された可能性があります。")
            return False

        # URLからファイル名を生成（例: snos-314-uncensored-leak.mp4）
        video_id = task.url.split('?')[0].rstrip('/').split('/')[-1] or f"vid_{int(time.time())}"
        filename = FileSystemManager.sanitize_filename(video_id) + ".mp4"
        final_path = target_dir / filename

        # 過去の中断（クラッシュ、ボット検知による中断等）でNAS上に残った可能性のある
        # yt-dlpの中間生成物（.part本体、.part-FragN.part、.ytdl）を今回の試行前に
        # 一掃する。ダウンロード完了済み（final_pathが存在しスキップする）場合でも、
        # 隣に残った断片ファイルはそのままだと永遠にクリーンアップされないため実行する。
        self._cleanup_stale_ytdlp_artifacts(final_path)

        if self._should_skip(final_path): return True

        return self._download_with_ytdlp(m3u8_url, final_path, task.url, target_dir)

    @staticmethod
    def _cleanup_stale_ytdlp_artifacts(final_path: Path) -> None:
        """final_pathと同名で始まる中間生成物（NAS上に残った古い`.part`/
        `.part-FragN.part`/`.ytdl`/旧版の`.fragments.tmp`ディレクトリ等）を削除する。

        以前の実装は結合(merge)処理をNAS上のfinal_pathへ直接outtmplさせていたため、
        処理が中断すると数百〜数千個のフラグメント断片がNAS上に残留し続けていた
        （実機で確認）。_download_with_ytdlpの修正により今後はこの種の残骸は
        発生しなくなるが、修正前に残った既存の残骸や、今回このメソッド自身が
        発見できなかった残骸を安全側で一掃する。
        """
        try:
            if not final_path.parent.exists():
                return
            for stale in final_path.parent.iterdir():
                if stale == final_path or not stale.name.startswith(final_path.name):
                    continue
                try:
                    if stale.is_dir():
                        shutil.rmtree(stale, ignore_errors=True)
                    else:
                        stale.unlink()
                except OSError as e:
                    logger.warning(f"⚠️ 残留中間ファイルの削除に失敗しました ({stale}): {e}")
        except OSError as e:
            logger.warning(f"⚠️ 残留中間ファイルのスキャンに失敗しました ({final_path.parent}): {e}")

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            self.session.headers['Referer'] = url
            res = self.session.get(url, timeout=CONFIG.REQUEST_TIMEOUT)

            if res.status_code in CONFIG.SCRAPING_BLOCK_STATUS_CODES:
                raise BotDetectionError(f"{url}: HTTP {res.status_code}（ボット検知/レート制限の可能性）")
            res.raise_for_status()

            if _looks_like_block_page(res.text):
                raise BotDetectionError(f"{url}: 応答内容がボット検知ページ（Cloudflare等）のパターンに一致")

            return res.text
        except BotDetectionError:
            raise
        except Exception as e:
            logger.error(f"HTML取得エラー: {e}", exc_info=True)
            if _is_bot_detection_error(e):
                raise BotDetectionError(f"{url}: {e}") from e
            return None

    def _extract_m3u8_url(self, html: str) -> Optional[str]:
        # missavのJS難読化(p,a,c,k,e,d)を解除してm3u8を抽出
        match = re.search(r"eval\(function\(p,a,c,k,e,d\).*?return p}\('(.*?)',\s*(\d+),\s*(\d+),\s*'([^']*)'\.split\('\|'\)", html)
        if not match: return None
        
        p = match.group(1).replace("\\'", "'")
        # D-L5: group(2)がpacker本来のradix('a')。以前はこれを無視してbase36固定
        # (chars 36種のmod 36)で単語を復元していたため、radixが36以外(典型的には
        # 62)のページでは誤った単語に置換され、m3u8抽出そのものに失敗しうった。
        radix = int(match.group(2))
        c = int(match.group(3))
        k = match.group(4).split('|')

        def e_func(num: int) -> str:
            return _packer_base_n_digits(num, radix)

        unpacked = p
        for i in range(c - 1, -1, -1):
            word = k[i] if i < len(k) and k[i] else e_func(i)
            # 正規表現の置換でバックスラッシュ等が誤動作しないようlambdaでエスケープ処理
            unpacked = re.sub(r'\b' + e_func(i) + r'\b', lambda m, w=word: w, unpacked)
            
        # 1080p -> 720p -> オリジナルの順で取得を試行
        for var_name in ['source1280', 'source842', 'source']:
            url_match = re.search(f"{var_name}=['\"]([^'\"]+)['\"]", unpacked)
            if url_match:
                return url_match.group(1)

        # 変数名が変更された場合のフォールバック
        fallback = re.search(r"['\"](https://[^'\"]+\.m3u8)['\"]", unpacked)
        if fallback:
            return fallback.group(1)
            
        return None

    def _fetch_m3u8_manifest(self, m3u8_url: str, page_url: str) -> Optional[str]:
        """m3u8マニフェスト本体を、ブラウザ偽装(impersonate)付きで直接取得する。

        missavのm3u8はsurrit.com等、CloudflareのボットチャレンジがかかったCDNで
        配信されていることが多い。yt-dlpのgenericエクストラクタにextractor_argsで
        impersonateを指定しても、それが効くのは最初のURL判定用リクエストのみで、
        その後内部的に発生する「m3u8情報のダウンロード」という再取得リクエストには
        impersonate設定が引き継がれない(yt-dlp側の制限。実機検証で403の再現を確認済み)。
        そのためマニフェスト自体はcurl_cffiで直接ブラウザ偽装して取得し、
        結果をローカルファイル経由でyt-dlpに渡す(_download_with_ytdlp参照)。
        """
        try:
            import curl_cffi.requests as curl_requests
        except ImportError:
            logger.error(
                "⚠️ curl_cffiが見つかりません。missavのCloudflare対策CDNからのm3u8取得には"
                "必須です。'pip install -r DDD/requirements.txt' でインストールしてください。"
            )
            return None

        try:
            res = curl_requests.get(
                m3u8_url,
                headers={'Referer': page_url},
                impersonate="chrome",
                timeout=CONFIG.REQUEST_TIMEOUT,
            )
            if res.status_code in CONFIG.SCRAPING_BLOCK_STATUS_CODES:
                raise BotDetectionError(f"{m3u8_url}: HTTP {res.status_code}（ボット検知/レート制限の可能性）")
            res.raise_for_status()
            return res.text
        except BotDetectionError:
            raise
        except Exception as e:
            logger.error(f"⚠️ m3u8マニフェスト取得エラー: {e}", exc_info=True)
            if _is_bot_detection_error(e):
                raise BotDetectionError(f"{m3u8_url}: {e}") from e
            return None

    @staticmethod
    def _localize_m3u8_manifest(manifest_text: str, base_url: str) -> str:
        """m3u8内の相対URI(セグメント/サブプレイリスト/鍵URI等)を絶対URLへ書き換える。

        マニフェストをローカルファイルとしてyt-dlpに渡すため、相対URIが
        (取得元のCDN URLではなく)ローカルファイルパス基準で誤って解決される
        のを防ぐ。
        """
        def _absolutize_uri_attr(match: "re.Match") -> str:
            return f'URI="{urljoin(base_url, match.group(1))}"'

        lines = []
        for line in manifest_text.splitlines():
            stripped = line.strip()
            if stripped.startswith('#'):
                lines.append(re.sub(r'URI="([^"]+)"', _absolutize_uri_attr, line))
            elif stripped:
                lines.append(urljoin(base_url, stripped))
            else:
                lines.append(line)
        return "\n".join(lines)

    _FRAGMENT_DOWNLOAD_WORKERS = 5

    def _fetch_segment_once(self, url: str, page_url: str) -> bytes:
        """1個のHLSセグメントをcurl_cffi(ブラウザ偽装)で1回だけ取得する。

        リトライは行わない(_download_segment側で行う)。
        """
        import curl_cffi.requests as curl_requests

        res = curl_requests.get(
            url,
            headers={'Referer': page_url},
            impersonate="chrome",
            timeout=CONFIG.REQUEST_TIMEOUT,
        )
        if res.status_code in CONFIG.SCRAPING_BLOCK_STATUS_CODES:
            raise BotDetectionError(f"{url}: HTTP {res.status_code}（ボット検知/レート制限の可能性）")
        res.raise_for_status()
        return res.content

    def _download_segment(self, url: str, page_url: str) -> bytes:
        """1個のHLSセグメントを、指数バックオフ付きリトライで取得する。

        #397: 以前はcurl_cffiを1回呼ぶだけで、数千セグメント中1つの一時的な
        タイムアウト(低速回線ではREQUEST_TIMEOUTのコメントどおり起こりうる)で
        例外→finallyのrmtreeで全フラグメント削除→連続失敗カウント加算、
        3回で実行中断、という形で数GBのダウンロードが丸ごと破棄されていた。
        SEGMENT_DOWNLOAD_MAX_ATTEMPTS 回まで、SEGMENT_RETRY_BASE_DELAY * 2^n 秒
        待って再試行する。BotDetectionError(403/429/503)はIP単位のブロックで
        あり再試行しても悪化させるだけなのでリトライせず即座に伝播させる。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, CONFIG.SEGMENT_DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                return self._fetch_segment_once(url, page_url)
            except BotDetectionError:
                raise
            except Exception as e:
                last_exc = e
                if attempt >= CONFIG.SEGMENT_DOWNLOAD_MAX_ATTEMPTS:
                    break
                delay = CONFIG.SEGMENT_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"⚠️ セグメント取得に失敗しました ({attempt}/{CONFIG.SEGMENT_DOWNLOAD_MAX_ATTEMPTS}): "
                    f"{e}。{delay:.1f}秒後に再試行します: {url}"
                )
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    def _download_segments_and_localize_manifest(
        self, localized_manifest: str, page_url: str, tmp_dir: Path
    ) -> str:
        """絶対URL化済みのm3u8マニフェストの各セグメント(および#EXT-X-KEYのURI等)を
        curl_cffi(ブラウザ偽装)で自前ダウンロードし、ローカルファイル名に
        差し替えたマニフェストを返す。

        yt-dlp自身にセグメント取得をさせると、yt-dlpの"requests"ネットワーク
        ハンドラが独自のSSLContextを使うためTLS指紋(JA3)がブラウザ/素のrequests
        とは異なり、User-Agent等のヘッダーを完全に一致させてもWAFに403で
        ブロックされ続けることを実機の生トラフィック検証(debug_printtraffic)で
        確認した。一方、curl_cffiでのブラウザ偽装リクエストはページ本体・
        m3u8マニフェスト・個別セグメントいずれも問題なく通ることを確認済み。
        そのためセグメント取得自体もcurl_cffi経由で行い、yt-dlp/ffmpegには
        取得済みのローカルファイルのみを渡す(ネットワークアクセスさせない)。
        """
        lines = localized_manifest.splitlines()
        targets: Dict[int, str] = {}
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                m = re.search(r'URI="([^"]+)"', stripped)
                if m:
                    targets[i] = m.group(1)
            else:
                targets[i] = stripped

        def _fetch_one(idx: int, url: str) -> Tuple[int, str]:
            suffix = Path(url.split('?')[0]).suffix or '.bin'
            local_name = f"seg_{idx:06d}{suffix}"
            local_path = tmp_dir / local_name
            content = self._download_segment(url, page_url)
            local_path.write_bytes(content)
            # 相対ファイル名のままだと、yt-dlp側でfile://の基準URLに対する
            # 相対URI解決が(特にWindowsのドライブレター付きfile://パスで)
            # うまくいかず"url must be a string"のエラーになることを実機で
            # 確認したため、各セグメントの絶対file:// URIを書き込む。
            return idx, local_path.resolve().as_uri()

        resolved: Dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=self._FRAGMENT_DOWNLOAD_WORKERS) as executor:
            futures = {executor.submit(_fetch_one, idx, url): idx for idx, url in targets.items()}
            try:
                for future in as_completed(futures):
                    idx, local_uri = future.result()  # 例外はそのまま呼び出し元へ伝播させる
                    resolved[idx] = local_uri
            except Exception:
                # ボット検知(403/429/503)等で一部セグメントが例外を出した場合、
                # 「即時セッション中断」を実際に機能させるため、まだ実行が
                # 始まっていない残りのセグメント取得をキャンセルする。
                # `with`ブロックの終了時に暗黙で呼ばれる shutdown(wait=True) には
                # cancel_futuresを指定できず、数百〜数千件のキュー済みセグメントが
                # ブロック中のCDNへのHTTP GETを完走し終えるまで例外の伝播が
                # 遅延してしまっていた(実行中の最大_FRAGMENT_DOWNLOAD_WORKERS件は
                # 完了を待つが、キュー済みの残りはリクエスト自体を送らずに済む)。
                executor.shutdown(wait=True, cancel_futures=True)
                raise

        new_lines = list(lines)
        for idx, _url in targets.items():
            local_uri = resolved[idx]
            if lines[idx].strip().startswith('#'):
                new_lines[idx] = re.sub(r'URI="[^"]+"', f'URI="{local_uri}"', lines[idx])
            else:
                new_lines[idx] = local_uri
        return "\n".join(new_lines)

    @staticmethod
    def _prepare_fragment_tmp_dir(tmp_dir: Path) -> bool:
        """セグメント取得用の一時ディレクトリを準備する（品質: _download_with_ytdlpから分離）。

        前回実行がクラッシュ等で中断した場合、同名のtmp_dirが未クリーンアップの
        まま残っている可能性がある。古いフラグメント/結合途中ファイルが今回の
        試行に混入しないよう、開始前に必ず削除してから作り直す。動画1本あたり
        数GBのフラグメントを書き込むため、事前に空き容量も確認する。ここを
        怠ると、ローカルディスクを圧迫してシステム全体（他プロセスやSSH
        セッション等）に影響しかねない。

        Args:
            tmp_dir (Path): 準備対象の一時ディレクトリ。

        Returns:
            bool: 準備に成功した場合True。ディレクトリ作成失敗・空き容量
                不足の場合はFalse（いずれもエラーログ出力済み）。
        """
        shutil.rmtree(tmp_dir, ignore_errors=True)
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"⚠️ 一時フラグメント用ディレクトリの作成に失敗しました: {e}", exc_info=True)
            return False

        if not FileSystemManager.check_disk_space(tmp_dir, min_free_gb=CONFIG.LOCAL_TMP_MIN_FREE_SPACE_GB):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False

        return True

    def _merge_fragments_and_transfer_to_nas(
        self,
        localized_manifest: str,
        page_url: str,
        tmp_dir: Path,
        local_merged_path: Path,
        nas_tmp_path: Path,
        final_path: Path,
    ) -> bool:
        """セグメントを取得してyt-dlpで結合し、完成ファイルをNASへ転送する
        （品質: _download_with_ytdlpから分離）。

        セグメント取得・yt-dlpによる結合(merge)はローカルディスク上で完結させ、
        完成した1ファイルのみを最後にNASへ移す(理由は呼び出し元のコメント参照)。
        呼び出し元の_download_with_ytdlpがtry/exceptでBotDetectionError・
        その他の例外を捕捉するため、本メソッドは例外をそのまま送出する。

        Args:
            localized_manifest (str): ローカル化済みm3u8マニフェスト文字列。
            page_url (str): 元動画ページのURL。
            tmp_dir (Path): セグメント取得用の一時ディレクトリ。
            local_merged_path (Path): 結合後ファイルのローカル一時パス。
            nas_tmp_path (Path): NAS上での転送用一時パス。
            final_path (Path): 最終的な保存先パス（NAS上）。

        Returns:
            bool: 結合・転送に成功した場合True。ディスク空き容量不足の
                場合のみFalse（エラーログ出力済み）。それ以外の失敗は
                例外として送出される。
        """
        logger.info(f"📥 セグメント取得開始 (curl_cffi): {final_path.name}")
        local_manifest = self._download_segments_and_localize_manifest(
            localized_manifest, page_url, tmp_dir
        )

        tmp_manifest_path = tmp_dir / "playlist.m3u8"
        tmp_manifest_path.write_text(local_manifest, encoding="utf-8")

        # セグメント取得完了時点の実サイズをもとに、この先の結合
        # (yt-dlpによるフラグメント連結)と、その後のFixupM3u8
        # (タイムスタンプ補正のためのffmpeg再多重化。別ファイルへの
        # 書き出しを伴う)でさらに同程度のディスク使用が発生することを
        # 見込み、重い処理を始める前にもう一度空き容量を確認する。
        # これを怠ると、数十分かけてセグメントを取得した後、結合〜後処理の
        # 終盤でディスクフルにより"Conversion failed!"のような要領を
        # 得ないエラーで失敗し、それまでの時間と帯域が丸ごと無駄になる
        # (実機で確認)。
        downloaded_bytes = sum(f.stat().st_size for f in tmp_dir.iterdir() if f.is_file())
        _, _, free_bytes = shutil.disk_usage(tmp_dir)
        # 結合済みファイル本体 + FixupM3u8が新たに書き出す修正版ファイルの
        # 分として、取得済みセグメント合計の約2.2倍の空きを要求する。
        required_bytes = int(downloaded_bytes * 2.2)
        if free_bytes < required_bytes:
            logger.error(
                f"⚠️ ローカルディスクの空き容量不足のため結合処理を中断します "
                f"(取得済み: 約{downloaded_bytes // (2**30)}GB, "
                f"必要目安: 約{required_bytes // (2**30)}GB, "
                f"空き: {free_bytes // (2**30)}GB)。"
                f"{CONFIG.LOCAL_TMP_DIR} の空き容量を増やしてから再実行してください。"
            )
            return False

        ydl_opts = {
            'format': 'best',
            'outtmpl': str(local_merged_path),
            'quiet': not CONFIG.SHOW_PROGRESS_BAR,
            'no_warnings': True,
            # セグメントは既にローカルへ取得済みのため、yt-dlpにはローカル
            # ファイルとして渡す(file://URL)。結合処理のみyt-dlp/ffmpegに
            # 任せ、ネットワークアクセスは一切発生させない。
            'enable_file_urls': True,
        }
        logger.info(f"📥 結合開始 (yt-dlp, ローカルディスク上): {final_path.name}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([tmp_manifest_path.resolve().as_uri()])

        # 完成した1ファイルのみをNASへ書き出す。同一ファイルシステム間の
        # os.replace()は原子的だが、ローカルディスク→NASという異なる
        # ファイルシステム間の移動は原子的にできない。そこで
        # 「save_dir内の一時名へコピー→save_dir内でos.replace()」の2段階に
        # することで、コピー中に中断してもfinal_path自体は書き換わらず、
        # _should_skip()が中途半端なファイルを完成済みと誤認しないようにする。
        logger.info(f"📤 NASへ転送中: {final_path.name}")
        shutil.copy2(str(local_merged_path), str(nas_tmp_path))

        # NAS(CIFS)は接続が不安定な場合があり、実機のdmesgでも
        # "sends on sock ... stuck for 15 seconds"や"No writable handle
        # in writepages"(バッファ済み書き込みをサーバーへ反映できな
        # かったことを示す)が確認されている。この場合shutil.copy2自体は
        # 例外を送出せず「見かけ上成功」してしまうことがあり、末尾の
        # moov atomが丸ごと欠落した再生不能なmp4が生成される実害を確認
        # した。コピー元とコピー先のファイルサイズを比較し、転送が
        # 不完全だった場合は成功扱いにしない。
        local_size = local_merged_path.stat().st_size
        nas_size = nas_tmp_path.stat().st_size
        if nas_size != local_size:
            nas_tmp_path.unlink(missing_ok=True)
            raise OSError(
                f"NASへの転送後にファイルサイズが一致しませんでした "
                f"(ローカル: {local_size} bytes, NAS: {nas_size} bytes)。"
                "NASの接続不安定による転送不良の可能性があります。"
            )

        nas_tmp_path.replace(final_path)
        return True

    def _download_with_ytdlp(self, m3u8_url: str, final_path: Path, page_url: str, save_dir: Path) -> bool:
        # HLS(m3u8)は「マニフェスト・全セグメントをcurl_cffi(ブラウザ偽装)で
        # 自前取得してローカルに保存 → yt-dlpにはローカルファイルのみを渡して
        # 結合させる」方式で処理する(理由は_download_segments_and_localize_manifest
        # のdocstring参照)。
        manifest_text = self._fetch_m3u8_manifest(m3u8_url, page_url)
        if manifest_text is None:
            return False

        localized_manifest = self._localize_m3u8_manifest(manifest_text, m3u8_url)

        # フラグメントはNAS上のsave_dirではなくローカルディスクに一時保存する
        # (理由はCONFIG.LOCAL_TMP_DIRのコメント参照)。結合済みの最終ファイルの
        # みNAS上のfinal_pathへ書き出す。
        tmp_dir = CONFIG.LOCAL_TMP_DIR / (final_path.name + ".fragments.tmp")
        if not self._prepare_fragment_tmp_dir(tmp_dir):
            return False

        # yt-dlpによる結合(merge)先もローカルディスクにする。以前はここに
        # final_path(NAS上)を直接指定していたが、HLSのhlsnativeダウンローダーは
        # ローカルfile://の入力であっても出力先(outtmpl)に対して
        # 「フラグメント毎に`<name>.part-FragN.part`を書き込んでから結合」という
        # 動作をするため、結局NAS上に数百〜数千個の小ファイルを書き込むことになり、
        # セグメント取得側で修正したのと全く同じNASマウント遅延由来の問題
        # (書き込み直後の読み込みでのfragment not found、長時間のハング)を
        # 結合段階で再発させてしまっていた（実機のNAS上に大量の
        # `*.part-FragN.part`/`*.ytdl`が残留する形で確認）。結合はローカル
        # ディスク上で完結させ、完成した1ファイルのみを最後にNASへ移す。
        local_merged_path = tmp_dir / final_path.name
        nas_tmp_path = final_path.with_name(final_path.name + ".nastmp")

        try:
            if not self._merge_fragments_and_transfer_to_nas(
                localized_manifest, page_url, tmp_dir, local_merged_path, nas_tmp_path, final_path
            ):
                return False

            DiscordNotifier.send(f"✅ 動画保存完了 (missav)\nファイル: `{final_path.name}`\n場所: `{save_dir.name}`")
            return True
        except BotDetectionError:
            if final_path.exists(): final_path.unlink()
            nas_tmp_path.unlink(missing_ok=True)
            raise
        except Exception as e:
            logger.error(f"⚠️ M3U8 DL エラー: {e}", exc_info=True)
            if final_path.exists(): final_path.unlink() # 失敗した一時ファイルの削除
            nas_tmp_path.unlink(missing_ok=True)
            if _is_bot_detection_error(e):
                raise BotDetectionError(f"{page_url}: {e}") from e
            return False
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

# ==========================================
# 4. メインコントローラー
# ==========================================
class BatchDownloader:
    def __init__(self):
        self.session = NetworkManager.create_session()
        self._shutdown_requested = False
        # D-L3: シグナルを_shutdown_requestedへフラグ化するだけでは、進行中の
        # タスク(yt-dlpによる数GB規模のダウンロード等)はメインループの次回
        # チェック(タスク境界)まで止まらない。2回目以降のシグナルでは即座に
        # KeyboardInterruptを送出し、実行中の処理を強制的に中断できるようにする。
        self._interrupt_count = 0
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        self.history = HistoryManager.load_history()

    def _signal_handler(self, signum: int, frame: Any) -> None:
        self._interrupt_count += 1
        if self._interrupt_count == 1:
            logger.info("🛑 停止シグナル検知（現在のタスク完了後に終了します。強制終了するには再度シグナルを送ってください）")
            self._shutdown_requested = True
            return
        # D-L3: 1回目のシグナル後もタスクが終わらない(数GB規模のダウンロード中
        # 等)場合、2回目のシグナルで即座に強制中断する。ロック解放は
        # run()のtry/finallyが担保する。
        logger.critical("🛑🛑 2回目の停止シグナルを検知したため、実行中の処理を強制中断します")
        raise KeyboardInterrupt("second interrupt signal received; forcing immediate shutdown")

    def _get_strategy(self, url: str) -> Optional[DownloadStrategy]:
        # 【修正】ハードコードではなく、設定フラグで制御するように変更
        if "youtube.com" in url or "youtu.be" in url:
            if not CONFIG.ENABLE_YOUTUBE_DL:
                logger.info(f"🚫 YouTube機能は設定により無効化されています: {url}")
                return None
            # 有効な場合は通常のフローへ進む

        # missavなら専用ストラテジー、それ以外はUniversal
        if "missav" in url:
            return ScrapingStrategy(CONFIG.BASE_SAVE_DIR, self.session)
        else:
            # YouTube以外の汎用サイト（Twitter/X, Vimeoなど）は引き続きダウンロード可能
            return UniversalYtDlpStrategy(CONFIG.BASE_SAVE_DIR, self.session)

    def _collect_tasks(self) -> List[DownloadTask]:
        # ソースファイルごとにグループ化して集める。MAX_TASKS_PER_RUNで先頭から
        # 打ち切られる際に、収集順が先のリストファイルだけが毎回上限を使い切り、
        # 他のリストファイルが慢性的に後回しにされる（飢餓状態になる）のを防ぐため、
        # 最後にラウンドロビンで平坦化する。
        tasks_by_source: Dict[str, List[DownloadTask]] = {}
        seen_urls: Set[str] = set()

        def _add(url: str, source_name: str) -> None:
            if url in seen_urls:
                return
            seen_urls.add(url)
            tasks_by_source.setdefault(source_name, []).append(DownloadTask(url, source_name))

        if CONFIG.LIST_FILE_PATH.exists():
            # #184: list/*.txt側は非UTF-8バイト等の読み込み失敗をtry/exceptで保護し
            # エラーログを出したうえで処理を継続するが、list.txt側にはこの保護が
            # 無かった。list.txtの読み込みで例外が発生すると_collect_tasks全体が
            # 未処理例外で中断し、後続で処理されるはずのlist/*.txtのタスクまで
            # 巻き添えで処理されなくなっていた。list/*.txt側と同じパターンで保護する。
            try:
                with open(CONFIG.LIST_FILE_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        url = line.strip()
                        if url and not url.startswith("#"):
                            url = _normalize_url(url)
                            if url not in self.history:
                                _add(url, "list")
            except Exception as e:
                logger.error(f"リスト読み込みエラー ({CONFIG.LIST_FILE_PATH.name}): {e}", exc_info=True)

        if CONFIG.LIST_DIR_PATH.exists():
            # glob()の順序はOS/ファイルシステム依存で不定なため、実行毎に順序が
            # ぶれないようソートしておく（ラウンドロビンの公平性とは別に、挙動の
            # 再現性・デバッグしやすさのため）。
            for list_file in sorted(CONFIG.LIST_DIR_PATH.glob("*.txt")):
                source_name = list_file.stem
                try:
                    with open(list_file, "r", encoding="utf-8") as f:
                        for line in f:
                            url = line.strip()
                            if url and not url.startswith("#"):
                                url = _normalize_url(url)
                                if url not in self.history:
                                    _add(url, source_name)
                except Exception as e:
                    logger.error(f"リスト読み込みエラー ({list_file.name}): {e}", exc_info=True)

        return _round_robin_flatten(tasks_by_source.values())

    def _purge_skipped_tasks(self, skipped_tasks: List[DownloadTask]) -> None:
        """
        スキップ対象となったタスクを元リストから物理削除し、アーカイブへ退避する。
        
        Args:
            skipped_tasks (List[DownloadTask]): パージ対象のタスクリスト
        """
        if not skipped_tasks:
            return

        # 1. タスクをソース(ファイル名)ごとにグループ化
        tasks_by_source = defaultdict(set)
        for task in skipped_tasks:
            tasks_by_source[task.source_name].add(task.url)

        deleted_count = 0
        archive_path = CONFIG.BASE_SAVE_DIR / "archived_tasks.txt"

        # 2. アーカイブへの追記（SSOTからパージされた証跡を残す）
        try:
            with open(archive_path, "a", encoding="utf-8") as af:
                af.write(f"\n# Archived on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                for task in skipped_tasks:
                    af.write(f"{task.url}\n")
        except Exception as e:
            logger.error(f"⚠️ アーカイブファイルへの書き込みに失敗しました: {e}", exc_info=True)
            return # アーカイブ失敗時は元ファイルの削除も中断（データロスト防止）

        # 3. 元ファイルからの物理削除（インメモリでフィルタリングして上書き）
        for source_name, urls_to_remove in tasks_by_source.items():
            if source_name == "list":
                file_path = CONFIG.LIST_FILE_PATH
            else:
                file_path = CONFIG.LIST_DIR_PATH / f"{source_name}.txt"

            if not file_path.exists():
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                # パージ対象外の行だけを残す
                retained_lines = []
                for line in lines:
                    stripped_line = line.strip()
                    url = _normalize_url(stripped_line) if stripped_line else stripped_line
                    if url in urls_to_remove:
                        deleted_count += 1
                        logger.debug(f"🗑️ パージ実行: {url} (from {source_name})")
                    else:
                        retained_lines.append(line)

                # アトミックな上書き更新
                temp_path = file_path.with_suffix('.tmp')
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.writelines(retained_lines)
                temp_path.replace(file_path)

            except Exception as e:
                logger.error(f"⚠️ リストファイル({file_path.name})のパージ処理に失敗しました: {e}", exc_info=True)

        logger.info(f"🧹 期限切れ（無効）のタスク {deleted_count} 件をパージしました。")

    def _sleep_between_tasks(self, url: str) -> None:
        """次のタスクまで待機する。

        固定間隔だと機械的なアクセスパターンとして検知されやすいため、ランダムな
        ジッターを持たせる。YouTube/missavはボット検知が特に厳しいため、より
        保守的な（長め・幅広の）間隔を使う。
        """
        if "youtube.com" in url or "youtu.be" in url:
            low, high = CONFIG.YOUTUBE_SLEEP_RANGE
        elif "missav" in url:
            low, high = CONFIG.MISSAV_SLEEP_RANGE
        else:
            low, high = CONFIG.DEFAULT_SLEEP_RANGE
        delay = random.uniform(low, high)
        logger.debug(f"💤 次のタスクまで {delay:.1f} 秒待機します")
        time.sleep(delay)

    def run(self) -> None:
        # 【追加】多重起動防止ロック: cron等での実行が重複した場合に
        # list.txt / list/*.txt の読み書きが競合しないようにする
        lock_fd = os.open(str(CONFIG.LOCK_FILE_PATH), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            # D-L4: 他インスタンス実行中によるスキップは異常系ではなく想定内の
            # 正常系(newface_monitor.run_monitorと同じ扱い)であり、sys.exit(1)
            # で終了するとrun_task.sh側がERRORとして記録してしまっていた。
            # 正常終了(終了コード0)として扱うようreturnに変更する。
            logger.info("⏭️ 他のインスタンスが既に実行中のため終了します (lock busy)")
            os.close(lock_fd)
            return

        try:
            self._run_locked()
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def _preflight_checks(self) -> bool:
        """ロック取得後の前提条件チェック（品質: _run_lockedから分離）。

        Returns:
            bool: 処理を継続してよい場合True。クールダウン中・時間外・NAS
                未マウントのいずれかで中断すべき場合はFalse（ログ出力済み）。
        """
        # #398: 前回実行のクラッシュ等で残留した*.fragments.tmpを、他プロセスとの
        # 競合が無いことが保証されたロック取得後の最初に一掃する(SDカード上の
        # ローカルディスクを圧迫し続け、LOCAL_TMP_MIN_FREE_SPACE_GBチェックで
        # 後続の全ダウンロードが失敗する事象への対策)。
        FileSystemManager.sweep_stale_fragment_dirs()

        SystemHealthChecker.check_dependencies()

        cooldown_until = CooldownManager.is_in_cooldown()
        if cooldown_until is not None:
            logger.info(
                f"🧊 ボット検知クールダウン中のため今回の実行をスキップします "
                f"（解除予定: {cooldown_until.strftime('%Y-%m-%d %H:%M:%S')}）"
            )
            return False

        if not SystemHealthChecker.is_within_time_window():
            if FORCE_MODE:
                logger.debug("⚠️ FORCEモード: 時間制限無視")
            else:
                logger.info(f"🕒 指定時間外（{CONFIG.START_HOUR}:00 - {CONFIG.END_HOUR}:00）のため終了（--forceで無視可能）")
                return False

        if not SystemHealthChecker.verify_nas_mount():
            return False

        return True

    def _prepare_tasks(self) -> List[DownloadTask]:
        """今回実行分のタスクリストを収集・フィルタ・上限適用する（品質: _run_lockedから分離）。

        Returns:
            List[DownloadTask]: 今回実行対象のタスクリスト（実行対象がない場合は空リスト）。
        """
        tasks = self._collect_tasks()
        if not tasks:
            logger.info("処理対象のURLがありません。")
            return []

        # YouTube無効時はタスクを除外し、パージ処理へ回す
        skipped_tasks = []
        if not CONFIG.ENABLE_YOUTUBE_DL:
            valid_tasks = []
            for t in tasks:
                if "youtube.com" in t.url or "youtu.be" in t.url:
                    skipped_tasks.append(t)
                else:
                    valid_tasks.append(t)

            if skipped_tasks:
                logger.info(f"🚫 YouTube機能が無効なため、{len(skipped_tasks)} 件のタスクをスキップおよびパージします。")
                self._purge_skipped_tasks(skipped_tasks)

            tasks = valid_tasks

        # パージ後、タスクが0になった場合は終了
        if not tasks:
            logger.info("パージ処理の結果、実行可能なタスクがなくなりました。")
            return []

        # 1回の実行あたりのタスク数を制限する。ジッター付きの間隔を空けていても、
        # 「1回の起動で数百件を一気に処理する」こと自体が異常なアクセス量になり得るため、
        # 残りは次回の実行（履歴・リストとも未消費のまま）に自然と持ち越される。
        total_pending = len(tasks)
        if CONFIG.MAX_TASKS_PER_RUN > 0 and total_pending > CONFIG.MAX_TASKS_PER_RUN:
            tasks = tasks[:CONFIG.MAX_TASKS_PER_RUN]
            logger.info(
                f"📉 1回あたりの上限（{CONFIG.MAX_TASKS_PER_RUN}件）に合わせて "
                f"{total_pending}件中{len(tasks)}件のみ処理します（残りは次回以降に持ち越し）。"
            )

        return tasks

    def _process_tasks(self, tasks: List[DownloadTask]) -> None:
        """収集済みタスクを順次ダウンロード実行するメインループ（品質: _run_lockedから分離）。

        Args:
            tasks (List[DownloadTask]): 今回実行対象のタスクリスト。
        """
        consecutive_failures = 0
        for i, task in enumerate(tasks):
            if self._shutdown_requested: break
            if not SystemHealthChecker.is_within_time_window() and not FORCE_MODE:
                logger.info("⏰ 終了時刻により中断")
                break

            logger.info(f"\n[{i+1}/{len(tasks)}] 開始: {task.url}")

            try:
                strategy = self._get_strategy(task.url)

                # 【追加】YouTube等のスキップ対象（None）だった場合は次へ
                if strategy is None:
                    continue

                if strategy.download(task):
                    HistoryManager.add_history(task.url)
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            except BotDetectionError as e:
                # 429やSign-in要求はIP/アカウント単位の制限である可能性が高く、
                # 残りのタスクを続けても状況を悪化させるだけなので即座に中断する。
                logger.critical(f"🚨 ボット検知/レート制限の兆候を検知しました: {e}", exc_info=True)
                CooldownManager.trigger_cooldown()
                DiscordNotifier.send(
                    f"🚨 CRITICAL: ボット検知/レート制限の兆候（429・Sign-in要求等）を検知したため、"
                    f"残りのタスクを中断し、{CONFIG.BOT_DETECTION_COOLDOWN_HOURS:.0f}時間の"
                    f"クールダウンに入ります。\n詳細: {e}",
                    is_error=True
                )
                break
            except Exception as e:
                logger.error(f"エラー: {e}", exc_info=True)
                consecutive_failures += 1

            if consecutive_failures >= CONFIG.CONSECUTIVE_FAILURE_THRESHOLD:
                logger.error(f"⚠️ 連続{consecutive_failures}回失敗したため、レート制限の可能性を考慮し処理を中断します。")
                DiscordNotifier.send(
                    f"⚠️ 連続{consecutive_failures}回のダウンロード失敗を検知したため、以降のタスクをスキップします。",
                    is_error=True
                )
                break

            if i < len(tasks) - 1 and not self._shutdown_requested:
                self._sleep_between_tasks(task.url)

        logger.info("🎉 全処理終了")

    def _run_locked(self) -> None:
        if not self._preflight_checks():
            return

        tasks = self._prepare_tasks()
        if not tasks:
            return

        logger.info("="*60)
        logger.info("   🚀 Smart Pipeline Downloader (v2.4.0)")
        logger.info(f"   Schedule: {CONFIG.START_HOUR}:00 - {CONFIG.END_HOUR}:00")
        logger.info(f"   Tasks: {len(tasks)}")
        logger.info("="*60)

        self._process_tasks(tasks)

if __name__ == "__main__":
    if CLEAR_COOLDOWN_MODE:
        # 手動運用向け: ボット検知を誤検知だと判断した場合や、原因を解消済みの場合に
        # クールダウン期限を待たずに手動で解除するためのエントリーポイント。
        CooldownManager.clear()
        logger.info("🧊 クールダウンを解除しました。")
        sys.exit(0)

    BatchDownloader().run()