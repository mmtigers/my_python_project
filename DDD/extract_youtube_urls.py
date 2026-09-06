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
from dataclasses import dataclass
from typing import List, Optional, Set, Iterator, Dict, Any

import yt_dlp

from file_utils import sanitize_filename as _shared_sanitize_filename
from file_utils import resolve_my_home_system_root

# ==========================================
# 0. 環境設定 & ロギング (Unified Logging)
# ==========================================
# プロジェクトルートへのパス解決 (DDD/ から MY_HOME_SYSTEM/core/ を参照するため)。
# 品質: プロジェクトルート解決をfile_utils.resolve_my_home_system_rootへ集約
# (以前はnewface_monitor.pyと同じ、固定の兄弟ディレクトリ前提のみの単純な方式を
# 個別に実装していた)。core/ は develop/MY_HOME_SYSTEM/core に実在する
# (develop/core ではない)ため、DDDの単なる親ディレクトリではImportErrorになり、
# 常にローカルフォールバック用スタブへ落ちてしまう点に変わりはない。
CURRENT_DIR = Path(__file__).resolve().parent  # ~/develop/DDD
PROJECT_ROOT = resolve_my_home_system_root(CURRENT_DIR)  # ~/develop/MY_HOME_SYSTEM
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

try:
    from core.logger import get_logger
    from core.nas_utils import get_managed_target_directory
    logger = get_logger(__name__)
except ImportError as e:
    # 開発環境や単体実行時のフォールバック
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("UrlExtractor")
    # #463: core.*のインポート失敗はNASではなくローカルディスクへの書き込みに
    # 切り替わることを意味する。本番環境でMY_HOME_SYSTEMへのパス解決が崩れる等の
    # 変更があった場合に気づけるよう、無警告で切り替わらないようにする。
    logger.warning(
        f"⚠️ core.*のインポートに失敗したため開発用フォールバックへ切り替わりました "
        f"(NASではなくローカルディスクへ書き込みます): {e}"
    )

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

    # #227: 1チャンネル内部で発行される /videos -> /playlists -> 各プレイリスト
    # という複数リクエストは間隔なしで連射されていた。これらの内部リクエスト間に
    # ジッター待機を挟む。
    INTRA_CHANNEL_SLEEP_RANGE: tuple = (1.0, 3.0)
    
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

    def __init__(self) -> None:
        # #227: extract_iter内部(1チャンネルにつき/videos・/playlists・各プレイリスト
        # という複数リクエスト)で発生した失敗件数。extract_iterが1件でも結果をyield
        # すれば呼び出し元は「成功」扱いにしていたため、大量のプレイリストが失敗しても
        # サーキットブレーカーの連続失敗カウントが常に0にリセットされていた
        # (#413: この呼び出し元だった SubscriptionManager は後にデッドコードとして
        # 削除されたが、last_extract_internal_failures自体はextract_iterの内部失敗を
        # 呼び出し元へ伝える汎用的な仕組みとして残す)。extract_iterの呼び出しごとに
        # リセットし、呼び出し元が参照できるようにする。
        self.last_extract_internal_failures: int = 0

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
        self.last_extract_internal_failures = 0

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

            # #227: /videos と /playlists は同一チャンネルに対する連続リクエストで
            # あり、間隔を空けずに連射するとレート制限/Bot検知を誘発しうる。
            time.sleep(random.uniform(*AppConfig.INTRA_CHANNEL_SLEEP_RANGE))

            # Phase 2: Playlists
            try:
                # 呼び出し間の状態汚染を避けるためコピーを渡す（理由は上のコメント参照）
                with yt_dlp.YoutubeDL(dict(AppConfig.YDL_OPTS)) as ydl:
                    pl_tab = ydl.extract_info(f"{base_url}/playlists", download=False)
                    if pl_tab and 'entries' in pl_tab:
                        playlists = list(pl_tab['entries'])
                        logger.info(f"📂 {len(playlists)} 個のプレイリストが見つかりました。")
                        for i, pl in enumerate(playlists):
                            if not pl:
                                continue
                            pl_url = pl.get('url')
                            pl_title = pl.get('title', 'Unknown Playlist')
                            if pl_url:
                                if i > 0:
                                    # #227: 検出した各プレイリストへの逐次リクエストが
                                    # sleep無しで連続発行されていたため、ここにも
                                    # ジッター待機を挟む。
                                    time.sleep(random.uniform(*AppConfig.INTRA_CHANNEL_SLEEP_RANGE))
                                res = self._extract_single_list(pl_url, force_title=pl_title)
                                if res:
                                    res.is_playlist = True
                                    yield res
                                else:
                                    # #227: 個々のプレイリスト取得失敗を呼び出し元の
                                    # サーキットブレーカーが検知できるよう記録する。
                                    self.last_extract_internal_failures += 1
            except Exception:
                logger.error("❌ プレイリスト一覧の取得に失敗しました", exc_info=True)
                self.last_extract_internal_failures += 1
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

    def save(self, result: ExtractionResult, base_dir: Optional[Path] = None) -> bool:
        """抽出結果をテキストファイルに保存する。

        Args:
            result (ExtractionResult): 保存対象の抽出データ。
            base_dir (Optional[Path]): 保存先のベースディレクトリ。省略時は
                AppConfig.get_output_base_dir()を呼び出して取得する(#243修正前の
                挙動)。get_output_base_dir()はNASマウント確認・自己修復・障害通知を
                伴う重い処理のため、複数件のExtractionResultを保存する呼び出し元は
                同一処理内で1回だけ取得した値をここへ渡して使い回すこと
                (UrlExtractorApp.run()参照)。

        Returns:
            bool: 保存に成功した場合は True。
        """
        # #243: 呼び出し元から渡されなかった場合のみ遅延評価でディレクトリを取得する。
        # 以前は常にここでget_output_base_dir()を呼んでいたため、呼び出し元
        # (UrlExtractorApp.run())が1回に抑えていたつもりの重い処理(NASマウント確認・
        # 自己修復・障害通知)が、保存件数分だけ再評価され、NAS瞬断時に再マウント試行・
        # 通知が多重発生していた。
        if base_dir is None:
            base_dir = AppConfig.get_output_base_dir()
        target_dir = base_dir / AppConfig.SUB_DIR_NAME
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
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

        # D-L10: 以前はoutput_path.open("w", ...)で直接上書きしていたため、
        # 書き込み中(NAS瞬断等)にプロセスが中断すると、同名ファイルが既に
        # 存在するケース(上記の重複)では中身が空/一部だけのファイルで
        # 上書きされたまま残ってしまいうった。newface_monitor.py/
        # batch_download_discord.pyの他の永続化と同じ「.tmpへ書き込み→
        # replace」のアトミックパターンに揃える。
        tmp_path = output_path.with_suffix(output_path.suffix + '.tmp')
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                for url in result.urls:
                    f.write(url + "\n")
            tmp_path.replace(output_path)
            logger.info(f"✅ 保存完了: {filename} ({len(result.urls)} 件)")
            return True
        except IOError:
            logger.error(f"❌ ファイル書き込みエラー: {output_path}", exc_info=True)
            # 書き込み失敗時の.tmpファイル残置を防ぐ(best-effort)。
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

