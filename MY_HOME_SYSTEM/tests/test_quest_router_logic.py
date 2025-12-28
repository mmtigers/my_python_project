import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# --- 【修正 1】 パス解決の変更 ---
# プロジェクトルート (MY_HOME_SYSTEM/) を sys.path の先頭に追加
# これにより、内部での 'import config', 'import common' が解決可能になる
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

# --- 【修正 2】 インポートパスの変更 ---
# プロジェクトルートがパスに入ったため、'MY_HOME_SYSTEM.' プレフィックスは不要
try:
    from routers import quest_router
    # configやcommonも必要に応じてモック化あるいはインポート確認が可能
    import common
    import config
except ImportError as e:
    print(f"Critical Import Error: {e}")
    print(f"Current sys.path: {sys.path}")
    raise e

class TestQuestRouterLogic(unittest.TestCase):
    
    @patch('routers.quest_router.sqlite3.connect') # パッチパスも修正
    def test_complete_quest_logic(self, mock_connect):
        """
        クエスト完了時のロジック検証
        """
        # Mockのセットアップ
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur
        
        # コンテキストマネージャ (with common.get_db_cursor) 対応
        # common.pyの実装に依存せずテストするため、connectをMock化しているが、
        # リファクタリング後は common.get_db_cursor を patch する必要があるかもしれない。
        # 現状(Step 1)は sqlite3.connect を直接使っているコードに対するテストなのでこれでOK。

        # DBからの戻り値を設定
        mock_cur.execute.return_value.fetchone.side_effect = [
            {'quest_id': 1, 'title': '掃除', 'exp_gain': 20, 'gold_gain': 10},
            {'user_id': 'dad', 'level': 1, 'exp': 0, 'gold': 0}
        ]

        # 実行
        action = quest_router.QuestAction(user_id='dad', quest_id=1)
        result = quest_router.complete_quest(action)

        # 検証
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['earnedGold'], 10)
        self.assertFalse(result['leveledUp'])
        
        # SQL実行の検証
        args_list = mock_cur.execute.call_args_list
        update_called = any("UPDATE quest_users" in str(call) for call in args_list)
        self.assertTrue(update_called, "UPDATE query should be executed")
        
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('routers.quest_router.sqlite3.connect')
    def test_get_all_data_structure(self, mock_connect):
        """データ取得APIがエラーなく構造を返すか検証"""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur
        
        mock_cur.execute.return_value.fetchall.return_value = [] 
        mock_cur.execute.return_value.__iter__.return_value = []

        result = quest_router.get_all_data()
        
        self.assertIn('users', result)
        self.assertIn('quests', result)
        self.assertIn('logs', result)
        
        mock_conn.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()