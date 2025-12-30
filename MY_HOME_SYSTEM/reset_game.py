import os
import sys
import datetime

# 共通モジュールを読み込めるようにパス設定
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import common

def reset_game_data():
    print("🧨 ゲームデータの完全リセットを開始します...")
    
    # ユーザーに確認
    confirm = input("全ての履歴、装備、レベル、所持金が消去されます。よろしいですか？ (y/N): ")
    if confirm.lower() != 'y':
        print("中止しました。")
        return

    try:
        with common.get_db_cursor(commit=True) as cur:
            # 1. 履歴テーブルの全削除
            print("🗑️  クエスト履歴を削除中...")
            cur.execute("DELETE FROM quest_history")
            
            print("🗑️  報酬履歴を削除中...")
            cur.execute("DELETE FROM reward_history")
            
            # 2. 装備所持テーブルの全削除
            print("🗑️  所有装備を削除中...")
            cur.execute("DELETE FROM user_equipments")
            
            # 3. ユーザーステータスの初期化
            print("✨ ユーザーを初期状態(Lv.1 / 0G)に戻しています...")
            cur.execute("""
                UPDATE quest_users 
                SET level = 1, 
                    exp = 0, 
                    gold = 0, 
                    updated_at = ?
            """, (datetime.datetime.now().isoformat(),))
            
        print("\n✅ リセット完了！ 全員「レベル1・所持金0」からスタートです。")
        print("   ブラウザをリロードして確認してください。")
        
    except Exception as e:
        print(f"\n❌ エラーが発生しました: {e}")

if __name__ == "__main__":
    reset_game_data()