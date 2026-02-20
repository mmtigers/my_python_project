#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Production Grade Batch Downloader (v2.2.0 Universal Support)
-------------------------------------------------
Features:
- Multi-List Support: Automatically processes all files in 'list/' directory.
- Smart Organization: Creates subfolders based on list filenames.
- Atomic File Writes & Robust Error Handling.
- Download History Management.
- Discord Notifications.
- Schedule: 02:00 - 06:00.
- Universal Support: Uses yt-dlp for ALL supported sites (not just YouTube).
- Specialized Scraping: Specific logic for 'tktube'.
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
import glob
from collections import defaultdict
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Any, Set, NamedTuple
from dataclasses import dataclass, field
from pathlib import Path

# External Libraries
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm
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

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR
for _ in range(3):
    if (PROJECT_ROOT / "services").exists():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
else:
    PROJECT_ROOT = Path("/home/masahiro/develop/MY_HOME_SYSTEM")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

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
    RESTRICT_TIME: bool = not FORCE_MODE
    START_HOUR: int = 2
    END_HOUR: int = 6
    SHORT_SLEEP_SECONDS: int = 5
    MIN_FREE_SPACE_GB: int = 50
    
    # 【追加】機能フラグ: 環境変数で制御可能にする (デフォルトはFalse=無効のまま維持)
    ENABLE_YOUTUBE_DL: bool = os.getenv("ENABLE_YOUTUBE_DL", "false").lower() == "true"
    BASE_SAVE_DIR: Path = Path(os.getenv("VIDEO_SAVE_DIR", "/mnt/nas/ddd"))
    LIST_FILE_PATH: Path = CURRENT_DIR / "list.txt"
    LIST_DIR_PATH: Path = CURRENT_DIR / "list"
    HISTORY_FILE_PATH: Path = CURRENT_DIR / "history.txt"
    NAS_MOUNT_POINT: Path = Path("/mnt/nas")
    NAS_MARKER_FILE: str = ".mounted"
    
    REQUEST_TIMEOUT: int = 20
    MAX_RETRIES: int = 3
    
    # 抽出パターン (Specialized Scraping)
    URL_PATTERNS: List[Tuple[str, str]] = field(default_factory=lambda: [
        ('HD', r"video_alt_url\s*:\s*['\"]([^'\"]+)['\"]"),
        ('SD', r"video_url\s*:\s*['\"]([^'\"]+)['\"]"),
    ])
    
    SHOW_PROGRESS_BAR: bool = sys.stdout.isatty()

    @property
    def nas_marker_path(self) -> Path:
        return self.NAS_MOUNT_POINT / self.NAS_MARKER_FILE

CONFIG = AppConfig()

class DownloadTask(NamedTuple):
    url: str
    source_name: str

# ==========================================
# 2. マネージャー & ユーティリティ
# ==========================================
class DiscordNotifier:
    @staticmethod
    def send(text: str, is_error: bool = False) -> None:
        channel = 'error' if is_error else 'notify'
        message = {"type": "text", "text": text}
        try:
            _send_discord_webhook([message], channel=channel)
        except Exception as e:
            logger.error(f"⚠️ Discord通知エラー: {e}")

