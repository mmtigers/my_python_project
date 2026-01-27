# MY_HOME_SYSTEM/monitors/suumo_monitor.py
import sys
import os
import time
import requests
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

# 親ディレクトリのパスを追加して common, config をインポート可能にする
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import common

# ロガー設定 (設計書 8.1準拠)
logger = common.setup_logging("suumo_monitor")

class SuumoMonitor:
    """
    SUUMOの新着物件情報を監視し、Discordへ通知するクラス。
    
    Attributes:
        target_url (Optional[str]): 監視対象のSUUMO検索結果URL。
        max_budget (int): 通知対象とする家賃の上限額。
        webhook_url (Optional[str]): 通知先のDiscord Webhook URL。
    """

    def __init__(self) -> None:
        """初期化処理。設定読み込みとヘッダー定義を行う。"""
        self.target_url: Optional[str] = config.SUUMO_SEARCH_URL
        self.max_budget: int = config.SUUMO_MAX_BUDGET
        self.webhook_url: Optional[str] = config.DISCORD_WEBHOOK_NOTIFY
        
        # スクレイピング用ヘッダー
        self.headers: Dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
        }

    def _parse_price(self, price_str: str) -> int:
        """
        金額文字列を整数に変換する。

        Args:
            price_str (str): 金額文字列（例: '6.5万円', '3000円', '-'）

        Returns:
            int: 円単位の整数値。変換不能な場合は0を返す。
        """
        try:
            if "万円" in price_str:
                val = float(price_str.replace("万円", ""))
                return int(val * 10000)
            elif "円" in price_str:
                return int(re.sub(r'[^0-9]', '', price_str))
            elif price_str == "-":
                return 0
            return 0
        except Exception:
            return 0

    def fetch_listings(self) -> List[Dict[str, str | int]]:
        """
        SUUMOから物件情報を取得する。

        サーバー負荷軽減のため、リクエスト前にWaitを入れる。

        Returns:
            List[Dict[str, str | int]]: 物件情報のリスト。エラー時は空リスト。
        """
        if not self.target_url or "suumo.jp" not in self.target_url:
            logger.warning("⚠️ SUUMOのURLが未設定または不正です。.envを確認してください。")
            return []

        logger.info(f"📡 SUUMO検索開始: 予算 {self.max_budget}円以下")
        
        try:
            # 設計書 2.0 (Scraping Manners) - Wait処理
            time.sleep(2)
            
            # 設計書 9.3 (Fail-Safe) - タイムアウト設定
            response = requests.get(self.target_url, headers=self.headers, timeout=10)
            response.encoding = response.apparent_encoding
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP Error: {response.status_code}")
                return []

            soup = BeautifulSoup(response.text, 'html.parser')
            cassettes = soup.find_all("div", class_="cassetteitem")
            
            listings: List[Dict[str, str | int]] = []
            
            for cassette in cassettes:
                # 物件名
                title_elem = cassette.find("div", class_="cassetteitem_content-title")
                title = title_elem.text.strip() if title_elem else "不明な物件"
                
                # 住所
                address_elem = cassette.find("li", class_="cassetteitem_detail-col1")
                address = address_elem.text.strip() if address_elem else "住所不明"

                # サムネイル画像
                img_tag = cassette.find("img", class_="js-noContextMenu")
                thumb_url = img_tag.get("rel") if img_tag and img_tag.get("rel") else ""
                
                # 部屋ごとのリスト
                items = cassette.find_all("tbody")
                
                for item in items:
                    rent_elem = item.find("span", class_="cassetteitem_price--rent")
                    admin_elem = item.find("span", class_="cassetteitem_price--administration")
                    
                    rent = self._parse_price(rent_elem.text.strip()) if rent_elem else 0
                    admin = self._parse_price(admin_elem.text.strip()) if admin_elem else 0
                    total_price = rent + admin
                    
                    if total_price > self.max_budget:
                        continue

                    madori_elem = item.find("span", class_="cassetteitem_madori")
                    madori = madori_elem.text.strip() if madori_elem else "-"
                    
                    # リンク取得 (JS除外ロジック)
                    link_elem = item.find("a", class_="js-cassette_link_href")
                    if not link_elem:
                        link_elem = item.find("a", href=lambda h: h and "/chintai/" in h and "javascript" not in h)

                    if link_elem:
                        relative_url = link_elem.get('href', '')
                        link = "https://suumo.jp" + relative_url
                        property_id = relative_url.split('?')[0]
                    else:
                        continue

                    listings.append({
                        "id": property_id,
                        "title": title,
                        "address": address,
                        "price": total_price,
                        "rent": rent,
                        "admin": admin,
                        "madori": madori,
                        "url": link,
                        "thumb": thumb_url
                    })
            
            logger.info(f"🔍 取得物件数: {len(listings)}件")
            return listings

        except Exception as e:
            logger.error(f"🔥 スクレイピング中にエラー発生: {e}")
            return []

    def filter_new_listings(self, listings: List[Dict[str, str | int]]) -> List[Dict[str, str | int]]:
        """
        DBと照合して新着物件のみを抽出・保存する。

        Args:
            listings (List[Dict]): 取得した全物件リスト

        Returns:
            List[Dict]: 新着物件のリスト
        """
        if not listings:
            return []
            
        new_listings: List[Dict[str, str | int]] = []
        
        # 設計書 7.1 - データベース操作の制限（明示的カラム指定）遵守
        with common.get_db_cursor(commit=True) as cur:
            for item in listings:
                try:
                    cur.execute("SELECT id FROM suumo_records WHERE property_id = ?", (item['id'],))
                    if cur.fetchone() is None:
                        cur.execute("""
                            INSERT INTO suumo_records (property_id, title, rent_price, url, address)
                            VALUES (?, ?, ?, ?, ?)
                        """, (item['id'], item['title'], item['price'], item['url'], item['address']))
                        
                        new_listings.append(item)
                except Exception as e:
                    logger.error(f"DB Error for {item['title']}: {e}")
                    
        return new_listings

    def notify_discord(self, listings: List[Dict[str, str | int]]) -> None:
        """
        新着物件をDiscordへ通知する。

        Args:
            listings (List[Dict]): 通知対象の物件リスト
        """
        if not listings or not self.webhook_url:
            return

        logger.info(f"📢 新着物件 {len(listings)}件を通知します")
        
        for item in listings:
            embed = {
                "title": f"🏠 新着: {item['title']}",
                "description": (
                    f"**賃料**: {item['price']:,}円 (管理費込)\n"
                    f"**住所**: {item['address']}\n"
                    f"**間取り**: {item['madori']}\n"
                    f"[物件詳細を見る]({item['url']})"
                ),
                "color": 0x1E90FF,
                "thumbnail": {"url": item['thumb']} if item['thumb'] else {}
            }
            
            payload = {
                "username": "SUUMO Hunter",
                "embeds": [embed]
            }
            
            try:
                # 設計書 9.3 - Fail-Safe & Retry (簡易的なSleepによるWait)
                requests.post(self.webhook_url, json=payload, timeout=5)
                time.sleep(1)
            except Exception as e:
                logger.error(f"Discord送信エラー: {e}")

    def run(self) -> None:
        """メイン実行プロセス。"""
        listings = self.fetch_listings()
        new_items = self.filter_new_listings(listings)
        
        if new_items:
            self.notify_discord(new_items)
            logger.info("✅ 処理完了: 新着あり")
        else:
            logger.info("💤 新着なし")

if __name__ == "__main__":
    monitor = SuumoMonitor()
    monitor.run()