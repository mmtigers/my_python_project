#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Production Grade Batch Downloader (v1.2.0 Notification Update)
-------------------------------------------------
Features:
- Atomic File Writes (Prevents corrupted partial files)
- Modern Pathlib Implementation
- Strict Type Hinting & Docstrings
- Robust Error Handling & Logging
- Strategy Pattern for Scalability
- Automatic Deduplication of URL List
- Dependency Checks (ffmpeg, yt-dlp version)
- Smart Log Handling (Clean logs for Cron jobs)
- Force Run Mode (--force argument support)
- Simplified Discord Notifications (No URLs)
"""

import os
import sys
import time
import re
import shutil
import datetime
import logging
import signal
import requests
import subprocess
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Any, Set
from dataclasses import dataclass
from pathlib import Path

# External Libraries
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm
import yt_dlp

# ==========================================
# 0. 環境設定 & ロギング初期化
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("Downloader")

# 簡易的な引数チェック（--force があれば時間制限などを無視）
FORCE_MODE = "--force" in sys.argv

# プロジェクトルートの解決
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR

# 'services' ディレクトリが見つかるまで親を探索 (最大3階層)
found_root = False
for _ in range(3):
    if (PROJECT_ROOT / "services").exists():
        found_root = True
        break
    PROJECT_ROOT = PROJECT_ROOT.parent

if not found_root:
    PROJECT_ROOT = Path("/home/masahiro/develop/MY_HOME_SYSTEM")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# 通知サービスのインポート
try:
    from services.notification_service import _send_discord_webhook
except ImportError:
    logger.warning("⚠️ Notification Service not found. Discord notification disabled.")
    def _send_discord_webhook(messages, image_data=None, channel="notify"):
        pass

# ==========================================
# 1. コンフィグレーション
# ==========================================
@dataclass(frozen=True)
class AppConfig:
    """アプリケーション設定 (Immutable)"""
    # --force オプションがある場合は時間制限を無効化
    RESTRICT_TIME: bool = not FORCE_MODE
    START_HOUR: int = 0
    END_HOUR: int = 5
    INTERVAL_SECONDS: int = 3600
    MIN_FREE_SPACE_GB: int = 50
    
    # パス関係
    BASE_SAVE_DIR: Path = Path(os.getenv("VIDEO_SAVE_DIR", "/mnt/nas/ddd"))
    LIST_FILE_PATH: Path = CURRENT_DIR / "list.txt"
    NAS_MOUNT_POINT: Path = Path("/mnt/nas")
    NAS_MARKER_FILE: str = ".mounted"
    
    # ネットワーク設定
    REQUEST_TIMEOUT: int = 20
    MAX_RETRIES: int = 3
    
    # UI設定
    # ターミナル実行時のみプログレスバーを表示（ログファイル汚染防止）
    SHOW_PROGRESS_BAR: bool = sys.stdout.isatty()

    @property
    def nas_marker_path(self) -> Path:
        return self.NAS_MOUNT_POINT / self.NAS_MARKER_FILE

CONFIG = AppConfig()

# ==========================================
# 2. マネージャー & ユーティリティ
# ==========================================
class DiscordNotifier:
    """通知管理"""
    @staticmethod
    def send(text: str, is_error: bool = False) -> None:
        channel = 'error' if is_error else 'notify'
        message = {"type": "text", "text": text}
        try:
            _send_discord_webhook([message], channel=channel)
            logger.info("🔔 Discord通知送信完了")
        except Exception as e:
            logger.error(f"⚠️ Discord通知エラー: {e}")

class NetworkManager:
    """ネットワークセッション管理"""
    @staticmethod
    def create_session() -> requests.Session:
        session = requests.Session()
        retries = Retry(
            total=CONFIG.MAX_RETRIES,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
        })
        return session

class FileSystemManager:
    """ファイルシステム操作管理"""

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """OSで使用できない文字を置換"""
        return re.sub(r'[\\/*?:"<>|]', '_', filename)

    @staticmethod
    def ensure_dir(path: Path) -> bool:
        """ディレクトリ作成（権限チェック付き）"""
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except PermissionError:
            msg = f"❌ 権限エラー: {path} に書き込めません。"
            logger.error(msg)
            DiscordNotifier.send(msg, is_error=True)
            return False

    @staticmethod
    def check_disk_space(path: Path) -> bool:
        """ディスク容量チェック"""
        try:
            check_path = path
            while not check_path.exists():
                check_path = check_path.parent
                if check_path == check_path.parent:
                    break

            total, used, free = shutil.disk_usage(check_path)
            free_gb = free // (2**30)

            if free_gb < CONFIG.MIN_FREE_SPACE_GB:
                msg = (f"⚠️ **DISK FULL**: 空き容量が {free_gb}GB です。\n"
                       f"NVR録画領域保護のため中断します。")
                logger.warning(msg)
                DiscordNotifier.send(msg, is_error=True)
                return False
            return True
        except Exception as e:
            logger.error(f"⚠️ Disk check error: {e}")
            return True

# ==========================================
# 3. システム健全性チェック
# ==========================================
class SystemHealthChecker:
    @staticmethod
    def is_within_time_window() -> bool:
        if not CONFIG.RESTRICT_TIME:
            return True
        current_hour = datetime.datetime.now().hour
        return CONFIG.START_HOUR <= current_hour < CONFIG.END_HOUR

    @staticmethod
    def verify_nas_mount() -> bool:
        if not CONFIG.NAS_MOUNT_POINT.exists():
            msg = f"⛔ **CRITICAL**: `{CONFIG.NAS_MOUNT_POINT}` が見つかりません。"
            logger.critical(msg)
            DiscordNotifier.send(msg, is_error=True)
            return False

        if not CONFIG.nas_marker_path.exists():
            msg = (f"⛔ **CRITICAL**: NASマウントチェック失敗！\n"
                   f"`{CONFIG.NAS_MARKER_FILE}` が見つかりません。\n"
                   f"SDカード保護のため停止します。")
            logger.critical(msg)
            DiscordNotifier.send(msg, is_error=True)
            return False
        return True
    
    @staticmethod
    def check_dependencies() -> None:
        """外部依存ツールのチェック"""
        try:
            import yt_dlp.version
            logger.info(f"ℹ️ yt-dlp version: {yt_dlp.version.__version__}")
        except ImportError:
            pass

        if shutil.which("ffmpeg") is None:
            msg = "⚠️ **WARNING**: `ffmpeg` がインストールされていません。\n高画質動画の結合に失敗する可能性があります。"
            logger.warning(msg)
            DiscordNotifier.send(msg, is_error=True)

# ==========================================
# 4. ダウンロード戦略 (Strategy Pattern)
# ==========================================
class DownloadStrategy(ABC):
    
    def __init__(self, save_base_dir: Path, session: requests.Session):
        self.save_base_dir = save_base_dir
        self.session = session

    @abstractmethod
    def download(self, url: str) -> bool:
        pass

    def _prepare_directory(self, sub_dir: str = "") -> Optional[Path]:
        target_dir = self.save_base_dir / sub_dir if sub_dir else self.save_base_dir
        
        if not FileSystemManager.ensure_dir(target_dir):
            return None
        if not FileSystemManager.check_disk_space(target_dir):
            return None
        return target_dir

    def _should_skip(self, filepath: Path) -> bool:
        if filepath.exists() and filepath.stat().st_size > 0:
            logger.info(f"⏭️ 既に存在するためスキップ: {filepath.name}")
            return True
        return False

class YoutubeStrategy(DownloadStrategy):
    """yt-dlpを使用したYouTubeダウンロード"""
    
    def download(self, url: str) -> bool:
        logger.info("🎥 YouTube動画として処理します...")
        target_dir = self._prepare_directory("youtube")
        if not target_dir: return False

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': f'{str(target_dir)}/%(title)s.%(ext)s',
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'nopart': False,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                filename_str = ydl.prepare_filename(info)
                
                base_path = Path(filename_str)
                final_path = base_path.with_suffix('.mp4')

                if self._should_skip(base_path) or self._should_skip(final_path):
                    return True

                logger.info(f"📥 ダウンロード開始: {info.get('title')}")
                ydl.download([url])
                logger.info("✨ 完了")
                
                # 通知内容からURLを削除
                DiscordNotifier.send(f"✅ **YouTube保存完了**\nファイル: `{final_path.name}`")
                return True
        except Exception as e:
            logger.error(f"⚠️ YouTubeエラー: {e}")
            return False

class GenericStrategy(DownloadStrategy):
    """汎用スクレイピング (Tktube等)"""
    
    def __init__(self, save_base_dir: Path, session: requests.Session, sub_dir: str = ""):
        super().__init__(save_base_dir, session)
        self.sub_dir = sub_dir

    def download(self, url: str) -> bool:
        target_dir = self._prepare_directory(self.sub_dir)
        if not target_dir: return False

        html = self._fetch_html(url)
        if not html: return False

        candidates = self._extract_video_urls(html)
        if not candidates:
            logger.warning("⚠️ 動画リンクが見つかりませんでした。")
            return False

        filename = self._generate_filename(url)
        final_path = target_dir / filename

        if self._should_skip(final_path):
            return True

        return self._execute_atomic_download(candidates, final_path, url, target_dir)

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            self.session.headers['Referer'] = url
            res = self.session.get(url, timeout=CONFIG.REQUEST_TIMEOUT)
            res.raise_for_status()
            return res.text
        except Exception as e:
            logger.error(f"❌ サイトアクセスエラー: {e}")
            return None

    def _extract_video_urls(self, html: str) -> List[Tuple[str, str]]:
        urls = []
        match_hd = re.search(r"video_alt_url\s*:\s*['\"]([^'\"]+)['\"]", html)
        if match_hd:
            urls.append(('HD', match_hd.group(1).strip().rstrip('/')))
        match_sd = re.search(r"video_url\s*:\s*['\"]([^'\"]+)['\"]", html)
        if match_sd:
            urls.append(('SD', match_sd.group(1).strip().rstrip('/')))
        return urls

    def _generate_filename(self, url: str) -> str:
        clean_url = url.split('?')[0].rstrip('/')
        raw_name = clean_url.split('/')[-1] or f"video_{int(time.time())}"
        safe_name = FileSystemManager.sanitize_filename(raw_name)
        return f"{safe_name}.mp4"

    def _execute_atomic_download(self, candidates: List[Tuple[str, str]], final_path: Path, src_url: str, save_dir: Path) -> bool:
        """アトミック書き込み (.tmp -> .mp4)"""
        temp_path = final_path.with_suffix('.tmp')
        self.session.headers['Referer'] = src_url
        
        for label, video_url in candidates:
            logger.info(f"↳ {label} を試行中...")
            try:
                with self.session.get(video_url, stream=True, timeout=CONFIG.REQUEST_TIMEOUT) as res:
                    if res.status_code == 404: continue
                    res.raise_for_status()
                    total_size = int(res.headers.get('content-length', 0))

                    logger.info(f"📥 ダウンロード中: {final_path.name}")
                    
                    with open(temp_path, 'wb') as f, tqdm(
                        total=total_size, 
                        unit='iB', 
                        unit_scale=True, 
                        unit_divisor=1024, 
                        colour='green', 
                        leave=False,
                        disable=not CONFIG.SHOW_PROGRESS_BAR
                    ) as bar:
                        for chunk in res.iter_content(chunk_size=1024*1024):
                            size = f.write(chunk)
                            bar.update(size)
                    
                    temp_path.rename(final_path)
                    
                    logger.info("✨ 完了")
                    # 通知内容からURLを削除 (保存先フォルダだけ残す)
                    DiscordNotifier.send(f"✅ **動画保存完了**\nファイル: `{final_path.name}`\n保存先: `{save_dir}`")
                    return True

            except Exception as e:
                logger.warning(f"⚠️ {label} 失敗: {e}")
                if temp_path.exists():
                    try: temp_path.unlink()
                    except OSError: pass
                continue
        
        logger.error("⛔ 全候補でのダウンロードに失敗しました")
        return False

# ==========================================
# 5. メインコントローラー
# ==========================================
class BatchDownloader:
    
    def __init__(self):
        self.session = NetworkManager.create_session()
        self._shutdown_requested = False
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        logger.info(f"🛑 停止シグナル検知 ({signum})。安全に終了します...")
        self._shutdown_requested = True

    def _get_strategy(self, url: str) -> DownloadStrategy:
        if "youtube.com" in url or "youtu.be" in url:
            return YoutubeStrategy(CONFIG.BASE_SAVE_DIR, self.session)
        elif "tktube" in url:
            return GenericStrategy(CONFIG.BASE_SAVE_DIR, self.session, sub_dir="tktube")
        else:
            return GenericStrategy(CONFIG.BASE_SAVE_DIR, self.session)

    def _wait_interval(self) -> None:
        if self._shutdown_requested: return
        minutes = CONFIG.INTERVAL_SECONDS / 60
        logger.info(f"💤 ネットワーク負荷軽減のため {minutes:.0f}分 待機します...")
        
        for _ in range(CONFIG.INTERVAL_SECONDS):
            if self._shutdown_requested:
                logger.info("🛑 待機をキャンセルして終了します。")
                break
            time.sleep(1)

    def run(self) -> None:
        # 0. 依存チェック
        SystemHealthChecker.check_dependencies()

        # 1. 前提条件チェック
        if not CONFIG.LIST_FILE_PATH.exists():
            logger.error(f"エラー: {CONFIG.LIST_FILE_PATH} が見つかりません。")
            return
        
        if not SystemHealthChecker.is_within_time_window():
            if FORCE_MODE:
                logger.info(f"⚠️ FORCEモード: 時間制限（{CONFIG.START_HOUR}:00 - {CONFIG.END_HOUR}:00）を無視して実行します。")
            else:
                logger.info(f"🕒 現在は指定時間外（{CONFIG.START_HOUR}:00 - {CONFIG.END_HOUR}:00）のため実行しません。")
                return

        if not SystemHealthChecker.verify_nas_mount():
            return

        # 2. リスト読み込み（重複排除 & 正規化）
        try:
            urls: Set[str] = set()
            with open(CONFIG.LIST_FILE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    clean_line = line.strip()
                    if clean_line and not clean_line.startswith("#"):
                        urls.add(clean_line)
            
            sorted_urls = sorted(list(urls))
            
        except UnicodeDecodeError:
            logger.error("リストファイルのエンコード読み込みに失敗しました。")
            return

        if not sorted_urls:
            logger.info("処理対象のURLがありません。")
            return

        logger.info("="*60)
        logger.info("   🚀 Robust Batch Downloader Started (v1.2.0)")
        logger.info(f"   Mode: {'FORCE (Limit Ignore)' if FORCE_MODE else 'NORMAL (Scheduled)'}")
        logger.info(f"   Targets: {len(sorted_urls)} unique URLs")
        logger.info(f"   Interval: {CONFIG.INTERVAL_SECONDS}s | Save: {CONFIG.BASE_SAVE_DIR}")
        logger.info("="*60)

        # 3. バッチ処理実行
        for i, url in enumerate(sorted_urls):
            if self._shutdown_requested:
                break
                
            if not SystemHealthChecker.is_within_time_window() and not FORCE_MODE:
                logger.info("⏰ 終了時刻になりました。本日の処理を中断します。")
                break

            if not url.startswith("http"):
                continue

            logger.info(f"\n[{i+1}/{len(sorted_urls)}] 処理開始: {url}")
            
            try:
                strategy = self._get_strategy(url)
                strategy.download(url)
            except Exception as e:
                logger.error(f"予期せぬエラーが発生しました: {e}", exc_info=True)

            if i < len(sorted_urls) - 1:
                self._wait_interval()

        logger.info("🎉 本日のスケジュール終了")

if __name__ == "__main__":
    downloader = BatchDownloader()
    downloader.run()