#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube URL Extractor (v3.0.0 Auto-Subscription)
-----------------------------------------------
Features:
- Subscription Mode: Auto-crawl channels listed in 'subscriptions.txt'.
- Channel Crawling: Extracts /videos & /playlists automatically.
- Organized Output: Saves to 'list/Channel_Playlist.txt'.
- High Performance Metadata Extraction (extract_flat).
"""

import sys
import argparse
import logging
import re
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Set, Iterator
import yt_dlp

# ==========================================
# 0. 環境設定 & ロギング
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("UrlExtractor")

CURRENT_DIR = Path(__file__).resolve().parent

# ==========================================
# 1. コンフィグレーション
# ==========================================
@dataclass(frozen=True)
class AppConfig:
    OUTPUT_DIR: Path = CURRENT_DIR
    SUB_DIR_NAME: str = "list"
    SUBSCRIPTION_FILE: str = "subscriptions.txt"
    
    YDL_OPTS: dict = field(default_factory=lambda: {
        'extract_flat': True,
        'quiet': True,
        'ignoreerrors': True,
        'no_warnings': True,
    })

CONFIG = AppConfig()

@dataclass
class ExtractionResult:
    title: str
    urls: List[str]
    source_url: str
    channel_name: str = "unknown_channel"
    is_playlist: bool = False

# ==========================================
# 2. コアロジック (Extractor)
# ==========================================
class YouTubeExtractor:
    
    @staticmethod
    def _normalize_url(entry: dict) -> Optional[str]:
        url = entry.get('url') or entry.get('webpage_url')
        video_id = entry.get('id')
        
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        
        if url and ("youtube.com" in url or "youtu.be" in url):
            return url
        return None

    def _is_channel_url(self, url: str) -> bool:
        clean_url = url.split('?')[0].rstrip('/')
        return bool(re.search(r"youtube\.com/(@[\w\-\.]+|channel/[\w\-]+|c/[\w\-]+|user/[\w\-]+)$", clean_url))

    def _extract_single_list(self, target_url: str, force_title: str = "") -> Optional[ExtractionResult]:
        logger.info(f"🔍 解析中...: {target_url}")
        
        results: Set[str] = set()
        list_title = force_title or "unknown_list"
        channel_name = "unknown_channel"

        try:
            with yt_dlp.YoutubeDL(CONFIG.YDL_OPTS) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if not info: return None

                channel_name = info.get('channel') or info.get('uploader') or "unknown_channel"
                if not force_title:
                    list_title = info.get('title') or "extracted_urls"
                
                entries = info.get('entries')
                if entries:
                    logger.info(f"   ↳ リスト取得中: '{list_title}' (by {channel_name})")
                    for entry in entries:
                        if not entry: continue
                        url = self._normalize_url(entry)
                        if url: results.add(url)
                else:
                    url = self._normalize_url(info)
                    if url: results.add(url)

        except Exception as e:
            logger.warning(f"⚠️ 抽出スキップ ({target_url}): {e}")
            return None

        sorted_urls = sorted(list(results))
        if not sorted_urls:
            return None

        return ExtractionResult(
            title=list_title,
            urls=sorted_urls,
            source_url=target_url,
            channel_name=channel_name
        )

    def extract_iter(self, target_url: str) -> Iterator[ExtractionResult]:
        if self._is_channel_url(target_url):
            logger.info("ℹ️ チャンネルURLを検出。詳細スキャンを開始します。")
            base_url = target_url.split('?')[0].rstrip('/')

            # Phase 1: All Videos
            video_result = self._extract_single_list(f"{base_url}/videos")
            if video_result:
                video_result.title += " - All Videos"
                yield video_result

            # Phase 2: Playlists
            try:
                with yt_dlp.YoutubeDL(CONFIG.YDL_OPTS) as ydl:
                    pl_tab = ydl.extract_info(f"{base_url}/playlists", download=False)
                    if pl_tab and 'entries' in pl_tab:
                        playlists = list(pl_tab['entries'])
                        logger.info(f"📂 {len(playlists)} 個のプレイリストが見つかりました。")
                        for pl in playlists:
                            if not pl: continue
                            pl_url = pl.get('url')
                            pl_title = pl.get('title', 'Unknown Playlist')
                            if pl_url:
                                res = self._extract_single_list(pl_url, force_title=pl_title)
                                if res:
                                    res.is_playlist = True
                                    yield res
            except Exception as e:
                logger.error(f"❌ プレイリスト一覧取得失敗: {e}")
        else:
            res = self._extract_single_list(target_url)
            if res: yield res

# ==========================================
# 3. ファイル管理 & サブスクリプション
# ==========================================
class FileManager:
    
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        safe = re.sub(r'[\\/*?:"<>|]', '_', filename).strip()
        return safe[:200].strip('. ')

    def save(self, result: ExtractionResult, output_base_dir: Path) -> bool:
        target_dir = output_base_dir / CONFIG.SUB_DIR_NAME
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_channel = self._sanitize_filename(result.channel_name)
        safe_title = self._sanitize_filename(result.title)
        
        filename = f"{safe_title}.txt" if safe_channel == "unknown_channel" else f"{safe_channel}_{safe_title}.txt"
        output_path = target_dir / filename

        try:
            with output_path.open("w", encoding="utf-8") as f:
                for url in result.urls:
                    f.write(url + "\n")
            logger.info(f"✅ 保存完了: {filename} ({len(result.urls)} 件)")
            return True
        except IOError as e:
            logger.error(f"❌ ファイル保存エラー: {e}")
            return False

class SubscriptionManager:
    def __init__(self, extractor: YouTubeExtractor, file_manager: FileManager):
        self.extractor = extractor
        self.file_manager = file_manager
        self.sub_file = CONFIG.OUTPUT_DIR / CONFIG.SUBSCRIPTION_FILE

    def process_subscriptions(self):
        if not self.sub_file.exists():
            logger.warning(f"⚠️ {CONFIG.SUBSCRIPTION_FILE} が見つかりません。")
            with self.sub_file.open("w", encoding="utf-8") as f:
                f.write("# ここにチャンネルURLを1行ずつ記述してください\n")
            logger.info(f"🆕 空のファイルを作成しました: {self.sub_file}")
            return

        with self.sub_file.open("r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

        logger.info(f"🔄 サブスクリプション巡回開始: {len(urls)} 件")
        
        for i, url in enumerate(urls):
            logger.info(f"\n[{i+1}/{len(urls)}] 巡回中: {url}")
            for result in self.extractor.extract_iter(url):
                self.file_manager.save(result, CONFIG.OUTPUT_DIR)

# ==========================================
# 4. アプリケーション本体
# ==========================================
class UrlExtractorApp:
    def __init__(self):
        self.extractor = YouTubeExtractor()
        self.file_manager = FileManager()
        self.sub_manager = SubscriptionManager(self.extractor, self.file_manager)

    def run(self):
        print("=" * 50)
        print("   YouTube URL Extractor (v3.0.0)")
        print("=" * 50)

        parser = argparse.ArgumentParser()
        parser.add_argument("url", nargs="?", help="抽出対象のYouTube URL")
        parser.add_argument("--cron", action="store_true", help="サブスクリプション自動巡回モード")
        args = parser.parse_args()

        if args.cron:
            self.sub_manager.process_subscriptions()
            logger.info("🎉 自動巡回完了")
            return

        target_url = args.url
        if not target_url:
            try:
                target_url = input("URLを入力してください (Enterで終了):\n> ").strip()
            except KeyboardInterrupt:
                sys.exit(0)

        if target_url:
            total_files = 0
            for result in self.extractor.extract_iter(target_url):
                if self.file_manager.save(result, CONFIG.OUTPUT_DIR):
                    total_files += 1
            logger.info(f"🎉 処理完了: {total_files} ファイル作成")

if __name__ == "__main__":
    app = UrlExtractorApp()
    app.run()