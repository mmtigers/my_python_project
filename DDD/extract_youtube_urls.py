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
import time
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Set, Iterator, Dict, Any
import sqlite3
from contextlib import closing

import yt_dlp

from file_utils import sanitize_filename as _shared_sanitize_filename

# ==========================================
# 0. 環境設定 & ロギング (Unified Logging)
# ==========================================
# プロジェクトルートへのパス解決 (DDD/ から MY_HOME_SYSTEM/core/ を参照するため)
# newface_monitor.py と同じ方式: core/ は develop/MY_HOME_SYSTEM/core に実在する
# (develop/core ではない)。DDDの単なる親ディレクトリではImportErrorになり、
# 常にローカルフォールバック用スタブへ落ちてしまっていた。
CURRENT_DIR = Path(__file__).resolve().parent  # ~/develop/DDD
PROJECT_ROOT = CURRENT_DIR.parent / "MY_HOME_SYSTEM"  # ~/develop/MY_HOME_SYSTEM
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

    def get_managed_target_directory(*args, **kwargs) -> Path:
        # 呼び出し元(get_output_base_dir)はfallback_dir_str(BASE_DIR/'data'の絶対パス)を
        # 渡してくる想定。これを無視してカレントディレクトリ相対の"./data"を返すと、
        # 実行時のカレントディレクトリ次第で保存先・DBパスが毎回変わってしまう
        # (newface_monitor.pyで修正済みの同一バグ)。
        fallback_dir_str = kwargs.get("fallback_dir_str")
        if fallback_dir_str:
            return Path(fallback_dir_str)
        return Path("./data")

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

    # レート制限対策: チャンネル/URLごとの巡回間隔とサーキットブレーカー閾値
    SUBSCRIPTION_SLEEP_RANGE: tuple = (2.0, 5.0)
    CONSECUTIVE_FAILURE_THRESHOLD: int = 3
    
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
            # yt_dlp.YoutubeDL.__init__は渡されたparams辞書を直接書き換える
            # （実測でjs_runtimes/http_headers/outtmpl等のキーが追加される）ため、
            # AppConfig.YDL_OPTSというクラス属性の共有辞書をそのまま渡すと、
            # このメソッドが繰り返し呼ばれる（チャンネル毎・プレイリスト毎）うちに
            # 呼び出し間で状態が汚染されるリスクがある。呼び出しごとにコピーを渡す。
            with yt_dlp.YoutubeDL(dict(AppConfig.YDL_OPTS)) as ydl:
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
                # 呼び出し間の状態汚染を避けるためコピーを渡す（理由は上のコメント参照）
                with yt_dlp.YoutubeDL(dict(AppConfig.YDL_OPTS)) as ydl:
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
    def _sanitize_filename(filename: str, max_length: int = 200) -> str:
        """ファイル名として使用できない文字を置換する。

        Args:
            filename (str): 元の文字列。
            max_length (int): 生成する文字列の最大バイト数（UTF-8エンコード後）。

        Returns:
            str: 安全なファイル名文字列。
        """
        return _shared_sanitize_filename(filename, max_length=max_length)

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

        # #175: 各コンポーネントを既定のmax_length(200バイト)のまま連結すると、
        # "{safe_channel}_{safe_title}.txt" は最大 200+1+200+4=405 バイトとなり
        # ext4等の255バイト上限を確実に超過する。チャンネル名と動画タイトルの
        # 両方を含めても合計が255バイトに収まるよう、それぞれの上限を100バイトに
        # 抑える(100+1(区切り)+100+4(".txt")=205バイト、安全マージンあり)。
        safe_channel = self._sanitize_filename(result.channel_name, max_length=100)
        safe_title = self._sanitize_filename(result.title, max_length=100)

        filename = f"{safe_title}.txt" if safe_channel == "unknown_channel" else f"{safe_channel}_{safe_title}.txt"
        output_path = target_dir / filename

        if output_path.exists():
            logger.warning(f"⚠️ 上書き: {filename} は既に存在します（チャンネル名/タイトルが重複している可能性）")

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
        # ★バグ修正(Issue #123): db_pathは以前ここ(__init__時点)で一度だけ確定していたが、
        # process_subscriptions()実行のたびに評価し直す方式に変更したため、インスタンス
        # 属性としては持たない(詳細はprocess_subscriptions()のコメント参照)。

    def _verify_environment(self, current_base: Optional[Path] = None) -> bool:
        """
        NASのマウント状態（フォールバック中ではないか）を検証する。

        Args:
            current_base (Optional[Path]): 検証対象のベースディレクトリ。省略時は
                AppConfig.get_output_base_dir()を呼び出して取得する。
                get_output_base_dir()はマウント確認・自己修復・障害通知を伴う重い
                処理のため、呼び出し元が既に同一時点の値を取得済みの場合はそれを
                渡して使い回すこと(process_subscriptions()参照)。

        Returns:
            bool: 正常なNAS環境であれば True、ローカルフォールバック中であれば False
        """
        if current_base is None:
            current_base = AppConfig.get_output_base_dir()
        # 絶対パスの包含チェック(旧実装)は、フォールバック関数がkwargsを無視して
        # CWD相対の"./data"を返すバグと組み合わさると、絶対パスのLOCAL_DIR_STRが
        # 短い相対パス文字列に決して含まれず、フォールバック状態を検知できなかった。
        # パス正規化した上での比較にすることで、表記揺れに関わらず確実に検知する。
        if current_base.resolve() == Path(AppConfig.LOCAL_DIR_STR).resolve():
            logger.error("🚨 NASがアンマウント状態(ローカルフォールバック中)を検知しました。")
            logger.error("データの不整合・上書きを防ぐため、サブスクリプション処理をFail-Softで中断します。")
            return False
        return True

    def _init_db(self, db_path: Path) -> None:
        """サブスクリプション管理用のテーブルが存在しない場合は作成する。"""
        with closing(sqlite3.connect(db_path)) as conn:
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
        # ★バグ修正(Issue #123): 以前はdb_pathを__init__時点のNAS状態で固定していたため、
        # プロセス起動時にNASがフォールバック中で、その後この巡回開始時までにNASが復帰
        # していると(autofsの再マウント遅延はこのリポジトリで既知の事象)、ここでの検証
        # 自体は最新のNAS状態を見て通過するのにdb_pathだけ古いローカルパスのまま取り
        # 残されていた。結果、ローカルに空DBが新規作成されてSELECTが0件になり、「アク
        # ティブなサブスクリプションが登録されていません」で無言のno-op終了していた
        # (巡回1回分が静かにスキップされ、ゴミの空DDD/home_system.dbが残る)。
        # get_output_base_dir()の呼び出し結果を1回だけ取得し、検証とdb_path導出の
        # 両方をその同一時点の値から行うことで、評価タイミングのズレを無くす
        # (get_output_base_dir()はNASの自己修復・障害通知を伴う重い処理のため、
        # 呼び出し回数も1回に抑える)。
        current_base = AppConfig.get_output_base_dir()
        if not self._verify_environment(current_base):
            return
        db_path = current_base.parent / "home_system.db"

        # 2. DB初期化
        # #185: db_path.parent.mkdir()が送出しうるOSError(権限エラー・読み取り専用
        # マウント等)は sqlite3.Error のサブクラスではないため捕捉されず、本メソッド
        # 内の他の失敗経路(ログ出力+安全なreturn)というフェイルソフト方針に反して
        # --cron実行全体が未処理例外で異常終了していた。OSErrorも合わせて捕捉する。
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db(db_path)
        except (sqlite3.Error, OSError) as e:
            logger.error(f"❌ DB初期化エラー: {e}", exc_info=True)
            return

        urls: List[str] = []

        # 3. DBからアクティブなサブスクリプションを取得
        try:
            with closing(sqlite3.connect(db_path)) as conn:
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

        consecutive_failures = 0
        for i, url in enumerate(urls):
            if i > 0:
                # レート制限/Bot検知対策: リクエスト間にジッター付きの待機を挟む
                time.sleep(random.uniform(*AppConfig.SUBSCRIPTION_SLEEP_RANGE))

            logger.debug(f"[{i+1}/{len(urls)}] 巡回処理中: {url}")
            got_result = False
            for result in self.extractor.extract_iter(url):
                self.file_manager.save(result)
                got_result = True

            if got_result:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                logger.warning(f"⚠️ 抽出結果を取得できませんでした ({url}) — 連続失敗数: {consecutive_failures}")
                if consecutive_failures >= AppConfig.CONSECUTIVE_FAILURE_THRESHOLD:
                    logger.error("複数回連続で抽出に失敗したため巡回を中断します — レート制限の可能性があります")
                    break
                
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