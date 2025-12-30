import unittest
import sys
import os
import sqlite3
import shutil
from datetime import datetime

# プロジェクトルートにパスを通す
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import common
import init_unified_db
from routers.quest_router import QuestService, MasterUser, MasterQuest, MasterReward

class TestQuestService(unittest.TestCase):
    
    def setUp(self):
        """各テストケースの実行前に呼ばれる"""
        # --- 修正: テスト中はDiscord通知を無効化 ---
        self.original_webhook = config.DISCORD_WEBHOOK_ERROR
        config.DISCORD_WEBHOOK_ERROR = None  # 一時的にNoneにする
        
        # ロガーのセットアップ
        common.setup_logging("test_quest")
        
        # テスト用DBのセットアップ
        self.test_db_file = "test_home_system.db"
        config.SQLITE_DB_PATH = self.test_db_file
        
        # DB初期化（テーブル作成）
        init_unified_db.init_db()
        
        self.service = QuestService()
        
        # テストデータのシード
        self._seed_master_data()

    def tearDown(self):
        """各テストケースの終了後に呼ばれる"""
        # --- 修正: Discord通知設定を復元 ---
        config.DISCORD_WEBHOOK_ERROR = self.original_webhook
        
        # DBファイルの削除
        if os.path.exists(self.test_db_file):
            try:
                os.remove(self.test_db_file)
            except PermissionError:
                pass # Windows等でファイルロックが残る場合の安全策

    def _seed_master_data(self):
        """テストに必要な最低限のマスタデータを投入"""
        with common.get_db_cursor(commit=True) as cur:
            # ユーザー
            cur.execute("""
                INSERT INTO quest_users (user_id, name, job_class, level, exp, gold)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("user1", "TestPlayer", "Novice", 1, 0, 100))
            
            # クエスト
            cur.execute("""
                INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain)
                VALUES (?, ?, ?, ?, ?)
            """, (101, "Test Quest", "daily", 50, 20))
            
            # 報酬
            cur.execute("""
                INSERT INTO reward_master (reward_id, title, cost_gold)
                VALUES (?, ?, ?)
            """, (201, "Test Reward", 50))

    # --- テストケース ---

    def test_calculate_next_level_exp(self):
        """経験値計算ロジックの検証"""
        self.assertEqual(self.service.calculate_next_level_exp(1), 100)
        self.assertEqual(self.service.calculate_next_level_exp(2), 120)

    def test_complete_quest_basic(self):
        """クエスト完了の基本動作検証"""
        result = self.service.process_complete_quest("user1", 101)
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["earnedExp"], 50)
        self.assertEqual(result["earnedGold"], 20)
        self.assertFalse(result["leveledUp"])
        
        with common.get_db_cursor() as cur:
            user = cur.execute("SELECT * FROM quest_users WHERE user_id='user1'").fetchone()
            self.assertEqual(user["exp"], 50)
            self.assertEqual(user["gold"], 120)

    def test_level_up_logic(self):
        """レベルアップ処理の検証"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE quest_users SET exp=90 WHERE user_id='user1'")
        
        result = self.service.process_complete_quest("user1", 101)
        
        self.assertTrue(result["leveledUp"])
        self.assertEqual(result["newLevel"], 2)
        
        with common.get_db_cursor() as cur:
            user = cur.execute("SELECT * FROM quest_users WHERE user_id='user1'").fetchone()
            self.assertEqual(user["level"], 2)
            self.assertEqual(user["exp"], 40)

    def test_purchase_reward_success(self):
        """報酬購入（成功）"""
        result = self.service.process_purchase_reward("user1", 201)
        
        self.assertEqual(result["status"], "purchased")
        self.assertEqual(result["newGold"], 50)

    def test_purchase_reward_insufficient_gold(self):
        """報酬購入（資金不足エラー）"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE quest_users SET gold=10 WHERE user_id='user1'")
        
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            self.service.process_purchase_reward("user1", 201)
        
        self.assertEqual(cm.exception.status_code, 400)

    def test_quest_cancel(self):
        """クエストキャンセルの検証"""
        self.service.process_complete_quest("user1", 101)
        
        with common.get_db_cursor() as cur:
            hist = cur.execute("SELECT * FROM quest_history ORDER BY id DESC LIMIT 1").fetchone()
            hist_id = hist["id"]
        
        self.service.process_cancel_quest("user1", hist_id)
        
        with common.get_db_cursor() as cur:
            user = cur.execute("SELECT * FROM quest_users WHERE user_id='user1'").fetchone()
            self.assertEqual(user["exp"], 0)
            self.assertEqual(user["gold"], 100)
            hist_check = cur.execute("SELECT * FROM quest_history WHERE id=?", (hist_id,)).fetchone()
            self.assertIsNone(hist_check)

if __name__ == '__main__':
    unittest.main()