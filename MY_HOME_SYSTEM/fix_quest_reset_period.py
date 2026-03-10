import logging
from core.database import get_db_cursor

# ログ出力をコンソールで見えるように設定
logging.basicConfig(level=logging.INFO)

def fix_reset_period():
    try:
        # commit=True を指定することで、withブロック終了時に自動コミットされます
        with get_db_cursor(commit=True) as cursor:
            if cursor is None:
                print("❌ DBカーソルの取得に失敗しました。")
                return

            # 修正: テーブル名を `quest_master` に変更しました
            cursor.execute("""
                UPDATE quest_master
                SET reset_period = 'daily'
                WHERE reset_period = 'weekly_monday' 
                  AND quest_id NOT LIKE 'boss_%'
            """)
            
            # 変更された行数を取得
            print(f"✅ {cursor.rowcount} 件のクエストを 'daily' に修正しました。")
            
    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")

if __name__ == "__main__":
    fix_reset_period()