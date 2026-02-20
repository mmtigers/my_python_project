#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube URL Extractor (Integrated with MY_HOME_SYSTEM)
------------------------------------------------------
指定されたYouTubeチャンネルやプレイリストから動画URLを抽出するスクリプト。
MY_HOME_SYSTEMのエコシステム（ロガー、ディレクトリ構成）に準拠。
"""

import sys
import argparse
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Set, Iterator, Dict, Any
import sqlite3
from contextlib import closing

import yt_dlp

# ==========================================
# 0. 環境設定 & ロギング (Unified Logging)
# ==========================================
# プロジェクトルートへのパス解決 (DDD/ から core/ を参照するため)
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

try:
    from core.logger import get_logger
    from core.nas_utils import get_managed_target_directory
    logger = get_logger(__name__)
except ImportError:
    # 開発環境や単体実行時のフォールバック
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("UrlExtractor")
    def get_managed_target_directory(*args, **kwargs): return Path("./data")

# ==========================================
# 1. コンフィグレーション (Configuration)
# ==========================================
class AppConfig:
    """アプリケーション設定を保持する定数クラス。"""
    
    # File Paths
    BASE_DIR: Path = CURRENT_DIR
    NAS_DIR_STR: str = '/mnt/nas/home_system/youtube_extractor/data'  # 本環境のNASパスに適宜変更してください
    LOCAL_DIR_STR: str = str(BASE_DIR / 'data')
    MOUNT_POINT: str = '/mnt/nas'

    SUB_DIR_NAME: str = "list"
    SUBSCRIPTION_FILE: str = "subscriptions.txt"
    
    # yt-dlp オプション: 高速化のため extract_flat を使用
    YDL_OPTS: Dict[str, Any] = {
        'extract_flat': True,
        'quiet': True,
        'ignoreerrors': True,
        'no_warnings': True,
    }

    @classmethod
    def get_output_base_dir(cls) -> Path:
        """NASアクセスを検証・修復し、動的にベースディレクトリを解決する（遅延評価）。
        
        クラスロード時ではなく、実際のファイル処理が必要になったタイミングで
        マウント確認や自動修復ロジックを実行する。
        
        Returns:
            Path: 利用可能なディレクトリパス
        """
        return get_managed_target_directory(
            nas_dir_str=cls.NAS_DIR_STR,
            fallback_dir_str=cls.LOCAL_DIR_STR,
            mount_point=cls.MOUNT_POINT
        )


@dataclass
class ExtractionResult:
    """抽出結果を格納するデータクラス。

    Attributes:
        title (str): 動画リストまたはプレイリストのタイトル。
        urls (List[str]): 抽出されたURLのリスト。
        source_url (str): 抽出元のURL。
        channel_name (str): チャンネル名。不明な場合は 'unknown_channel'。
        is_playlist (bool): プレイリストの場合は True。
    """
    title: str
    urls: List[str]
    source_url: str
    channel_name: str = "unknown_channel"
    is_playlist: bool = False

# ==========================================
# 2. コアロジック (Extractor)
# ==========================================
class YouTubeExtractor:
    """YouTubeからURL情報を抽出するクラス。"""

    @staticmethod
    def _normalize_url(entry: Dict[str, Any]) -> Optional[str]:
        """エントリ情報から正規化されたYouTube URLを生成する。

        Args:
            entry (Dict[str, Any]): yt-dlp から取得したエントリ辞書。

        Returns:
            Optional[str]: 正規化されたURL。生成できない場合は None。
        """
        url = entry.get('url') or entry.get('webpage_url')
        video_id = entry.get('id')
        
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        
        if url and ("youtube.com" in url or "youtu.be" in url):
            return url
        return None

    def _is_channel_url(self, url: str) -> bool:
        """指定されたURLがチャンネルトップページのURLかを判定する。

        Args:
            url (str): 判定対象のURL。

        Returns:
            bool: チャンネルURLであれば True。
        """
        clean_url = url.split('?')[0].rstrip('/')
        return bool(re.search(r"youtube\.com/(@[\w\-\.]+|channel/[\w\-]+|c/[\w\-]+|user/[\w\-]+)$", clean_url))

    def _extract_single_list(self, target_url: str, force_title: str = "") -> Optional[ExtractionResult]:
        """単一のURL（動画リストやプレイリスト）から情報を抽出する。

        Args:
            target_url (str): 対象のURL。
            force_title (str, optional): タイトルを強制指定する場合に使用。

        Returns:
            Optional[ExtractionResult]: 抽出結果オブジェクト。失敗時は None。
        """
        logger.info(f"🔍 解析開始: {target_url}")
        
        results: Set[str] = set()
        list_title = force_title or "unknown_list"
        channel_name = "unknown_channel"

        try:
            with yt_dlp.YoutubeDL(AppConfig.YDL_OPTS) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if not info:
                    return None

                channel_name = info.get('channel') or info.get('uploader') or "unknown_channel"
                if not force_title:
                    list_title = info.get('title') or "extracted_urls"
                
                entries = info.get('entries')
                if entries:
                    logger.info(f"   ↳ リスト取得中: '{list_title}' (by {channel_name})")
                    for entry in entries:
                        if not entry:
                            continue
                        url = self._normalize_url(entry)
                        if url:
                            results.add(url)
                else:
                    # 単一動画の場合
                    url = self._normalize_url(info)
                    if url:
                        results.add(url)

        except Exception:
            # Error Handling: スタックトレースを含めてログ出力
            logger.error(f"❌ 抽出失敗 ({target_url})", exc_info=True)
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
        """URLの種類に応じて再帰的または単発で抽出を行うイテレータ。

        チャンネルURLの場合は `/videos` と `/playlists` を自動探索する。

        Args:
            target_url (str): 開始URL。

        Yields:
            Iterator[ExtractionResult]: 抽出結果を順次返す。
        """
        if self._is_channel_url(target_url):
            logger.info("ℹ️ チャンネルURLを検出。詳細スキャンを開始します。")
            base_url = target_url.split('?')[0].rstrip('/')

            # Phase 1: All Videos
            video_result = self._extract_single_list(f"{base_url}/videos")
            if video_result:
                # チャンネル動画一覧であることを明記
                # dataclassはfrozenではないため属性変更可能だが、設計上新しいインスタンスの方が安全
                yield ExtractionResult(
                    title=f"{video_result.title} - All Videos",
                    urls=video_result.urls,
                    source_url=video_result.source_url,
                    channel_name=video_result.channel_name,
                    is_playlist=False
                )

            # Phase 2: Playlists
            try:
                with yt_dlp.YoutubeDL(AppConfig.YDL_OPTS) as ydl:
                    pl_tab = ydl.extract_info(f"{base_url}/playlists", download=False)
                    if pl_tab and 'entries' in pl_tab:
                        playlists = list(pl_tab['entries'])
                        logger.info(f"📂 {len(playlists)} 個のプレイリストが見つかりました。")
                        for pl in playlists:
                            if not pl:
                                continue
                            pl_url = pl.get('url')
                            pl_title = pl.get('title', 'Unknown Playlist')
                            if pl_url:
                                res = self._extract_single_list(pl_url, force_title=pl_title)
                                if res:
                                    res.is_playlist = True
                                    yield res
            except Exception:
                logger.error("❌ プレイリスト一覧の取得に失敗しました", exc_info=True)
        else:
            res = self._extract_single_list(target_url)
            if res:
                yield res

# ==========================================
# 3. ファイル管理 & サブスクリプション
# ==========================================
class FileManager:
    """ファイル保存に関する責務を持つクラス。"""
    
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """ファイル名として使用できない文字を置換する。

        Args:
            filename (str): 元の文字列。

        Returns:
            str: 安全なファイル名文字列。
        """
        safe = re.sub(r'[\\/*?:"<>|]', '_', filename).strip()
        return safe[:200].strip('. ')

    def save(self, result: ExtractionResult) -> bool:
        """抽出結果をテキストファイルに保存する。

        Args:
            result (ExtractionResult): 保存対象の抽出データ。

        Returns:
            bool: 保存に成功した場合は True。
        """
        # 遅延評価でディレクトリを取得
        target_dir = AppConfig.get_output_base_dir() / AppConfig.SUB_DIR_NAME
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"❌ ディレクトリ作成失敗: {target_dir}", exc_info=True)
            return False

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
        except IOError:
            logger.error(f"❌ ファイル書き込みエラー: {output_path}", exc_info=True)
            return False

class SubscriptionManager:
    """
    定期巡回（サブスクリプション）を管理するクラス。
    SSOTポリシーに基づき、SQLite DBを用いて状態を管理する。
    """

    def __init__(self, extractor: YouTubeExtractor, file_manager: FileManager):
        self.extractor = extractor
        self.file_manager = file_manager
        
        # DBはNASのベースディレクトリの1つ上の階層（home_system直下）に配置
        self.db_path = AppConfig.get_output_base_dir().parent / "home_system.db"

    def _verify_environment(self) -> bool:
        """
        NASのマウント状態（フォールバック中ではないか）を検証する。
        
        Returns:
            bool: 正常なNAS環境であれば True、ローカルフォールバック中であれば False
        """
        current_base = AppConfig.get_output_base_dir()
        if AppConfig.LOCAL_DIR_STR in str(current_base):
            logger.error("🚨 NASがアンマウント状態（ローカルフォールバック中）を検知しました。")
            logger.error("データの不整合・上書きを防ぐため、サブスクリプション処理をFail-Softで中断します。")
            return False
        return True

    def _init_db(self) -> None:
        """サブスクリプション管理用のテーブルが存在しない場合は作成する。"""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with closing(conn.cursor()) as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS youtube_subscriptions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_url TEXT UNIQUE NOT NULL,
                        is_active INTEGER DEFAULT 1,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            conn.commit()

    def process_subscriptions(self) -> None:
        """登録されたチャンネルリストをDBから読み込み、順次抽出を実行する。"""
        # 1. 環境検証（データロスト防止の防波堤）
        if not self._verify_environment():
            return

        # 2. DB初期化
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
        except sqlite3.Error as e:
            logger.error(f"❌ DB初期化エラー: {e}", exc_info=True)
            return

        urls: List[str] = []
        
        # 3. DBからアクティブなサブスクリプションを取得
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                with closing(conn.cursor()) as cur:
                    cur.execute("SELECT channel_url FROM youtube_subscriptions WHERE is_active = 1")
                    rows = cur.fetchall()
                    urls = [row[0] for row in rows]
        except sqlite3.Error as e:
            logger.error(f"❌ DB読み込みエラー: {e}", exc_info=True)
            return

        if not urls:
            logger.debug("DBにアクティブなサブスクリプションが登録されていません。")
            return

        logger.info(f"🔄 サブスクリプション巡回開始: {len(urls)} 件 (Source: SQLite DB)")
        
        for i, url in enumerate(urls):
            logger.debug(f"[{i+1}/{len(urls)}] 巡回処理中: {url}")
            for result in self.extractor.extract_iter(url):
                self.file_manager.save(result)
                
# ==========================================
# 4. アプリケーション本体
# ==========================================
class UrlExtractorApp:
    """アプリケーションのエントリーポイントクラス。"""

    def __init__(self):
        self.extractor = YouTubeExtractor()
        self.file_manager = FileManager()
        self.sub_manager = SubscriptionManager(self.extractor, self.file_manager)

    def run(self) -> None:
        """コマンドライン引数を解析し、メイン処理を実行する。"""
        logger.info("=== YouTube URL Extractor (v3.1.0) Started ===")

        parser = argparse.ArgumentParser(description="Extract YouTube URLs from channels or playlists.")
        parser.add_argument("url", nargs="?", help="Target YouTube URL")
        parser.add_argument("--cron", action="store_true", help="Auto-subscription mode")
        args = parser.parse_args()

        if args.cron:
            self.sub_manager.process_subscriptions()
            logger.info("🎉 自動巡回プロセスが完了しました")
            return

        target_url = args.url
        if not target_url:
            # 対話モード（loggerではなくinputを使用）
            try:
                print("URLを入力してください (Enterで終了):")
                target_url = input("> ").strip()
            except KeyboardInterrupt:
                logger.info("ユーザーにより中断されました")
                sys.exit(0)

        if target_url:
            total_files = 0
            # イテレータを回して処理
            for result in self.extractor.extract_iter(target_url):
                if self.file_manager.save(result):
                    total_files += 1
            logger.info(f"🎉 処理完了: 計 {total_files} ファイルを作成しました")
        else:
            logger.info("URLが指定されなかったため終了します")

if __name__ == "__main__":
    app = UrlExtractorApp()
    app.run()