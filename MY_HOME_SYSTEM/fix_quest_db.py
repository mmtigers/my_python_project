import sqlite3
import config
import common
import init_unified_db  # ステップ1で修正した初期化スクリプトを読み込みます

def fix_quest_tables():
    print("🔧 クエスト機能のテーブル修復を開始します...")
    
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cur = conn.cursor()
    
    # 1. 削除対象のテーブルリスト
    # 古い定義(id)と新しい定義(user_id)が混在している可能性があるため、
    # 関連しそうなテーブルを一度すべて削除します。
    target_tables = [
        "quest_users",     # ★ここが諸悪の根源（定義不一致）
        "quest_tasks",     # 古い定義の残骸
        "quest_master",    # 新しい定義
        "quest_status",    # 古い定義
        "quest_history",   # 履歴（「誰か」になっているデータも消えます）
        "quest_rewards",   # 古い定義
        "reward_master",   # 新しい定義
        "reward_history"   # 履歴
    ]
    
    print("🗑️  古い/壊れたテーブルを削除中...")
    for table in target_tables:
        try:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            print(f"   - {table} を削除しました")
        except Exception as e:
            print(f"   - {table} の削除に失敗: {e}")
            
    conn.commit()
    conn.close()
    
    print("✅ 削除完了。")
    print("🔨 新しいテーブル定義で再作成します...")
    
    # 2. 修正済みの init_unified_db を呼び出して、正しいテーブルを作成させる
    try:
        init_unified_db.init_db()
        print("✅ テーブル再作成に成功しました！")
    except Exception as e:
        print(f"❌ 再作成エラー: {e}")
        return

    print("\n🎉 修復が完了しました！")
    print("サーバー(unified_server.py)を再起動してください。")
    print("起動時に自動的に初期データ(まさひろ、はるな等)が投入されます。")

if __name__ == "__main__":
    fix_quest_tables()