# MY_HOME_SYSTEM/menu_service.py
import os
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import sqlite3

# 自作モジュール
import common
import config

# ログ設定
logger = logging.getLogger('MenuService')

class MenuService:
    """
    晩御飯のメニュー提案支援サービス
    - 過去の履歴取得
    - 特別な日（給料日、ボーナス日）の判定
    """
    
    # 特別な日の定義
    PAYDAY_DAY: int = 25
    BONUS_DATES: List[Tuple[int, int]] = [(6, 10), (12, 10)] # (月, 日)

    def __init__(self) -> None:
        # DB初期化は common モジュール経由で行うため、パスの計算は不要になった
        self._init_db()

    def _init_db(self) -> None:
        """food_recordsテーブルの初期化（存在しない場合のみ作成）"""
        # common.get_db_cursor を使用してリソース管理を委譲
        with common.get_db_cursor(commit=True) as cursor:
            if cursor:
                try:
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS food_records (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            date TEXT,  -- YYYY-MM-DD
                            menu TEXT,
                            created_at TEXT
                        )
                    ''')
                except Exception as e:
                    logger.error(f"❌ DB初期化エラー: {e}")

    def get_recent_menus(self, days: int = 7) -> List[str]:
        """
        直近n日間の夕食履歴を取得する
        
        Args:
            days (int): 取得する過去の日数
            
        Returns:
            List[str]: "YYYY-MM-DD: メニュー名" のリスト
        """
        try:
            target_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            # 読み取り専用でカーソル取得
            with common.get_db_cursor() as cursor:
                if not cursor:
                    return []

                cursor.execute(
                    "SELECT date, menu FROM food_records WHERE date >= ? ORDER BY date DESC", 
                    (target_date,)
                )
                rows = cursor.fetchall()
                
            return [f"{r['date']}: {r['menu']}" for r in rows]
            
        except Exception as e:
            logger.error(f"❌ メニュー履歴取得エラー: {e}")
            return []

    def get_special_day_info(self) -> Optional[str]:
        """
        今日が給料日やボーナス日ならその情報を返す
        
        Returns:
            str: 特別な日の名称（例: "給料日💰"）、なければNone
        """
        today = datetime.now()
        month = today.month
        day = today.day
        
        special_messages: List[str] = []

        # 給料日判定
        if day == self.PAYDAY_DAY:
            special_messages.append("給料日💰")
        
        # ボーナス日判定
        if (month, day) in self.BONUS_DATES:
            special_messages.append("ボーナス日🎉")
            
        if special_messages:
            return " & ".join(special_messages)
        return None

if __name__ == "__main__":
    # 単体テスト用
    logging.basicConfig(level=logging.INFO)
    service = MenuService()
    
    print("🍽️ MenuService Test")
    print(f"特別な日: {service.get_special_day_info()}")
    print(f"直近の履歴: {service.get_recent_menus()}")