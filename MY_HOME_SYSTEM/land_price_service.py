# MY_HOME_SYSTEM/land_price_service.py
import requests
import sqlite3
import logging
import time
import re
import sys
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 自作モジュール
import config
import common

# ロガー設定
logger = common.setup_logging("land_price_service")

# 処理中断用の内部例外
class AbortProcessing(Exception):
    pass

class LandPriceService:
    """
    国土交通省「不動産情報ライブラリ」APIを利用して、
    指定エリアの土地価格情報を収集・記録するクラス
    (2025年 新API対応版)
    """
    
    # 新APIエンドポイント (XIT001: 不動産取引価格情報)
    API_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external/XIT001"
    TABLE_NAME = "land_price_records"
    MAX_CONSECUTIVE_ERRORS = 3

    def __init__(self):
        self.session = self._create_retry_session()
        self.consecutive_error_count = 0
        
        # APIキーのチェック
        if not getattr(config, "REINFOLIB_API_KEY", None):
            logger.error("❌ REINFOLIB_API_KEY が config.py に設定されていません。")
            logger.error("👉 https://www.reinfolib.mlit.go.jp/api/request/ からキーを取得してください。")
            sys.exit(1)

    def _create_retry_session(self, retries=3, backoff_factor=1.0):
        session = requests.Session()
        retry = Retry(
            total=retries, backoff_factor=backoff_factor,
            status_forcelist=[500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session

    def fetch_and_save(self):
        logger.info("🚀 土地価格情報の取得を開始します (新API)...")
        
        targets = self._get_target_periods() # (year, quarter) のリスト
        total_new = 0
        new_items_details = []

        try:
            for target_area in config.LAND_PRICE_TARGETS:
                # configのcity_code (例:28207) から 都道府県コード(28) を抽出
                city_code = target_area["city_code"]
                area_code = city_code[:2] 
                
                logger.info(f"🔎 {target_area['city_name']} のデータを検索中...")

                for year, quarter in targets:
                    if self.consecutive_error_count >= self.MAX_CONSECUTIVE_ERRORS:
                        raise AbortProcessing("連続エラーのため中断します")

                    data = self._call_api(year, quarter, area_code, city_code)
                    if not data:
                        continue

                    for item in data:
                        # 1. 町名フィルタ
                        district_name = item.get("DistrictName", "")
                        if not any(d in district_name for d in target_area["districts"]):
                            continue

                        # 2. 丁目フィルタ
                        target_chome = target_area.get("filter_chome")
                        if not self._check_chome_filter(district_name, target_chome):
                             continue

                        # 3. 保存
                        if self._save_record(item, target_area["city_name"]):
                            total_new += 1
                            price_man = int(item.get("TradePrice", 0)) // 10000
                            type_name = item.get("Type", "土地")
                            desc = f"📍 {district_name} ({type_name})\n   💰 {price_man}万円 ({item.get('Area')}㎡)"
                            new_items_details.append(desc)
                    
                    time.sleep(1) # API制限考慮

            if total_new > 0:
                self._notify_user(total_new, new_items_details)
            else:
                logger.info("✨ 新しい取引データはありませんでした。")

        except AbortProcessing as e:
            logger.error(f"🚨 {e}")
        finally:
            self.session.close()

    def _get_target_periods(self):
        """検索対象の期間 (年, 四半期) を生成"""
        now = datetime.now()
        year = now.year
        q = (now.month - 1) // 3 + 1
        
        periods = []
        # 直近3四半期分
        for _ in range(3):
            periods.append((year, q))
            q -= 1
            if q < 1:
                q = 4
                year -= 1
        return periods

    def _call_api(self, year, quarter, area_code, city_code):
        headers = {
            "Ocp-Apim-Subscription-Key": config.REINFOLIB_API_KEY
        }
        params = {
            "year": year,
            "quarter": quarter,
            "area": area_code,  # 都道府県コード
            "city": city_code,  # 市区町村コード
            "priceClassification": "01" # 01:取引価格情報
        }
        
        try:
            res = self.session.get(self.API_URL, headers=headers, params=params, timeout=10)
            res.raise_for_status()
            self.consecutive_error_count = 0
            
            json_data = res.json()
            if json_data.get("status") == "OK":
                return json_data.get("data", [])
            
        except Exception as e:
            self.consecutive_error_count += 1
            logger.warning(f"APIエラー: {e}")
            
        return []

    def _check_chome_filter(self, district_name, target_chome_list):
        if not target_chome_list: return True
        # 漢数字変換
        kanji_map = str.maketrans("１２３４５６７８９", "123456789")
        normalized = district_name.translate(kanji_map)
        match = re.search(r'(\d+)丁目', normalized)
        if match:
            return int(match.group(1)) in target_chome_list
        # 丁目が文字列にないがフィルタがある場合、念のため通す（「西畑」単体など）
        return True

    def _save_record(self, item, city_name):
        try:
            # ユニークID作成 (新APIにはIDがない場合があるため複合キーで)
            trade_id = f"{item.get('CityCode')}_{item.get('DistrictName')}_{item.get('TradePrice')}_{item.get('Period')}"
            
            with common.get_db_cursor(commit=True) as cur:
                cur.execute(f"SELECT id FROM {self.TABLE_NAME} WHERE trade_id=?", (trade_id,))
                if cur.fetchone(): return False

                vals = (
                    trade_id, item.get("Prefecture"), city_name, item.get("DistrictName"),
                    item.get("Type"), int(item.get("TradePrice", 0)), int(item.get("Area", 0)),
                    int(item.get("UnitPrice", 0)) if item.get("UnitPrice") else 0,
                    item.get("Period"), common.get_now_iso()
                )
                sql = f"""INSERT INTO {self.TABLE_NAME} 
                (trade_id, prefecture, city, district, type, price, area_m2, price_per_m2, transaction_period, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
                cur.execute(sql, vals)
                logger.info(f"💾 新規記録: {item.get('DistrictName')}")
                return True
        except Exception as e:
            logger.error(f"DB保存エラー: {e}")
            return False

    def _notify_user(self, count, details):
        body = "\n".join(details[:5])
        if len(details) > 5: body += f"\n...他 {len(details)-5} 件"
        msg = f"🏘️ **土地価格情報 (新着)**\n指定エリアで {count} 件の取引情報が見つかりました。\n\n{body}"
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel="report")

if __name__ == "__main__":
    service = LandPriceService()
    service.fetch_and_save()