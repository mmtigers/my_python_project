import sqlite3
import datetime
import config

def recover_mom_data():
    print("🔍 データベースの状態を確認します...")
    
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    try:
        # 現在のユーザー一覧を取得
        cur.execute("SELECT * FROM quest_users")
        users = cur.fetchall()
        
        print(f"現在の登録ユーザー数: {len(users)}人")
        existing_ids = []
        for u in users:
            # カラム名の確認も兼ねて取得
            uid = u['user_id'] if 'user_id' in u.keys() else '不明'
            print(f" - 名前: {u['name']} (ID: {uid})")
            existing_ids.append(uid)
            
        # 'mom' がいない場合に追加
        if 'mom' not in existing_ids:
            print("\n⚠️ 「はるな(mom)」が見つかりません。データを復旧します...")
            
            # quest_router.py の seed_data と同じ内容
            mom_data = ('mom', 'はるな', '魔法使い', 1, 0, 150, datetime.datetime.now())
            
            cur.execute("""
                INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, mom_data)
            
            conn.commit()
            print("✅ 復旧成功: 「はるな」をデータベースに追加しました！")
        else:
            print("\n✅ 「はるな」のデータは既に存在しています。")
            print("もし画面に表示されない場合は、ブラウザをリロードしてみてください。")
            
    except Exception as e:
        print(f"\n❌ エラーが発生しました: {e}")
        print("テーブル定義がまだ古い(idカラムのまま)可能性があります。")
        
    conn.close()

if __name__ == "__main__":
    recover_mom_data()