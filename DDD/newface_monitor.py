#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NewFace Monitor System (Refactored for MY_HOME_SYSTEM)
Target: https://petitpetit-dream.com/newface/

Description:
    Webサイトの新人紹介ページを定期巡回し、新規キャストの追加を検知してDiscordに通知する。
    MY_HOME_SYSTEMのエコシステムに統合されたバージョン。
"""

import os
import json
import time
import random
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Set, Dict, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# MY_HOME_SYSTEM Core Imports
try:
    # システム統合環境下でのインポート
    from core.logger import get_logger
except ImportError:
    # 単体テスト用フォールバック
    import logging
    logging.basicConfig(level=logging.INFO)
    def get_logger(name): return logging.getLogger(name)

# ==========================================
# Configuration & Constants
# ==========================================

class MonitorConfig:
    """モニタリング設定および定数管理クラス。"""

    # Target Settings
    TARGET_URL: str = 'https://petitpetit-dream.com/newface/'
    
    # Selectors (HTML構造に依存)
    SELECTOR_CONTAINER: str = 'ul.gallist li'
    SELECTOR_NAME: str = 'article h3 a'
    SELECTOR_LINK: str = 'article h3 a'
    SELECTOR_IMAGE: str = 'div.ph img:not(.list_today)'

    # File Paths
    # 基本設計書に基づき、データディレクトリは適切に解決する
    BASE_DIR: Path = Path(__file__).resolve().parent
    DATA_DIR: Path = BASE_DIR / 'data'
    DATA_FILE: Path = DATA_DIR / 'known_casts.json'

    # Network Settings
    USER_AGENT: str = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
    TIMEOUT: int = 30  # seconds
    RETRY_TOTAL: int = 3
    RETRY_BACKOFF: float = 1.0

    # Notification Settings
    # 機密情報はソースコードに含めず環境変数から取得 (Source: 382)
    DISCORD_WEBHOOK_URL: Optional[str] = os.getenv('DISCORD_WEBHOOK_URL')


# ロガーの初期化 (Source: 334)
logger = get_logger("newface_monitor")


# ==========================================
# Data Models
# ==========================================

@dataclass
class CastMember:
    """キャスト情報を表現するデータクラス。

    Attributes:
        id (str): ユニーク識別子（URLパス等から生成）。
        name (str): キャスト名。
        detail_url (str): 詳細プロフィールのURL。
        image_url (str): サムネイル画像のURL。
    """
    id: str
    name: str
    detail_url: str
    image_url: str

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CastMember):
            return False
        return self.id == other.id

    def to_dict(self) -> Dict[str, str]:
        """辞書形式に変換する。

        Returns:
            Dict[str, str]: JSONシリアライズ可能な辞書。
        """
        return asdict(self)


# ==========================================
# Services
# ==========================================

class DiscordNotifier:
    """Discordへの通知を担当するサービスクラス。"""

    def __init__(self, webhook_url: Optional[str]):
        """
        Args:
            webhook_url (Optional[str]): DiscordのWebhook URL。
        """
        self.webhook_url = webhook_url

    def notify(self, new_casts: List[CastMember]) -> None:
        """新規キャスト情報をDiscordに通知する。

        Args:
            new_casts (List[CastMember]): 通知対象の新規キャストリスト。
        """
        if not self.webhook_url or 'YOUR_DISCORD' in self.webhook_url:
            logger.warning("Discord Webhook URL is not configured. Skipping notification.")
            return

        for cast in new_casts:
            payload = {
                "username": "New Face Monitor",
                "embeds": [
                    {
                        "title": f"✨ 新人キャスト情報: {cast.name}",
                        "description": "新しいキャストが追加されました！",
                        "url": cast.detail_url,
                        "color": 16738740,  # Pinkish
                        "fields": [
                            {"name": "Name", "value": cast.name, "inline": True},
                            {"name": "Link", "value": f"[詳細ページへ]({cast.detail_url})", "inline": True}
                        ],
                        "thumbnail": {"url": cast.image_url} if cast.image_url else {}
                    }
                ]
            }
            try:
                # レート制限回避のための待機 (Source: 396)
                time.sleep(1)
                response = requests.post(self.webhook_url, json=payload, timeout=10)
                response.raise_for_status()
                logger.info(f"Notification sent successfully for: {cast.name}")
            except requests.RequestException as e:
                logger.error(f"Failed to send notification for {cast.name}: {e}")


class DataManager:
    """データの永続化と読み込みを担当するクラス。"""

    @staticmethod
    def load_known_casts() -> Set[CastMember]:
        """保存済みのキャストデータを読み込む。

        Returns:
            Set[CastMember]: 既知のキャストの集合。読み込み失敗時は空集合を返す。
        """
        if not MonitorConfig.DATA_FILE.exists():
            logger.info("No existing data found. Starting with empty state.")
            return set()

        try:
            with open(MonitorConfig.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {CastMember(**item) for item in data}
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load data from {MonitorConfig.DATA_FILE}: {e}")
            # データ破損時は安全側に倒して空集合（再通知される可能性があるがシステム停止よりマシ）
            return set()

    @staticmethod
    def save_known_casts(casts: Set[CastMember]) -> None:
        """キャストデータをJSONファイルに保存する。

        Args:
            casts (Set[CastMember]): 保存対象のキャスト集合。
        """
        try:
            MonitorConfig.DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = [c.to_dict() for c in casts]
            
            with open(MonitorConfig.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Saved {len(casts)} casts to {MonitorConfig.DATA_FILE}")
        except IOError as e:
            logger.error(f"Failed to save data: {e}", exc_info=True)


class WebMonitor:
    """Webサイトの監視とスクレイピングを統括するクラス。"""

    def __init__(self):
        """HTTPセッションの初期化を行う。"""
        self.session = self._create_robust_session()

    def _create_robust_session(self) -> requests.Session:
        """リトライロジックを組み込んだ堅牢なHTTPセッションを作成する (Source: 364)。

        Returns:
            requests.Session: 設定済みのセッションオブジェクト。
        """
        session = requests.Session()
        retries = Retry(
            total=MonitorConfig.RETRY_TOTAL,
            backoff_factor=MonitorConfig.RETRY_BACKOFF,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({'User-Agent': MonitorConfig.USER_AGENT})
        return session

    def fetch_current_casts(self) -> Set[CastMember]:
        """ターゲットURLから現在のキャスト一覧を取得する。

        Returns:
            Set[CastMember]: 現在掲載されているキャストの集合。

        Raises:
            requests.RequestException: 通信エラー時。
        """
        try:
            # Bot検知回避のためのランダム待機
            time.sleep(random.uniform(1.0, 3.0))

            logger.info(f"Fetching URL: {MonitorConfig.TARGET_URL}")
            response = self.session.get(MonitorConfig.TARGET_URL, timeout=MonitorConfig.TIMEOUT)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            return self._parse_html(soup)

        except requests.RequestException as e:
            # 呼び出し元でハンドリングするために再送出、ただしログは記録する
            logger.error(f"Network error during scraping: {e}")
            raise

    def _parse_html(self, soup: BeautifulSoup) -> Set[CastMember]:
        """HTMLスープからキャスト情報を抽出する。

        Args:
            soup (BeautifulSoup): 解析対象のHTML。

        Returns:
            Set[CastMember]: 抽出されたキャストの集合。
        """
        casts = set()
        containers = soup.select(MonitorConfig.SELECTOR_CONTAINER)

        if not containers:
            logger.warning(
                f"No elements found matching selector: {MonitorConfig.SELECTOR_CONTAINER}. "
                "Layout might have changed."
            )
            return casts

        for div in containers:
            try:
                # Name Extraction
                name_elem = div.select_one(MonitorConfig.SELECTOR_NAME)
                name = name_elem.get_text(strip=True) if name_elem else "Unknown"

                # Link & ID Extraction
                link_elem = div.select_one(MonitorConfig.SELECTOR_LINK)
                detail_url = ""
                cast_id = ""

                if link_elem and link_elem.get('href'):
                    href = link_elem.get('href')
                    detail_url = urljoin(MonitorConfig.TARGET_URL, href)
                    # パスからIDを生成 (例: /prof/123 -> 123)
                    clean_path = href.rstrip('/')
                    cast_id = os.path.basename(clean_path)

                if not cast_id:
                    # フォールバック: 名前をIDとする
                    cast_id = f"name_{name}"

                # Image Extraction
                img_elem = div.select_one(MonitorConfig.SELECTOR_IMAGE)
                image_url = ""
                if img_elem and img_elem.get('src'):
                    image_url = urljoin(MonitorConfig.TARGET_URL, img_elem.get('src'))

                cast = CastMember(
                    id=cast_id,
                    name=name,
                    detail_url=detail_url,
                    image_url=image_url
                )
                casts.add(cast)

            except Exception as e:
                # 個別のパースエラーで全体を止めない (Source: 302)
                logger.warning(f"Error parsing specific cast element: {e}")
                continue

        logger.info(f"Successfully parsed {len(casts)} casts.")
        return casts

    def close(self):
        """リソースを明示的に解放する (Source: 401)。"""
        if self.session:
            self.session.close()


# ==========================================
# Main Execution Flow
# ==========================================

def run_monitor():
    """モニタープロセスのメインロジック。"""
    logger.info("=== NewFace Monitor Started ===")
    
    monitor = WebMonitor()
    notifier = DiscordNotifier(MonitorConfig.DISCORD_WEBHOOK_URL)
    
    try:
        # 1. Load Data
        known_casts = DataManager.load_known_casts()

        # 2. Fetch Data
        try:
            current_casts = monitor.fetch_current_casts()
        except requests.RequestException:
            # ネットワークエラー時は処理を中断するが、プロセスは正常終了させる
            logger.error("Aborting monitor run due to network failure.")
            return

        if not current_casts:
            logger.warning("No casts found via scraping. Verify selectors or site availability.")
            return

        # 3. Detect Diff
        new_casts_set = current_casts - known_casts
        new_casts = list(new_casts_set)

        # 4. Notify & Update
        if new_casts:
            logger.info(f"Detected {len(new_casts)} new casts.")
            notifier.notify(new_casts)
            
            # Merge and Save
            updated_casts = known_casts.union(current_casts)
            DataManager.save_known_casts(updated_casts)
        else:
            logger.info("No new casts detected.")
            # 最新状態で上書き保存（メタデータ更新のため）
            DataManager.save_known_casts(current_casts)

    except Exception as e:
        # 想定外のエラー（Source: 388）
        logger.critical(f"Critical error in NewFace Monitor: {e}", exc_info=True)
    
    finally:
        # 終了時のリソース解放 (Source: 401)
        monitor.close()
        logger.info("=== NewFace Monitor Finished ===")


if __name__ == "__main__":
    run_monitor()