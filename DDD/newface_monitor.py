#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NewFace Monitor System
Target: https://petitpetit-dream.com/newface/
Author: Masahiro's AI Assistant (Senior Python Automation Engineer)
Date: 2026-01-21
Version: 1.0.0

Description:
    Webサイトの新人紹介ページを定期巡回し、新規キャストの追加を検知してDiscordに通知する。
    データはJSONで永続化し、差分のみを通知対象とする。
"""

import os
import json
import time
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Set, Optional, Dict
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ==========================================
# Configuration (ユーザー設定エリア)
# ==========================================
class Config:
    # ターゲットURL
    TARGET_URL = 'https://petitpetit-dream.com/newface/'
    
    # ---------------------------------------------------------
    # [重要] HTML解析用セレクタ設定
    # サイトのHTML構造に合わせて、以下のCSSセレクタを修正してください。
    # DevTools (F12) で該当要素を確認し、適切なクラス名を指定します。
    # ---------------------------------------------------------
    # キャスト1人分を囲むコンテナ要素 (例: 'div.cast_box', 'li.item')
    # ※以下は仮定の設定です。実際のサイト構造に合わせて変更が必要です。
    CAST_CONTAINER_SELECTOR = 'ul.gallist li'
    
    # コンテナ内の要素を取得するためのサブセレクタ
    SELECTOR_NAME = 'article h3 a'     # 名前が表示されている要素
    SELECTOR_LINK = 'article h3 a'             # 詳細ページへのリンク (hrefを持つ要素)
    SELECTOR_IMAGE = 'div.ph img:not(.list_today)'          # サムネイル画像 (srcを持つ要素)
    
    # ---------------------------------------------------------
    # システム設定
    # ---------------------------------------------------------
    # データ保存ディレクトリ (スクリプトからの相対パス)
    DATA_DIR = Path(__file__).parent / 'data'
    DATA_FILE = DATA_DIR / 'known_casts.json'
    LOG_FILE = Path(__file__).parent / 'monitor.log'
    
    # リクエスト設定
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    TIMEOUT = 30  # 秒
    
    # 通知設定 (環境変数または直接入力)
    # 実際の運用では os.getenv('DISCORD_WEBHOOK_URL') 推奨
    DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', 'YOUR_DISCORD_WEBHOOK_URL_HERE')

# ==========================================
# Logging Setup
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(Config.LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==========================================
# Data Models
# ==========================================
@dataclass
class CastMember:
    """キャスト情報を表すデータクラス。集合演算のためにHash化可能にする。"""
    id: str        # ユニークID (URLの一部などを利用)
    name: str
    detail_url: str
    image_url: str

    def __hash__(self):
        # IDのみで同一性を判定する（名前の微修正などは新規とみなさないため）
        return hash(self.id)

    def __eq__(self, other):
        if not isinstance(other, CastMember):
            return False
        return self.id == other.id

    def to_dict(self) -> Dict:
        return asdict(self)

# ==========================================
# Notification Service (Modular)
# ==========================================
class BaseNotifier(ABC):
    @abstractmethod
    def notify(self, new_casts: List[CastMember]):
        pass

class DiscordNotifier(BaseNotifier):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def notify(self, new_casts: List[CastMember]):
        if not self.webhook_url or 'YOUR_DISCORD' in self.webhook_url:
            logger.warning("Discord Webhook URL is not configured.")
            return

        for cast in new_casts:
            payload = {
                "username": "New Face Monitor",
                "embeds": [
                    {
                        "title": f"✨ 新人キャスト情報: {cast.name}",
                        "description": "新しいキャストが追加されました！",
                        "url": cast.detail_url,
                        "color": 16738740,  # Pinkish color
                        "fields": [
                            {"name": "Name", "value": cast.name, "inline": True},
                            {"name": "Link", "value": f"[詳細ページへ]({cast.detail_url})", "inline": True}
                        ],
                        "thumbnail": {"url": cast.image_url} if cast.image_url else {}
                    }
                ]
            }
            try:
                # Discordのレート制限に配慮して少し待機
                time.sleep(1)
                response = requests.post(self.webhook_url, json=payload)
                response.raise_for_status()
                logger.info(f"Notification sent for {cast.name}")
            except Exception as e:
                logger.error(f"Failed to send notification for {cast.name}: {e}")

# ==========================================
# Core Logic
# ==========================================
class DataManager:
    """データの永続化を担当"""
    @staticmethod
    def load_known_casts() -> Set[CastMember]:
        if not Config.DATA_FILE.exists():
            return set()
        
        try:
            with open(Config.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # JSONからオブジェクトに復元
                return {CastMember(**item) for item in data}
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load data: {e}. Starting with empty set.")
            return set()

    @staticmethod
    def save_known_casts(casts: Set[CastMember]):
        Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            # リストに変換して保存
            data = [c.to_dict() for c in casts]
            with open(Config.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved {len(casts)} casts to {Config.DATA_FILE}")
        except IOError as e:
            logger.error(f"Failed to save data: {e}")

class Scraper:
    """Webスクレイピングを担当"""
    def __init__(self):
        self.session = self._create_robust_session()

    def _create_robust_session(self) -> requests.Session:
        """リトライロジックを組み込んだセッションを作成"""
        session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1,  # 1s, 2s, 4s wait
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({'User-Agent': Config.USER_AGENT})
        return session

    def fetch_current_casts(self) -> Set[CastMember]:
        """サイトから現在のキャスト一覧を取得"""
        try:
            # ランダム待機 (Bot検知回避)
            time.sleep(random.uniform(1.0, 3.0))
            
            logger.info(f"Fetching {Config.TARGET_URL}...")
            response = self.session.get(Config.TARGET_URL, timeout=Config.TIMEOUT)
            response.raise_for_status()
            
            # HTML解析
            soup = BeautifulSoup(response.content, 'html.parser')
            return self._parse_html(soup)

        except Exception as e:
            logger.error(f"Scraping error: {e}")
            raise

    def _parse_html(self, soup: BeautifulSoup) -> Set[CastMember]:
        """HTMLからキャスト情報を抽出"""
        casts = set()
        containers = soup.select(Config.CAST_CONTAINER_SELECTOR)
        
        if not containers:
            logger.warning(f"No elements found matching selector: {Config.CAST_CONTAINER_SELECTOR}. Check CSS selectors.")
            return casts

        for div in containers:
            try:
                # 名前取得
                name_elem = div.select_one(Config.SELECTOR_NAME)
                name = name_elem.get_text(strip=True) if name_elem else "Unknown"

                # リンク取得
                link_elem = div.select_one(Config.SELECTOR_LINK)
                detail_url = ""
                cast_id = ""
                
                if link_elem and link_elem.get('href'):
                    href = link_elem.get('href')
                    detail_url = urljoin(Config.TARGET_URL, href)
                    # URLの末尾などをIDとして利用 (例: .../prof/123 -> 123)
                    # 末尾がスラッシュの場合の対策
                    clean_path = href.rstrip('/')
                    cast_id = os.path.basename(clean_path)
                
                # IDが取得できない場合は名前をIDとして代用（あまり推奨されないがフォールバック）
                if not cast_id:
                    cast_id = f"name_{name}"

                # 画像取得
                img_elem = div.select_one(Config.SELECTOR_IMAGE)
                image_url = ""
                if img_elem and img_elem.get('src'):
                    image_url = urljoin(Config.TARGET_URL, img_elem.get('src'))

                cast = CastMember(
                    id=cast_id,
                    name=name,
                    detail_url=detail_url,
                    image_url=image_url
                )
                casts.add(cast)

            except Exception as e:
                logger.error(f"Error parsing a cast element: {e}")
                continue
                
        logger.info(f"Found {len(casts)} casts on the page.")
        return casts

# ==========================================
# Main Controller
# ==========================================
def main():
    logger.info("=== NewFace Monitor Started ===")
    
    # コンポーネントの初期化
    data_manager = DataManager()
    scraper = Scraper()
    notifier = DiscordNotifier(Config.DISCORD_WEBHOOK_URL)

    try:
        # 1. 過去データのロード
        known_casts = data_manager.load_known_casts()
        
        # 2. 最新データの取得
        current_casts = scraper.fetch_current_casts()
        
        if not current_casts:
            logger.warning("No casts found via scraping. Skipping update.")
            return

        # 3. 差分検知 (現在の集合 - 過去の集合 = 新規)
        new_casts_set = current_casts - known_casts
        
        # IDベースで差分を取るが、通知用にリスト化してソート（オプション）
        new_casts = list(new_casts_set)

        if new_casts:
            logger.info(f"Detected {len(new_casts)} new casts!")
            
            # 4. 通知
            # 初回起動時（known_castsが空）に大量通知が行くのを防ぐ場合、
            # 以下のコメントアウトを外して調整してください。
            # if len(known_casts) > 0:
            notifier.notify(new_casts)
            # else:
            #    logger.info("First run initialized. Skipping notifications.")
            
            # 5. データの永続化 (既存データ + 新規データ)
            # 削除されたキャストを残すかどうかはポリシー次第ですが、
            # ここでは「知っているキャスト」リストを更新する（現在掲載されているもの全てで上書き）形にします。
            # ※履歴を完全に残したい場合は known_casts.union(current_casts) にしてください。
            updated_casts = known_casts.union(current_casts) 
            data_manager.save_known_casts(updated_casts)
            
        else:
            logger.info("No new casts detected.")
            # 念のため最新状態で更新（名前変更などを反映させたい場合）
            # 今回はID判定なので、そのまま維持でも良いが、最新のスナップショットを保存
            data_manager.save_known_casts(current_casts)

    except Exception as e:
        logger.error(f"Critical error in main loop: {e}", exc_info=True)
    
    logger.info("=== Monitor Finished ===")

if __name__ == "__main__":
    main()