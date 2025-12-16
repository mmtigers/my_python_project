import sqlite3
import os
import logging
from datetime import datetime, timedelta
from typing import List, Optional

# ログ設定
logger = logging.getLogger('MenuService')

class MenuService:
    """
    晩御飯のメニュー提案支援サービス
    - 過去の履歴取得
    - 特別な日（給料日、ボーナス日）の判定
    """
    
    DB_NAME = "home_system.db"
    
    # 特別な日の定義
    PAYDAY_DAY = 25
    BONUS_DATES = [(6, 10), (12, 10)] # (月, 日)

    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self._init_db()

    def _init_db(self):
        """food_recordsテーブルの初期化（存在しない場合のみ作成）"""
        db_path = os.path.join(self.base_dir, self.DB_NAME)
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS food_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT,  -- YYYY-MM-DD
                        menu TEXT,
                        created_at TEXT
                    )
                ''')
                conn.commit()
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
        db_path = os.path.join(self.base_dir, self.DB_NAME)
        try:
            target_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT date, menu FROM food_records WHERE date >= ? ORDER BY date DESC", 
                    (target_date,)
                )
                rows = cursor.fetchall()
                
            return [f"{r[0]}: {r[1]}" for r in rows]
            
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
        
        special_messages = []

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