class HistoryManager:
    @staticmethod
    def load_history() -> Set[str]:
        history = set()
        if CONFIG.HISTORY_FILE_PATH.exists():
            try:
                with open(CONFIG.HISTORY_FILE_PATH, "r", encoding="utf-8") as f:
                    history = {line.strip() for line in f if line.strip()}
            except Exception: pass
        return history

    @staticmethod
    def add_history(url: str) -> None:
        try:
            with open(CONFIG.HISTORY_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(f"{url}\n")
        except Exception: pass

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
        return re.sub(r'[\\/*?:"<>|]', '_', filename)

    @staticmethod
    def ensure_dir(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except PermissionError:
            DiscordNotifier.send(f"❌ 権限エラー: {path}", is_error=True)
            return False

    @staticmethod
    def check_disk_space(path: Path) -> bool:
        try:
            check_path = path
            while not check_path.exists():
                check_path = check_path.parent
                if check_path == check_path.parent: break
            total, used, free = shutil.disk_usage(check_path)
            if (free // (2**30)) < CONFIG.MIN_FREE_SPACE_GB:
                DiscordNotifier.send(f"⚠️ DISK FULL: 残り {free // (2**30)}GB", is_error=True)
                return False
            return True
        except Exception:
            return True

class SystemHealthChecker:
    @staticmethod
    def is_within_time_window() -> bool:
        if not CONFIG.RESTRICT_TIME: return True
        return CONFIG.START_HOUR <= datetime.datetime.now().hour < CONFIG.END_HOUR

    @staticmethod
    def verify_nas_mount() -> bool:
        if not CONFIG.NAS_MOUNT_POINT.exists() or not CONFIG.nas_marker_path.exists():
            DiscordNotifier.send("⛔ CRITICAL: NASマウントエラー", is_error=True)
            return False
        return True
    
    @staticmethod
    def check_dependencies() -> None:
        if shutil.which("ffmpeg") is None:
            logger.warning("⚠️ ffmpeg not found.")

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

# ★変更点: YouTubeStrategy を UniversalYtDlpStrategy に名称変更し、汎用的に利用
class UniversalYtDlpStrategy(DownloadStrategy):
    def download(self, task: DownloadTask) -> bool:
        logger.info(f"🎥 Universal処理: {task.url} (List: {task.source_name})")
        
        # YouTubeの場合は "youtube" フォルダ、それ以外は "others" フォルダに分類
        category = "youtube" if "youtube.com" in task.url or "youtu.be" in task.url else "others"
        
        target_dir = self._determine_save_dir(task.source_name, category)
        if not target_dir: return False

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': f'{str(target_dir)}/%(title)s.%(ext)s',
            'merge_output_format': 'mp4',
            'quiet': True, 'no_warnings': True, 'nopart': False,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(task.url, download=False)
                filename = Path(ydl.prepare_filename(info)).with_suffix('.mp4')
                
                if self._should_skip(filename): return True

                logger.info(f"📥 DL開始: {info.get('title')}")
                ydl.download([task.url])
                DiscordNotifier.send(f"✅ 動画保存完了\nファイル: `{filename.name}`")
                return True
        except Exception as e:
            logger.error(f"⚠️ Universal DL エラー: {e}")
            return False

# ★変更点: スクレイピングが必要な特定サイト専用
class ScrapingStrategy(DownloadStrategy):
    def download(self, task: DownloadTask) -> bool:
        category = "tktube"
        target_dir = self._determine_save_dir(task.source_name, category)
        if not target_dir: return False

        html = self._fetch_html(task.url)
        if not html: return False

        candidates = self._extract_video_urls(html)
        if not candidates:
            logger.warning("⚠️ 動画リンクなし")
            return False

        filename = FileSystemManager.sanitize_filename(task.url.split('?')[0].rstrip('/').split('/')[-1] or f"vid_{int(time.time())}") + ".mp4"
        final_path = target_dir / filename

        if self._should_skip(final_path): return True
        return self._execute_atomic_download(candidates, final_path, task.url, target_dir)

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            self.session.headers['Referer'] = url
            res = self.session.get(url, timeout=CONFIG.REQUEST_TIMEOUT)
            return res.text
        except Exception: return None

    def _extract_video_urls(self, html: str) -> List[Tuple[str, str]]:
        urls = []
        for label, pattern in CONFIG.URL_PATTERNS:
            match = re.search(pattern, html)
            if match: urls.append((label, match.group(1).strip().rstrip('/')))
        return urls

    def _execute_atomic_download(self, candidates: List[Tuple[str, str]], final_path: Path, src_url: str, save_dir: Path) -> bool:
        temp_path = final_path.with_suffix('.tmp')
        self.session.headers['Referer'] = src_url
        
        for label, video_url in candidates:
            try:
                with self.session.get(video_url, stream=True, timeout=CONFIG.REQUEST_TIMEOUT) as res:
                    if res.status_code == 404: continue
                    total = int(res.headers.get('content-length', 0))
                    with open(temp_path, 'wb') as f, tqdm(total=total, unit='iB', unit_scale=True, disable=not CONFIG.SHOW_PROGRESS_BAR) as bar:
                        for chunk in res.iter_content(1024*1024):
                            size = f.write(chunk)
                            bar.update(size)
                    temp_path.rename(final_path)
                    DiscordNotifier.send(f"✅ 動画保存完了\nファイル: `{final_path.name}`\n場所: `{save_dir.name}`")
                    return True
            except Exception:
                if temp_path.exists(): temp_path.unlink()
                continue
        return False

# ==========================================
# 4. メインコントローラー
# ==========================================
class BatchDownloader:
    def __init__(self):
        self.session = NetworkManager.create_session()
        self._shutdown_requested = False
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        self.history = HistoryManager.load_history()

    def _signal_handler(self, signum: int, frame: Any) -> None:
        logger.info("🛑 停止シグナル検知")
        self._shutdown_requested = True

    def _get_strategy(self, url: str) -> Optional[DownloadStrategy]:
        # 【修正】ハードコードではなく、設定フラグで制御するように変更
        if "youtube.com" in url or "youtu.be" in url:
            if not CONFIG.ENABLE_YOUTUBE_DL:
                logger.info(f"🚫 YouTube機能は設定により無効化されています: {url}")
                return None
            # 有効な場合は通常のフローへ進む

        # 既存のロジック: tktubeなら専用ストラテジー、それ以外はUniversal
        if "tktube" in url:
            return ScrapingStrategy(CONFIG.BASE_SAVE_DIR, self.session)
        else:
            # YouTube以外の汎用サイト（Twitter/X, Vimeoなど）は引き続きダウンロード可能
            return UniversalYtDlpStrategy(CONFIG.BASE_SAVE_DIR, self.session)

    def _collect_tasks(self) -> List[DownloadTask]:
        tasks = []
        
        if CONFIG.LIST_FILE_PATH.exists():
            with open(CONFIG.LIST_FILE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    url = line.strip()
                    if url and not url.startswith("#") and url not in self.history:
                        tasks.append(DownloadTask(url, "list"))
        
        if CONFIG.LIST_DIR_PATH.exists():
            for list_file in CONFIG.LIST_DIR_PATH.glob("*.txt"):
                source_name = list_file.stem
                try:
                    with open(list_file, "r", encoding="utf-8") as f:
                        for line in f:
                            url = line.strip()
                            if url and not url.startswith("#") and url not in self.history:
                                tasks.append(DownloadTask(url, source_name))
                except Exception as e:
                    logger.error(f"リスト読み込みエラー ({list_file.name}): {e}")

        unique_tasks = {}
        for t in tasks:
            if t.url not in unique_tasks:
                unique_tasks[t.url] = t
        
        return list(unique_tasks.values())

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
            logger.error(f"⚠️ アーカイブファイルへの書き込みに失敗しました: {e}")
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
                    url = line.strip()
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
                logger.error(f"⚠️ リストファイル({file_path.name})のパージ処理に失敗しました: {e}")

        logger.info(f"🧹 期限切れ（無効）のタスク {deleted_count} 件をパージしました。")


    def run(self) -> None:
        SystemHealthChecker.check_dependencies()
        
        if not SystemHealthChecker.is_within_time_window():
            if FORCE_MODE: 
                logger.debug("⚠️ FORCEモード: 時間制限無視")
            else:
                logger.debug(f"🕒 指定時間外（{CONFIG.START_HOUR}:00 - {CONFIG.END_HOUR}:00）のため終了")
                return

        if not SystemHealthChecker.verify_nas_mount(): 
            return

        tasks = self._collect_tasks()
        if not tasks:
            logger.debug("処理対象のURLがありません。")
            return
        
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
            logger.debug("パージ処理の結果、実行可能なタスクがなくなりました。")
            return

        logger.info("="*60)
        logger.info("   🚀 Smart Pipeline Downloader (v2.2.0)")
        logger.info(f"   Schedule: {CONFIG.START_HOUR}:00 - {CONFIG.END_HOUR}:00")
        logger.info(f"   Tasks: {len(tasks)}")
        logger.info("="*60)

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
            except Exception as e:
                logger.error(f"エラー: {e}")

            if i < len(tasks) - 1:
                if not self._shutdown_requested:
                    time.sleep(CONFIG.SHORT_SLEEP_SECONDS)

        logger.info("🎉 全処理終了")

if __name__ == "__main__":
    BatchDownloader().run()