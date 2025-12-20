import unittest
from unittest.mock import MagicMock, patch
import handlers.ai_logic as ai_logic
import config

class TestAILogic(unittest.TestCase):

    def setUp(self):
        # APIキーがあるか確認 (なければテストスキップ)
        if not config.GEMINI_API_KEY:
            self.skipTest("GEMINI_API_KEYがないためスキップ")
        
        # DB保存をモック化（実際にDBには書かない）
        self.patcher = patch('common.save_log_generic')
        self.mock_save = self.patcher.start()
        
        # Discord通知もモック化
        self.patcher_push = patch('common.send_push')
        self.mock_push = self.patcher_push.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher_push.stop()

    def test_child_health(self):
        """子供の体調記録テスト"""
        print("\n🧪 Test: 子供の体調入力")
        msg = "たろうが38.5度の熱があるの。心配。"
        response = ai_logic.analyze_text_and_execute(msg, "dummy_user", "ママ")
        
        print(f"   Input: {msg}")
        print(f"   Response: {response}")
        
        # 検証
        self.assertIn("たろう", response)
        self.assertIn("記録しました", response)
        # save_log_genericが呼ばれたか
        self.mock_save.assert_called()
        args, _ = self.mock_save.call_args
        self.assertEqual(args[0], config.SQLITE_TABLE_CHILD) # テーブル名確認

    def test_shopping(self):
        """買い物記録テスト"""
        print("\n🧪 Test: 買い物入力")
        msg = "スーパーで食材を3000円分買ってきたよ"
        response = ai_logic.analyze_text_and_execute(msg, "dummy_user", "パパ")
        
        print(f"   Input: {msg}")
        print(f"   Response: {response}")
        
        self.assertIn("3000円", response)
        self.assertIn("家計簿", response)
        
        args, _ = self.mock_save.call_args
        self.assertEqual(args[0], config.SQLITE_TABLE_SHOPPING)

    def test_chat(self):
        """雑談テスト"""
        print("\n🧪 Test: 雑談")
        msg = "今日はいい天気だね"
        response = ai_logic.analyze_text_and_execute(msg, "dummy_user", "パパ")
        
        print(f"   Input: {msg}")
        print(f"   Response: {response}")
        
        # 雑談なのでDB保存は呼ばれないはず
        self.mock_save.assert_not_called()
        self.assertIsNotNone(response)

if __name__ == '__main__':
    unittest.main()