# #413 (D-L11): 以前ここにあった SubscriptionManager クラス(定期巡回/サブスク
# リプション機能。youtube_subscriptions テーブルをSQLite DBで管理し、--cron
# 実行時に登録済みチャンネルを順次抽出していた)は削除した。youtube_subscriptions
# テーブルへのINSERT/UPDATEを行うコードがリポジトリ内のどこにも存在せず(仕様書の
# 旧版でも同じ結論)、crontabにも未登録の、事実上のデッド機能だったため
# (オーナー判断: --cronは削除)。また同機能が使うDBパス
# (/mnt/nas/home_system/youtube_extractor/home_system.db)は、MY_HOME_SYSTEM
# 本体の home_system.db とは別ファイルであるにもかかわらず同名という紛らわしい
# 設計でもあった。

# ==========================================
# 4. アプリケーション本体
# ==========================================
class UrlExtractorApp:
    """アプリケーションのエントリーポイントクラス。"""

    def __init__(self):
        self.extractor = YouTubeExtractor()
        self.file_manager = FileManager()

    def run(self) -> None:
        """コマンドライン引数を解析し、メイン処理を実行する。"""
        logger.info("=== YouTube URL Extractor (v3.1.0) Started ===")

        parser = argparse.ArgumentParser(description="Extract YouTube URLs from channels or playlists.")
        parser.add_argument("url", nargs="?", help="Target YouTube URL")
        args = parser.parse_args()

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
            # #243: 1本のURLから複数のExtractionResultが得られる場合に
            # get_output_base_dir()が結果ごとに再評価されないよう、
            # 1回だけ取得した値をsave()へ渡して使い回す。
            base_dir = AppConfig.get_output_base_dir()
            # イテレータを回して処理
            for result in self.extractor.extract_iter(target_url):
                if self.file_manager.save(result, base_dir=base_dir):
                    total_files += 1
            logger.info(f"🎉 処理完了: 計 {total_files} ファイルを作成しました")
        else:
            logger.info("URLが指定されなかったため終了します")

if __name__ == "__main__":
    app = UrlExtractorApp()
    app.run()