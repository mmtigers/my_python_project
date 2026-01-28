# MY_HOME_SYSTEM/monitors/suumo_monitor.py
import os
import sys
import requests
import traceback
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

# プロジェクトルートへのパス解決
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.logger import setup_logging
from core.database import save_log_generic, get_db_cursor
from core.utils import get_now_iso
from services.notification_service import send_push

# Gemini API ライブラリ (設定されている場合のみ有効化)
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

# ロガー設定
logger = setup_logging("suumo_monitor")

class SuumoMonitor:
    """
    SUUMOの新着物件を監視し、AIによる評価を添えて通知するクラス。
    """

    def __init__(self) -> None:
        self.search_url: Optional[str] = config.SUUMO_SEARCH_URL
        self.line_user_id: Optional[str] = config.LINE_USER_ID
        self.table_name: str = "property_logs" # 物件監視用テーブル
        
        # Gemini設定
        if HAS_GEMINI and config.GEMINI_API_KEY:
            genai.configure(api_key=config.GEMINI_API_KEY)
            self.model = genai.GenerativeModel('gemini-1.5-flash')
            self.ai_enabled = True
        else:
            self.ai_enabled = False
            logger.warning("⚠️ Gemini API is disabled (Key missing or library not installed).")

    def fetch_properties(self) -> List[Dict[str, Any]]:
        """SUUMOをスクレイピングして物件リストを取得する。"""
        if not self.search_url:
            logger.error("❌ SUUMO_SEARCH_URL is not configured.")
            return []

        headers: Dict[str, str] = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        properties: List[Dict[str, Any]] = []

        try:
            res = requests.get(self.search_url, headers=headers, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')

            # 物件カードを抽出
            items = soup.select('.cassetteitem')
            for item in items:
                try:
                    name = item.select_one('.cassetteitem_content-title').get_text(strip=True)
                    # 最初のプラン/部屋情報を取得
                    row = item.select_one('.cassetteitem_inner .js-cassette_link')
                    if not row: continue
                    
                    price = item.select_one('.cassetteitem_price--rent').get_text(strip=True)
                    layout = item.select_one('.cassetteitem_menseki').get_text(strip=True)
                    link = "https://suumo.jp" + row.select_one('a')['href']
                    property_id = link.split('bc=')[-1].split('/')[0] if 'bc=' in link else link

                    properties.append({
                        "id": property_id,
                        "name": name,
                        "price": price,
                        "layout": layout,
                        "link": link
                    })
                except Exception:
                    continue

            logger.info(f"🔍 Fetched {len(properties)} properties from SUUMO.")
            return properties

        except Exception as e:
            logger.error(f"❌ Scraping failed: {e}")
            return []

    def filter_new_properties(self, properties: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """既知の物件を除外し、新着のみを返す。"""
        new_items: List[Dict[str, Any]] = []
        
        with get_db_cursor() as cur:
            if not cur: return properties
            
            for p in properties:
                # 過去に記録があるかチェック
                cur.execute(f"SELECT id FROM {self.table_name} WHERE device_id = ?", (p['id'],))
                if not cur.fetchone():
                    new_items.append(p)
        
        return new_items

    def analyze_with_ai(self, prop: Dict[str, Any]) -> str:
        """Gemini APIを使用して物件の魅力を分析する。"""
        if not self.ai_enabled:
            return "（AI評価スキップ）"

        prompt = (
            f"以下の不動産物件について、35歳・共働き・2人の子供（5歳, 2歳）がいる家庭の視点で、"
            f"「買い」か「見送り」かを100文字程度で論理的に評価してください。\n"
            f"物件名: {prop['name']}\n価格: {prop['price']}\n間取り: {prop['layout']}"
        )

        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            logger.warning(f"⚠️ Gemini Analysis failed: {e}")
            return "（AI評価エラー）"

    def run(self) -> None:
        """メイン実行ルーチン。"""
        logger.info("🚀 SUUMO Monitor started.")
        
        # 1. 取得
        all_props = self.fetch_properties()
        if not all_props: return

        # 2. 新着判定
        new_props = self.filter_new_properties(all_props)
        if not new_props:
            logger.info("✅ No new properties found.")
            return

        # 3. 通知と記録
        for p in new_props:
            logger.info(f"✨ New Property Found: {p['name']}")
            
            # AI評価
            ai_comment = self.analyze_with_ai(p)
            
            # 通知メッセージ構築
            msg = (
                f"🏠【SUUMO新着物件】\n"
                f"名称: {p['name']}\n"
                f"賃料: {p['price']}\n"
                f"広さ: {p['layout']}\n"
                f"URL: {p['link']}\n\n"
                f"🤖 AI評価:\n{ai_comment}"
            )

            # DB保存
            save_log_generic(
                self.table_name,
                ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
                (get_now_iso(), p['name'], p['id'], "Property", ai_comment[:100])
            )

            # 通知送信
            if self.line_user_id:
                send_push(self.line_user_id, [{"type": "text", "text": msg}], target="discord")

        logger.info(f"🏁 Processed {len(new_props)} new properties.")

if __name__ == "__main__":
    monitor = SuumoMonitor()
    monitor.run()